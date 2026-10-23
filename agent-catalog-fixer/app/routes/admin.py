"""Embedded admin: dashboard UI + JSON API."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from ..audit import grade
from ..billing import require_pro_plan
from ..config import Settings
from ..deps import get_current_shop, get_db, get_settings_dep, shopify_client_for
from ..config import get_settings
from ..jsonld import draft_faqs
from ..models import Plan, Shop
from ..services import BULK_FIXABLE, CatalogService

router = APIRouter()

_templates_dir = Path(__file__).parent.parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html"]),
)
_jinja.globals["shopify_api_key"] = get_settings().shopify_api_key


def _service(shop: Shop, db: Session, settings: Settings) -> CatalogService:
    return CatalogService(db, shopify_client_for(shop), settings)


# -- HTML dashboard -------------------------------------------------------

@router.get("/app", response_class=HTMLResponse)
def dashboard(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    audits = service.product_audits(shop)
    scored = len(audits)
    avg = round(sum(a.score for a in audits) / scored, 1) if scored else None
    issue_counts: dict[str, int] = {}
    for a in audits:
        for issue in a.issues:
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    rows = [
        {
            "product_id": a.product_id,
            "title": a.title,
            "score": a.score,
            "grade": grade(a.score),
            "issues": a.issues,
            "fixes_applied": a.fixes_applied,
            "issue_codes": ",".join(i["code"] for i in a.issues),
        }
        for a in audits
    ]
    html = _jinja.get_template("dashboard.html").render(
        shop=shop,
        rows=rows,
        scored=scored,
        avg_score=avg,
        grade=grade(avg) if avg is not None else "—",
        issue_counts=issue_counts,
        plan=shop.plan.value,
        product_limit=service.product_limit_for(shop),
        bulk_fixable=sorted(BULK_FIXABLE),
        request=request,
    )
    return HTMLResponse(html)


# -- JSON API --------------------------------------------------------------

@router.get("/api/summary")
def api_summary(
    response: Response,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    audits = service.product_audits(shop)
    return {
        "shop": shop.shop_domain,
        "plan": shop.plan.value,
        "products_scored": len(audits),
        "avg_score": round(sum(a.score for a in audits) / len(audits), 1) if audits else None,
        "product_limit": service.product_limit_for(shop),
    }


@router.post("/api/audit/run")
def api_run_audit(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    run = service.run_full_audit(shop)
    return {
        "run_id": run.id,
        "status": run.status,
        "products_scored": run.products_scored,
        "avg_score": run.avg_score,
    }


@router.get("/api/products")
def api_products(
    request: Request,
    response: Response,
    issue: str | None = None,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    audits = service.product_audits(shop, issue_code=issue)
    return [
        {
            "product_id": a.product_id,
            "title": a.title,
            "score": a.score,
            "grade": grade(a.score),
            "issues": a.issues,
            "fixes_applied": a.fixes_applied,
        }
        for a in audits
    ]


@router.get("/api/products/{product_id}")
def api_product_detail(
    product_id: str,
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    audits = [a for a in service.product_audits(shop) if a.product_id == str(product_id)]
    if not audits:
        raise HTTPException(404, "Product not audited yet.")
    a = audits[0]
    return {
        "product_id": a.product_id,
        "title": a.title,
        "score": a.score,
        "grade": grade(a.score),
        "issues": a.issues,
        "fixes_applied": a.fixes_applied,
        "snapshot": a.snapshot,
    }


@router.post("/api/products/{product_id}/fix")
def api_apply_fix(
    product_id: str,
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    service = _service(shop, db, settings)
    # fix_code passed as query param or JSON body.
    fix_code = request.query_params.get("fix_code", "")
    try:
        return service.apply_fix(shop, product_id, fix_code)
    except Exception:  # noqa: BLE001 - the UI parses JSON; never leak a bare 500
        import logging

        logging.getLogger(__name__).exception(
            "apply_fix failed for product %s", product_id
        )
        return {
            "applied": False,
            "detail": "Something went wrong applying that fix. Please try again.",
        }


@router.post("/api/bulk-fix")
async def api_bulk_fix(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    body = await request.json()
    fix_code = body.get("fix_code", "")
    product_ids = [str(p) for p in body.get("product_ids", [])]
    require_pro_plan(shop)
    service = _service(shop, db, settings)
    try:
        return service.bulk_fix(shop, fix_code, product_ids)
    except Exception:  # noqa: BLE001 - JSON for the UI, never a bare 500
        import logging

        logging.getLogger(__name__).exception("bulk_fix failed")
        return {
            "applied": False,
            "detail": "Something went wrong applying those fixes. Please try again.",
        }


@router.get("/api/products/{product_id}/faqs")
def api_draft_faqs(
    product_id: str,
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    client = shopify_client_for(shop)
    product = client.get_product(product_id)
    if not product:
        raise HTTPException(404, "Product not found.")
    return {"product_id": product_id, "faqs": draft_faqs(product)}


@router.get("/api/plan")
def api_plan(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
):
    return {
        "plan": shop.plan.value,
        "plans": {
            "free": {"price": 0, "product_limit": 25, "bulk_fix": False, "monitoring": False},
            "pro": {"price": 19, "product_limit": None, "bulk_fix": True, "monitoring": True},
        },
    }
