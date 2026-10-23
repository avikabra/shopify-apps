"""Tests for JSON-LD builders."""
import json

from app.jsonld import (
    build_faq_jsonld,
    build_policy_jsonld,
    build_product_jsonld,
    combined_jsonld,
    draft_faqs,
    render_script_tag,
)
from app.shopify.mock import fixture_products


def test_build_product_jsonld():
    p = fixture_products()[0]
    data = build_product_jsonld(p, shop_url="https://demo.myshopify.com")
    assert data["@type"] == "Product"
    assert data["name"] == "Organic Cotton T-Shirt"
    assert data["brand"]["name"] == "Evergreen Basics"
    assert data["category"] == "T-Shirts"
    assert len(data["offers"]) == 2
    assert data["offers"][0]["gtin"] == "012345678905"
    assert data["url"] == "https://demo.myshopify.com/products/organic-cotton-tee"


def test_build_product_jsonld_minimal():
    p = fixture_products()[1]
    data = build_product_jsonld(p)
    assert data["@type"] == "Product"
    assert "brand" not in data  # no vendor
    assert data["offers"][0]["availability"] == "https://schema.org/OutOfStock"


def test_build_faq_jsonld():
    faqs = [{"question": "Q?", "answer": "A."}, {"question": "", "answer": "skip"}]
    data = build_faq_jsonld(faqs)
    assert data["@type"] == "FAQPage"
    assert len(data["mainEntity"]) == 1
    assert data["mainEntity"][0]["name"] == "Q?"


def test_draft_faqs_multi_variant():
    faqs = draft_faqs(fixture_products()[2])
    questions = [f["question"] for f in faqs]
    assert any("available in" in q for q in questions)
    assert any("Who makes" in q for q in questions)


def test_draft_faqs_single_variant():
    faqs = draft_faqs(fixture_products()[1])
    assert len(faqs) >= 1


def test_build_policy_jsonld():
    data = build_policy_jsonld({"returns_policy": "30-day returns.", "return_window_days": 30})
    assert data["merchantReturnPolicy"]["merchantReturnDays"] == 30
    assert build_policy_jsonld({}) == {"@context": "https://schema.org"}


def test_render_script_tag():
    tag = render_script_tag({"@type": "Product"})
    assert tag.startswith('<script type="application/ld+json">')
    assert json.loads(tag.split("\n", 1)[1].rsplit("\n", 1)[0])["@type"] == "Product"


def test_combined_jsonld():
    p = fixture_products()[0]
    out = combined_jsonld(p, draft_faqs(p))
    assert "FAQPage" in out and '"@type": "Product"' in out
