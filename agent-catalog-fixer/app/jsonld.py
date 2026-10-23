"""schema.org JSON-LD builders.

Generates Product, FAQPage, and merchant-policy JSON-LD from product and shop
data the merchant already has. The app stores the payload in a product
metafield (namespace ``agent_fixer``, key ``seo_jsonld``); a theme app
extension (or manual snippet) renders it into the storefront <head>.
"""
from __future__ import annotations

import json

from .audit import strip_html


def _first_image_src(product: dict) -> str | None:
    images = product.get("images") or []
    if images:
        return images[0].get("src")
    return None


def build_product_jsonld(product: dict, shop_url: str = "") -> dict:
    """schema.org Product markup."""
    variants = product.get("variants") or []
    offers = []
    for v in variants:
        try:
            price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        offer: dict = {
            "@type": "Offer",
            "price": f"{price:.2f}",
            "priceCurrency": product.get("currency") or "USD",
            "availability": (
                "https://schema.org/InStock"
                if (v.get("inventory_quantity") or 0) > 0
                else "https://schema.org/OutOfStock"
            ),
        }
        barcode = (v.get("barcode") or "").strip()
        if barcode:
            offer["gtin"] = barcode
        sku = (v.get("sku") or "").strip()
        if sku:
            offer["sku"] = sku
        offers.append(offer)

    data: dict = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.get("title") or "",
        "description": strip_html(product.get("description_html") or "")[:5000],
    }
    vendor = (product.get("vendor") or "").strip()
    if vendor:
        data["brand"] = {"@type": "Brand", "name": vendor}
    ptype = (product.get("product_type") or "").strip()
    if ptype:
        data["category"] = ptype
    src = _first_image_src(product)
    if src:
        data["image"] = src
    if offers:
        data["offers"] = offers
    if shop_url and product.get("handle"):
        data["url"] = shop_url.rstrip("/") + "/products/" + product["handle"]
    return data


def build_faq_jsonld(faqs: list[dict]) -> dict:
    """schema.org FAQPage markup from [{question, answer}]."""
    entities = []
    for faq in faqs:
        q = (faq.get("question") or "").strip()
        a = (faq.get("answer") or "").strip()
        if not q or not a:
            continue
        entities.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": entities,
    }


def draft_faqs(product: dict) -> list[dict]:
    """Draft FAQs purely from existing product data (merchant approves)."""
    title = (product.get("title") or "this product").strip()
    faqs: list[dict] = []
    variants = product.get("variants") or []
    if len(variants) > 1:
        opts: dict[str, set[str]] = {}
        for v in variants:
            for opt in v.get("option_values") or []:
                name = (opt.get("name") or "").strip()
                value = (opt.get("value") or "").strip()
                if name and value:
                    opts.setdefault(name, set()).add(value)
        if opts:
            lines = "; ".join(
                f"{name}: {', '.join(sorted(vals))}" for name, vals in sorted(opts.items())
            )
            faqs.append({
                "question": f"What options is {title} available in?",
                "answer": f"{title} is available in {lines}.",
            })
    vendor = (product.get("vendor") or "").strip()
    if vendor:
        faqs.append({
            "question": f"Who makes {title}?",
            "answer": f"{title} is made by {vendor}.",
        })
    in_stock = any((v.get("inventory_quantity") or 0) > 0 for v in variants)
    faqs.append({
        "question": f"Is {title} in stock?",
        "answer": (
            f"Yes, {title} is currently in stock."
            if in_stock
            else f"{title} is currently out of stock. Check back soon."
        ),
    })
    return faqs


def build_policy_jsonld(shop_settings: dict) -> dict:
    """MerchantReturnPolicy + shipping hints from shop settings."""
    policies: dict = {"@context": "https://schema.org"}
    returns = (shop_settings.get("returns_policy") or "").strip()
    if returns:
        policies["merchantReturnPolicy"] = {
            "@type": "MerchantReturnPolicy",
            "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
            "merchantReturnDays": shop_settings.get("return_window_days", 30),
            "returnMethod": "https://schema.org/ReturnByMail",
            "returnFees": "https://schema.org/FreeReturn",
        }
        # Keep the merchant's own words discoverable too.
        policies["merchantReturnPolicy"]["description"] = returns[:1000]
    shipping = (shop_settings.get("shipping_policy") or "").strip()
    if shipping:
        policies["shippingDetails"] = {
            "@type": "OfferShippingDetails",
            "description": shipping[:1000],
        }
    return policies


def render_script_tag(payload: dict) -> str:
    """Render a JSON-LD payload as an HTML <script> tag."""
    return '<script type="application/ld+json">\n' + json.dumps(payload, indent=2) + "\n</script>"


def combined_jsonld(product: dict, faqs: list[dict], shop_url: str = "") -> str:
    """Full JSON-LD document: Product + FAQPage in one script tag."""
    graph = [build_product_jsonld(product, shop_url)]
    if faqs:
        graph.append(build_faq_jsonld(faqs))
    return render_script_tag({"@context": "https://schema.org", "@graph": graph})
