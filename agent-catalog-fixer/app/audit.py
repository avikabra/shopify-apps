"""Product audit engine: scores every product 0-100 on agent-readiness.

An "agent-ready" product is one an AI shopping agent (ChatGPT, Perplexity,
Google AI Mode) can confidently recommend and buy: it has a verifiable
identifier (GTIN/barcode), a rich description, structured data (FAQ/schema),
accessible images (alt text), complete variants, and the fields Shopify
Catalog requires for syndication.

The engine is pure and deterministic: it takes a normalized product dict and
returns a score plus a list of issues. No network, no LLM calls.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Scoring weights (sum to 100)
# ---------------------------------------------------------------------------
W_IDENTIFIERS = 20
W_DESCRIPTION = 15
W_STRUCTURED_DATA = 15
W_IMAGE_ALT = 15
W_VARIANT_COMPLETENESS = 15
W_CATALOG_ELIGIBILITY = 20

MIN_DESCRIPTION_WORDS = 80
PARTIAL_DESCRIPTION_WORDS = 40

_FAQ_NAMESPACE = "agent_fixer"
_FAQ_KEY = "faqs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    return re.sub(r"\s+", " ", parser.text()).strip()


def word_count(html: str) -> int:
    text = strip_html(html)
    if not text:
        return 0
    return len(text.split())


def gtin_check_digit_ok(barcode: str) -> bool:
    """Validate GTIN-8/12/13/14 check digit (GS1 algorithm)."""
    digits = re.sub(r"\D", "", barcode or "")
    if len(digits) not in (8, 12, 13, 14):
        return False
    check = int(digits[-1])
    total = 0
    # From the rightmost digit (excluding check), weight 3,1,3,1...
    for i, ch in enumerate(reversed(digits[:-1])):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


def _metafield_values(product: dict) -> list[dict]:
    return product.get("metafields") or []


def has_faq_content(product: dict) -> bool:
    """True if the product carries FAQ structured data (our metafield) or
    FAQ-like content in the description."""
    for mf in _metafield_values(product):
        if mf.get("namespace") == _FAQ_NAMESPACE and mf.get("key") == _FAQ_KEY:
            if (mf.get("value") or "").strip():
                return True
    text = strip_html(product.get("description_html") or "").lower()
    # Heuristic: description contains Q&A markers.
    return bool(re.search(r"\b(q:|question:|faq\b)", text))


def has_custom_metafields(product: dict) -> bool:
    for mf in _metafield_values(product):
        if mf.get("namespace") not in ("", None):
            return True
    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_identifiers(product: dict) -> tuple[int, list[dict]]:
    """GTIN/barcode presence and validity across variants (0-20)."""
    variants = product.get("variants") or []
    issues: list[dict] = []
    if not variants:
        issues.append({
            "code": "no_variants",
            "severity": "high",
            "message": "Product has no variants to identify.",
            "fix": "Add at least one variant before selling.",
            "auto_fixable": False,
        })
        return 0, issues
    valid = sum(1 for v in variants if gtin_check_digit_ok(v.get("barcode") or ""))
    present_invalid = sum(
        1 for v in variants
        if (v.get("barcode") or "").strip() and not gtin_check_digit_ok(v.get("barcode") or "")
    )
    missing = len(variants) - valid - present_invalid
    score = round(W_IDENTIFIERS * valid / len(variants))
    if missing:
        issues.append({
            "code": "missing_barcode",
            "severity": "high",
            "message": f"{missing} of {len(variants)} variant(s) have no barcode/GTIN. "
                       "AI agents skip products they cannot verify.",
            "fix": "Auto-generate placeholder GTIN-12 barcodes, then replace with your GS1-issued GTINs.",
            "auto_fixable": True,
        })
    if present_invalid:
        issues.append({
            "code": "invalid_barcode",
            "severity": "high",
            "message": f"{present_invalid} variant(s) have a barcode with a bad check digit.",
            "fix": "Regenerate valid barcodes for the affected variants.",
            "auto_fixable": True,
        })
    return score, issues


def score_description(product: dict) -> tuple[int, list[dict]]:
    """Description length and richness (0-15)."""
    words = word_count(product.get("description_html") or "")
    issues: list[dict] = []
    if words >= MIN_DESCRIPTION_WORDS:
        return W_DESCRIPTION, issues
    if words >= PARTIAL_DESCRIPTION_WORDS:
        score = 8
    elif words > 0:
        score = 3
    else:
        score = 0
    issues.append({
        "code": "short_description",
        "severity": "high" if words < PARTIAL_DESCRIPTION_WORDS else "medium",
        "message": f"Description is {words} words; AI agents need at least "
                   f"{MIN_DESCRIPTION_WORDS} words of attribute-rich copy to recommend a product.",
        "fix": "Expand the description from existing product data (materials, fit, variants, use cases).",
        "auto_fixable": True,
    })
    return score, issues


def score_structured_data(product: dict) -> tuple[int, list[dict]]:
    """FAQ / structured data presence (0-15)."""
    issues: list[dict] = []
    if has_faq_content(product):
        return W_STRUCTURED_DATA, issues
    if has_custom_metafields(product):
        issues.append({
            "code": "no_faq",
            "severity": "medium",
            "message": "No FAQ structured data. FAQPage schema is the highest-ROI signal for AI citations.",
            "fix": "Generate FAQs from the product data and publish FAQPage JSON-LD.",
            "auto_fixable": True,
        })
        return 8, issues
    issues.append({
        "code": "no_faq",
        "severity": "medium",
        "message": "No structured data at all. Agents rely on schema.org markup to verify details.",
        "fix": "Generate Product + FAQPage JSON-LD from the product data.",
        "auto_fixable": True,
    })
    return 0, issues


def score_image_alt(product: dict) -> tuple[int, list[dict]]:
    """Image alt-text coverage (0-15)."""
    images = product.get("images") or []
    issues: list[dict] = []
    if not images:
        issues.append({
            "code": "no_images",
            "severity": "medium",
            "message": "Product has no images. Agents deprioritize imageless products.",
            "fix": "Upload at least one product image.",
            "auto_fixable": False,
        })
        return 5, issues
    with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    score = round(W_IMAGE_ALT * with_alt / len(images))
    if with_alt < len(images):
        issues.append({
            "code": "missing_alt_text",
            "severity": "medium",
            "message": f"{len(images) - with_alt} of {len(images)} image(s) lack alt text.",
            "fix": "Fill alt text from the product title.",
            "auto_fixable": True,
        })
    return score, issues


def _variant_complete(variant: dict) -> bool:
    try:
        price = float(variant.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    return price > 0 and bool((variant.get("title") or "").strip())


def score_variant_completeness(product: dict) -> tuple[int, list[dict]]:
    """Every variant needs a price and a distinguishable title (0-15)."""
    variants = product.get("variants") or []
    issues: list[dict] = []
    if not variants:
        return 0, issues
    complete = sum(1 for v in variants if _variant_complete(v))
    score = round(W_VARIANT_COMPLETENESS * complete / len(variants))
    if complete < len(variants):
        issues.append({
            "code": "incomplete_variant",
            "severity": "medium",
            "message": f"{len(variants) - complete} variant(s) are missing a price or title.",
            "fix": "Set a price and descriptive title on every variant.",
            "auto_fixable": False,
        })
    return score, issues


def score_catalog_eligibility(product: dict) -> tuple[int, list[dict]]:
    """Fields Shopify Catalog / agent storefronts require (0-20)."""
    issues: list[dict] = []
    score = 0
    if (product.get("vendor") or "").strip():
        score += 5
    else:
        issues.append({
            "code": "missing_brand",
            "severity": "high",
            "message": "No brand/vendor. Catalog rejects products without a brand.",
            "fix": "Set the vendor (brand) on the product.",
            "auto_fixable": False,
        })
    if (product.get("product_type") or "").strip():
        score += 5
    else:
        issues.append({
            "code": "missing_product_type",
            "severity": "medium",
            "message": "No product type. Agents use it to match category queries.",
            "fix": "Set a product type (e.g. 'T-Shirts').",
            "auto_fixable": False,
        })
    if product.get("status") == "active" and product.get("published", True):
        score += 5
    else:
        issues.append({
            "code": "unpublished",
            "severity": "high",
            "message": "Product is not active/published, so agents cannot see it.",
            "fix": "Set status to Active and publish to sales channels.",
            "auto_fixable": False,
        })
    tags = product.get("tags") or []
    if tags:
        score += 5
    else:
        issues.append({
            "code": "no_tags",
            "severity": "low",
            "message": "No tags. Tags help agents match long-tail queries.",
            "fix": "Add descriptive tags (material, color, use case).",
            "auto_fixable": False,
        })
    return score, issues


def audit_product(product: dict) -> dict:
    """Score one product. Returns {score, issues, factors}."""
    factors: dict[str, int] = {}
    issues: list[dict] = []
    for name, fn in (
        ("identifiers", score_identifiers),
        ("description", score_description),
        ("structured_data", score_structured_data),
        ("image_alt", score_image_alt),
        ("variant_completeness", score_variant_completeness),
        ("catalog_eligibility", score_catalog_eligibility),
    ):
        pts, found = fn(product)
        factors[name] = pts
        issues.extend(found)
    total = sum(factors.values())
    return {"score": total, "issues": issues, "factors": factors}


def grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"
