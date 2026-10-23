"""Tests for auto-fix generators."""
import random

from app.audit import gtin_check_digit_ok, word_count
from app.fixes import (
    alt_text_for_image,
    expand_description,
    generate_gtin12,
    gtin12_check_digit,
    is_placeholder_gtin,
    suggest_tags,
)
from app.shopify.mock import fixture_products


def test_gtin12_check_digit_known():
    # Known UPC-A: 03600029145 -> check digit 2
    assert gtin12_check_digit("03600029145") == "2"


def test_generate_gtin12_valid():
    rng = random.Random(42)
    for _ in range(20):
        code = generate_gtin12(rng)
        assert len(code) == 12
        assert code.isdigit()
        assert gtin_check_digit_ok(code)


def test_generate_gtin12_unique():
    rng = random.Random(1)
    codes = {generate_gtin12(rng) for _ in range(50)}
    assert len(codes) == 50  # no collisions in 50 draws


def test_is_placeholder_gtin():
    assert is_placeholder_gtin("049000123456")
    assert not is_placeholder_gtin("036000291452")


def test_expand_description_keeps_original():
    p = fixture_products()[1]  # bad: "Buy now."
    expanded = expand_description(p)
    assert "Buy now." in expanded
    assert "Product details" in expanded


def test_expand_description_reaches_word_bar():
    # A data-rich product's expansion clears the 80-word bar.
    p = fixture_products()[0]
    expanded = expand_description(p)
    assert word_count(expanded) >= 80


def test_expand_description_improves_thin_product():
    # A thin product can't honestly reach 80 words from 2 words of input,
    # but the expansion must preserve the original and add real details.
    p = fixture_products()[1]
    before = word_count(p["description_html"])
    expanded = expand_description(p)
    assert word_count(expanded) > before * 3
    assert "Priced at $9.99." in expanded


def test_expand_description_uses_variant_options():
    p = fixture_products()[2]  # middling: Size US 9 / US 10
    expanded = expand_description(p)
    assert "US 9" in expanded and "US 10" in expanded


def test_expand_description_empty_original():
    p = dict(fixture_products()[1], description_html="")
    expanded = expand_description(p)
    # Still generates a useful section from title/price alone.
    assert "Product details" in expanded
    assert word_count(expanded) >= 15


def test_alt_text_for_image():
    p = {"title": "Trail Running Shoes"}
    assert alt_text_for_image(p, {"alt": ""}, 0) == "Trail Running Shoes"
    assert alt_text_for_image(p, {"alt": ""}, 1) == "Trail Running Shoes - view 2"
    assert alt_text_for_image(p, {"variant_title": "US 9"}, 0) == "Trail Running Shoes - US 9"
    # "Default Title" is noise; ignore it
    assert alt_text_for_image(p, {"variant_title": "Default Title"}, 0) == "Trail Running Shoes"


def test_suggest_tags():
    p = fixture_products()[1]  # no tags, no type, no vendor
    assert suggest_tags(p) == []
    p2 = dict(fixture_products()[2])
    tags = suggest_tags(p2)
    # product_type/vendor already partially covered; option values suggested
    assert "us 9" in tags or "us 10" in tags
