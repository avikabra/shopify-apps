"""CatalogService: orchestrates audits, fixes, and monitoring."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import security, tokens
from .audit import audit_product
from .config import Settings
from .fixes import (
    alt_text_for_image,
    expand_description,
    generate_gtin12,
    is_placeholder_gtin,
)
from .jsonld import build_faq_jsonld, combined_jsonld, draft_faqs
from .models import AuditRun, FaqEntry, Plan, ProductAudit, Shop
from .shopify import ShopifyClient


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Fix codes the API accepts -> whether they are bulk-eligible.
BULK_FIXABLE = {"missing_barcode", "short_description", "missing_alt_text", "no_faq"}


class CatalogService:
    def __init__(self, db: Session, shopify: ShopifyClient, settings: Settings):
        self.db = db
        self.shopify = shopify
        self.settings = settings

    # -- shops -------------------------------------------------------------
    def get_shop(self, shop_domain: str) -> Shop | None:
        return (
            self.db.query(Shop)
            .filter_by(shop_domain=shop_domain)
            .one_or_none()
        )

    # -- audits ------------------------------------------------------------
    def score_and_store(self, shop: Shop, product: dict) -> ProductAudit:
        result = audit_product(product)
        existing = (
            self.db.query(ProductAudit)
            .filter_by(shop_id=shop.id, product_id=str(product["id"]))
            .one_or_none()
        )
        payload = {
            "shop_id": shop.id,
            "product_id": str(product["id"]),
            "product_gid": product.get("gid", ""),
            "title": product.get("title", ""),
            "score": result["score"],
            "issues": result["issues"],
            "snapshot": _snapshot(product),
            "updated_at": _utcnow(),
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
            audit = existing
        else:
            audit = ProductAudit(**payload, fixes_applied=0)
            self.db.add(audit)
        self.db.commit()
        self.db.refresh(audit)
        return audit

    def run_full_audit(self, shop: Shop, limit: int | None = None) -> AuditRun:
        """Score the catalog (paginated). Free plan is capped by settings."""
        cap = self.settings.free_plan_product_limit
        if shop.plan == Plan.FREE:
            limit = cap if limit is None else min(limit, cap)
        run = AuditRun(shop_id=shop.id, status="running")
        self.db.add(run)
        self.db.commit()

        scored = 0
        total = 0
        after = None
        try:
            while True:
                products, page = self.shopify.list_products(first=50, after=after)
                for p in products:
                    if limit is not None and scored >= limit:
                        break
                    self.score_and_store(shop, p)
                    scored += 1
                    total += audit_product(p)["score"]
                if limit is not None and scored >= limit:
                    break
                if not page.get("has_next_page"):
                    break
                after = page.get("end_cursor")
        except Exception:
            run.status = "failed"
            run.finished_at = _utcnow()
            self.db.commit()
            raise
        run.status = "done"
        run.finished_at = _utcnow()
        run.products_scored = scored
        run.avg_score = round(total / scored, 1) if scored else None
        self.db.commit()
        self.db.refresh(run)
        return run

    def rescore_product(self, shop: Shop, product_id: str) -> ProductAudit | None:
        """Re-score a single product (used by the products/update webhook)."""
        product = self.shopify.get_product(product_id)
        if not product:
            return None
        return self.score_and_store(shop, product)

    def product_audits(self, shop: Shop, issue_code: str | None = None) -> list[ProductAudit]:
        q = self.db.query(ProductAudit).filter_by(shop_id=shop.id).order_by(ProductAudit.score)
        audits = q.all()
        if issue_code:
            audits = [a for a in audits if any(i.get("code") == issue_code for i in a.issues)]
        return audits

    # -- fixes -------------------------------------------------------------
    def apply_fix(self, shop: Shop, product_id: str, fix_code: str) -> dict:
        """Apply one auto-fix to one product. Returns {applied, detail}."""
        try:
            product = self.shopify.get_product(product_id)
        except Exception:  # noqa: BLE001 - token/API failures become a clean detail
            return {
                "applied": False,
                "detail": "We couldn't reach your store to apply the fix. "
                "Please try again in a moment.",
            }
        if not product:
            return {"applied": False, "detail": "Product not found."}
        handler = {
            "missing_barcode": self._fix_barcodes,
            "invalid_barcode": self._fix_barcodes,
            "short_description": self._fix_description,
            "missing_alt_text": self._fix_alt_text,
            "no_faq": self._fix_faq,
        }.get(fix_code)
        if not handler:
            return {"applied": False, "detail": f"Fix '{fix_code}' is not auto-fixable."}
        ok, detail = handler(shop, product)
        if ok:
            self._bump_fixes(shop, product_id)
            self.rescore_product(shop, product_id)
        return {"applied": ok, "detail": detail}

    def bulk_fix(self, shop: Shop, fix_code: str, product_ids: list[str]) -> dict:
        """Apply one fix across many products. Pro plan only."""
        if shop.plan != Plan.PRO:
            return {"applied": False, "detail": "Bulk auto-fix requires the Pro plan."}
        if fix_code not in BULK_FIXABLE:
            return {"applied": False, "detail": f"Fix '{fix_code}' cannot be bulk-applied."}
        results = {"applied": 0, "failed": 0, "errors": []}
        for pid in product_ids:
            try:
                r = self.apply_fix(shop, pid, fix_code)
                if r["applied"]:
                    results["applied"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"{pid}: {r['detail']}")
            except Exception as exc:  # noqa: BLE001 - report per-product, keep going
                results["failed"] += 1
                results["errors"].append(f"{pid}: {exc}")
        return results

    def _bump_fixes(self, shop: Shop, product_id: str) -> None:
        audit = (
            self.db.query(ProductAudit)
            .filter_by(shop_id=shop.id, product_id=str(product_id))
            .one_or_none()
        )
        if audit:
            audit.fixes_applied = (audit.fixes_applied or 0) + 1
            self.db.commit()

    def _fix_barcodes(self, shop: Shop, product: dict) -> tuple[bool, str]:
        from .audit import gtin_check_digit_ok

        fixed = 0
        for v in product.get("variants") or []:
            barcode = (v.get("barcode") or "").strip()
            if not barcode or not gtin_check_digit_ok(barcode):
                new_code = generate_gtin12()
                if self.shopify.update_variant_barcode(v["id"], new_code):
                    fixed += 1
        if not fixed:
            return False, "No variants needed a barcode."
        return True, (
            f"Assigned placeholder GTIN-12 to {fixed} variant(s). "
            "Replace with your GS1-issued GTINs before publishing to sales channels."
        )

    def _fix_description(self, shop: Shop, product: dict) -> tuple[bool, str]:
        expanded = expand_description(product)
        if self.shopify.update_product_description(str(product["id"]), expanded):
            return True, "Description expanded with attribute-rich product details."
        return False, "Shopify rejected the description update."

    def _fix_alt_text(self, shop: Shop, product: dict) -> tuple[bool, str]:
        fixed = 0
        for i, img in enumerate(product.get("images") or []):
            if not (img.get("alt") or "").strip():
                alt = alt_text_for_image(product, img, i)
                if self.shopify.update_image_alt(img["id"], str(product["id"]), alt):
                    fixed += 1
        if not fixed:
            return False, "No images needed alt text."
        return True, f"Filled alt text on {fixed} image(s) from the product title."

    def _fix_faq(self, shop: Shop, product: dict) -> tuple[bool, str]:
        faqs = draft_faqs(product)
        if not faqs:
            return False, "Could not draft FAQs for this product."
        payload = combined_jsonld(product, faqs, shop_url=f"https://{shop.shop_domain}")
        ok = self.shopify.set_product_metafields(str(product["id"]), [
            {"namespace": "agent_fixer", "key": "faqs",
             "value": json.dumps(faqs), "type": "json"},
            {"namespace": "agent_fixer", "key": "seo_jsonld",
             "value": payload, "type": "multi_line_text_field"},
        ])
        if not ok:
            return False, "Shopify rejected the metafield update."
        for faq in faqs:
            self.db.add(FaqEntry(
                shop_id=shop.id, product_id=str(product["id"]),
                question=faq["question"], answer=faq["answer"],
            ))
        self.db.commit()
        return True, (
            f"Drafted {len(faqs)} FAQs and stored Product + FAQPage JSON-LD in "
            "product metafields (agent_fixer.seo_jsonld). Add the snippet to your "
            "theme <head> or use a theme app extension to render it."
        )

    # -- plan helpers ------------------------------------------------------
    def product_limit_for(self, shop: Shop) -> int | None:
        if shop.plan == Plan.PRO:
            return None
        return self.settings.free_plan_product_limit


def _snapshot(product: dict) -> dict:
    """Small serializable summary stored with each audit."""
    return {
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "variant_count": len(product.get("variants") or []),
        "image_count": len(product.get("images") or []),
    }


def access_token_for(shop: Shop, settings: Settings) -> str:
    return tokens.fresh_access_token(shop, settings)
