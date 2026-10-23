"""Tests for the audit scoring engine."""
import pytest

from app.audit import (
    audit_product,
    grade,
    gtin_check_digit_ok,
    has_faq_content,
    score_catalog_eligibility,
    score_description,
    score_identifiers,
    score_image_alt,
    score_structured_data,
    score_variant_completeness,
    strip_html,
    word_count,
)
from app.shopify.mock import fixture_products


@pytest.fixture()
def good():
    return fixture_products()[0]


@pytest.fixture()
def bad():
    return fixture_products()[1]


@pytest.fixture()
def middling():
    return fixture_products()[2]


# -- helpers ---------------------------------------------------------------

def test_strip_html():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html("") == ""


def test_word_count():
    assert word_count("<p>one two three</p>") == 3
    assert word_count("") == 0


def test_gtin_check_digit_ok():
    assert gtin_check_digit_ok("012345678905")  # valid UPC-A
    assert gtin_check_digit_ok("036000291452")  # valid UPC-A
    assert not gtin_check_digit_ok("012345678906")  # bad check digit
    assert not gtin_check_digit_ok("12345")  # wrong length
    assert not gtin_check_digit_ok("")  # empty


def test_grade():
    assert grade(100) == "A" and grade(80) == "A"
    assert grade(79) == "B" and grade(60) == "B"
    assert grade(59) == "C" and grade(40) == "C"
    assert grade(39) == "D" and grade(20) == "D"
    assert grade(19) == "F" and grade(0) == "F"


# -- identifiers ------------------------------------------------------------

def test_identifiers_full(good):
    score, issues = score_identifiers(good)
    assert score == 20
    assert issues == []


def test_identifiers_none(bad):
    score, issues = score_identifiers(bad)
    assert score == 0
    assert any(i["code"] == "missing_barcode" for i in issues)
    assert any(i["auto_fixable"] for i in issues)


def test_identifiers_partial(middling):
    score, issues = score_identifiers(middling)
    assert score == 10  # 1 of 2 valid
    assert any(i["code"] == "missing_barcode" for i in issues)


def test_identifiers_invalid_barcode(bad):
    p = dict(bad)
    p["variants"] = [dict(v, barcode="123456789013") for v in p["variants"]]
    score, issues = score_identifiers(p)
    assert score == 0
    assert any(i["code"] == "invalid_barcode" for i in issues)


# -- description --------------------------------------------------------------

def test_description_full(good):
    score, issues = score_description(good)
    assert score == 15
    assert issues == []


def test_description_empty(bad):
    p = dict(bad, description_html="")
    score, issues = score_description(p)
    assert score == 0
    assert any(i["code"] == "short_description" for i in issues)


def test_description_partial_credit():
    p = {"description_html": "<p>" + "word " * 50 + "</p>"}
    score, _ = score_description(p)
    assert score == 8


# -- structured data ----------------------------------------------------------

def test_structured_data_faq(good):
    score, issues = score_structured_data(good)
    assert score == 15
    assert issues == []


def test_structured_data_none(bad):
    score, issues = score_structured_data(bad)
    assert score == 0
    assert any(i["code"] == "no_faq" for i in issues)


def test_has_faq_content_from_description():
    p = {"description_html": "<p>FAQ: how does this work?</p>", "metafields": []}
    assert has_faq_content(p)


# -- image alt -----------------------------------------------------------------

def test_image_alt_full(good):
    score, issues = score_image_alt(good)
    assert score == 15
    assert issues == []


def test_image_alt_none(bad):
    score, issues = score_image_alt(bad)
    assert score == 0
    assert any(i["code"] == "missing_alt_text" for i in issues)


def test_image_alt_no_images():
    score, issues = score_image_alt({"images": []})
    assert score == 5
    assert any(i["code"] == "no_images" for i in issues)


# -- variants -------------------------------------------------------------------

def test_variant_completeness_full(good):
    score, issues = score_variant_completeness(good)
    assert score == 15
    assert issues == []


def test_variant_completeness_incomplete():
    p = {"variants": [{"title": "", "price": "0"}]}
    score, issues = score_variant_completeness(p)
    assert score == 0
    assert any(i["code"] == "incomplete_variant" for i in issues)


# -- catalog eligibility ---------------------------------------------------------

def test_catalog_eligibility_full(good):
    score, issues = score_catalog_eligibility(good)
    assert score == 20
    assert issues == []


def test_catalog_eligibility_missing_brand(bad):
    score, issues = score_catalog_eligibility(bad)
    # active (+5), nothing else
    assert score == 5
    assert any(i["code"] == "missing_brand" for i in issues)
    assert any(i["code"] == "missing_product_type" for i in issues)


# -- full audit -------------------------------------------------------------------

def test_audit_good_scores_high(good):
    result = audit_product(good)
    assert result["score"] >= 80
    assert grade(result["score"]) == "A"
    assert result["issues"] == []
    assert set(result["factors"]) == {
        "identifiers", "description", "structured_data",
        "image_alt", "variant_completeness", "catalog_eligibility",
    }


def test_audit_bad_scores_low(bad):
    result = audit_product(bad)
    assert result["score"] < 40
    assert len(result["issues"]) >= 5


def test_audit_middling_in_middle(middling):
    result = audit_product(middling)
    assert 40 <= result["score"] < 80
