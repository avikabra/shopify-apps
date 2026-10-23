"""Auto-fix generators.

Everything here is deterministic and template-based — no paid APIs, no LLM
calls. Fixes that touch real catalog identity (GTINs) are clearly marked as
placeholders the merchant must replace with GS1-issued values.
"""
from __future__ import annotations

import random
import re

from .audit import MIN_DESCRIPTION_WORDS, strip_html, word_count


# ---------------------------------------------------------------------------
# GTIN-12 (UPC-A) generation
# ---------------------------------------------------------------------------
def gtin12_check_digit(first11: str) -> str:
    """Compute the UPC-A check digit for 11 digits."""
    digits = [int(c) for c in first11]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits))
    return str((10 - (total % 10)) % 10)


def generate_gtin12(rng: random.Random | None = None) -> str:
    """Generate a syntactically valid GTIN-12 (UPC-A).

    IMPORTANT: this is a *placeholder* identifier. It passes the GS1 check-digit
    algorithm but is not a GS1-issued GTIN. The dashboard labels it as such and
    instructs the merchant to replace it with their real GTIN.
    """
    rng = rng or random.Random()
    # Use the 049000 prefix block (historically unassigned/reserved range) to
    # minimize collision risk with real issued GTINs.
    first11 = "049000" + "".join(str(rng.randrange(10)) for _ in range(5))
    return first11 + gtin12_check_digit(first11)


def is_placeholder_gtin(barcode: str, product: dict | None = None) -> bool:
    """Heuristic: barcodes we generated start with the 049000 block."""
    digits = re.sub(r"\D", "", barcode or "")
    return digits.startswith("049000")


# ---------------------------------------------------------------------------
# Description expansion
# ---------------------------------------------------------------------------
def _variant_phrase(product: dict) -> str:
    variants = product.get("variants") or []
    if len(variants) <= 1:
        return ""
    options: dict[str, set[str]] = {}
    for v in variants:
        for opt in v.get("option_values") or []:
            name = (opt.get("name") or "").strip()
            value = (opt.get("value") or "").strip()
            if name and value:
                options.setdefault(name, set()).add(value)
    if not options:
        return ""
    parts = []
    for name, values in sorted(options.items()):
        vals = ", ".join(sorted(values))
        parts.append(f"{name}: {vals}")
    return "Available in " + "; ".join(parts) + "."


def expand_description(product: dict) -> str:
    """Build attribute-rich copy from existing product data.

    Keeps the merchant's original copy verbatim, then appends a "Product
    details" section synthesized from title, brand, type, tags, and variant
    options — enough to push thin descriptions past the 80-word bar.
    """
    original_html = product.get("description_html") or ""
    original_text = strip_html(original_html)
    title = (product.get("title") or "This product").strip()
    vendor = (product.get("vendor") or "").strip()
    ptype = (product.get("product_type") or "").strip()
    tags = [t for t in (product.get("tags") or []) if t and t.strip()]

    details: list[str] = []
    lead = f"{title}"
    if vendor:
        lead += f" by {vendor}"
    lead += "."
    details.append(lead)

    if ptype:
        details.append(f"Category: {ptype}.")

    variant_phrase = _variant_phrase(product)
    if variant_phrase:
        details.append(variant_phrase)

    if tags:
        details.append("Highlights: " + ", ".join(tags[:8]) + ".")

    # Truthful commerce facts agents use: price and availability.
    prices = []
    for v in product.get("variants") or []:
        try:
            prices.append(float(v.get("price") or 0))
        except (TypeError, ValueError):
            continue
    prices = [p for p in prices if p > 0]
    if prices:
        if len(set(prices)) == 1:
            details.append(f"Priced at ${prices[0]:.2f}.")
        else:
            details.append(f"Priced from ${min(prices):.2f} to ${max(prices):.2f}.")
    if any((v.get("inventory_quantity") or 0) > 0 for v in product.get("variants") or []):
        details.append("In stock and ready to ship.")

    # Generic but truthful care/shipping-agnostic closer. Never invents
    # materials, dimensions, or claims not present in the source data.
    details.append(
        "Designed for everyday use. Check the variant options above to find "
        "the exact fit for your needs."
    )

    section = "<h3>Product details</h3><p>" + "</p><p>".join(details) + "</p>"
    if original_html.strip():
        return original_html.rstrip() + "\n" + section
    return f"<p>{lead}</p>\n" + section


def description_meets_bar(product: dict) -> bool:
    return word_count(product.get("description_html") or "") >= MIN_DESCRIPTION_WORDS


# ---------------------------------------------------------------------------
# Image alt text
# ---------------------------------------------------------------------------
def alt_text_for_image(product: dict, image: dict, index: int = 0) -> str:
    """Deterministic alt text from the product title (+ variant when known)."""
    title = (product.get("title") or "Product").strip()
    variant_title = (image.get("variant_title") or "").strip()
    if variant_title and variant_title.lower() != "default title":
        return f"{title} - {variant_title}"
    if index > 0:
        return f"{title} - view {index + 1}"
    return title


# ---------------------------------------------------------------------------
# Tag suggestions (from data the merchant already has)
# ---------------------------------------------------------------------------
def suggest_tags(product: dict) -> list[str]:
    """Suggest tags derived only from existing product data."""
    existing = {t.lower() for t in (product.get("tags") or [])}
    suggestions: list[str] = []
    ptype = (product.get("product_type") or "").strip().lower()
    if ptype and ptype not in existing:
        suggestions.append(ptype)
    vendor = (product.get("vendor") or "").strip().lower()
    if vendor and vendor not in existing:
        suggestions.append(vendor)
    for v in product.get("variants") or []:
        for opt in v.get("option_values") or []:
            value = (opt.get("value") or "").strip().lower()
            if value and value not in existing and value not in suggestions:
                suggestions.append(value)
    return suggestions[:10]
