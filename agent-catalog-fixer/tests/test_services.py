"""Tests for the service layer: audits, fixes, plan gating."""
import pytest

from app.models import AuditRun, Plan, ProductAudit
from app.services import BULK_FIXABLE, CatalogService


def test_run_full_audit(service, shop, db):
    run = service.run_full_audit(shop)
    assert run.status == "done"
    assert run.products_scored == 3
    assert run.avg_score is not None
    audits = db.query(ProductAudit).filter_by(shop_id=shop.id).all()
    assert len(audits) == 3
    by_id = {a.product_id: a for a in audits}
    assert by_id["101"].score >= 80
    assert by_id["102"].score < 40


def test_run_full_audit_respects_free_cap(db, mock_shopify):
    from app.config import get_settings
    from app.models import Shop

    shop = Shop(shop_domain="big.myshopify.com", access_token_enc=b"mock", plan=Plan.FREE, settings={})
    db.add(shop)
    db.commit()
    # Copy settings so we don't mutate the cached singleton for other tests.
    capped = get_settings().model_copy(update={"free_plan_product_limit": 1})
    service = CatalogService(db=db, shopify=mock_shopify, settings=capped)
    run = service.run_full_audit(shop)
    assert run.products_scored == 1


def test_score_and_store_upserts(service, shop, db, mock_shopify):
    product = mock_shopify.get_product("101")
    first = service.score_and_store(shop, product)
    second = service.score_and_store(shop, product)
    assert first.id == second.id
    assert db.query(ProductAudit).filter_by(shop_id=shop.id).count() == 1


def test_product_audits_filter_issue(service, shop):
    service.run_full_audit(shop)
    filtered = service.product_audits(shop, issue_code="missing_barcode")
    assert {a.product_id for a in filtered} == {"102", "103"}
    assert service.product_audits(shop, issue_code="nope") == []


def test_apply_fix_barcode(service, shop, mock_shopify):
    service.run_full_audit(shop)
    before = mock_shopify.get_product("102")["variants"][0]["barcode"]
    assert before == ""
    result = service.apply_fix(shop, "102", "missing_barcode")
    assert result["applied"]
    after = mock_shopify.get_product("102")["variants"][0]["barcode"]
    assert len(after) == 12
    # Re-score should raise the product's score
    audits = {a.product_id: a for a in service.product_audits(shop)}
    assert audits["102"].fixes_applied == 1


def test_apply_fix_description(service, shop, mock_shopify):
    from app.audit import word_count

    service.run_full_audit(shop)
    before = word_count(mock_shopify.get_product("102")["description_html"])
    result = service.apply_fix(shop, "102", "short_description")
    assert result["applied"]
    after = word_count(mock_shopify.get_product("102")["description_html"])
    assert after > before
    assert "Product details" in mock_shopify.get_product("102")["description_html"]


def test_apply_fix_alt_text(service, shop, mock_shopify):
    service.run_full_audit(shop)
    result = service.apply_fix(shop, "102", "missing_alt_text")
    assert result["applied"]
    assert mock_shopify.get_product("102")["images"][0]["alt"] == "Widget"


def test_apply_fix_faq(service, shop, mock_shopify, db):
    from app.models import FaqEntry

    service.run_full_audit(shop)
    result = service.apply_fix(shop, "102", "no_faq")
    assert result["applied"]
    mfs = {m["key"]: m for m in mock_shopify.get_product("102")["metafields"]}
    assert "seo_jsonld" in mfs and "faqs" in mfs
    assert db.query(FaqEntry).filter_by(shop_id=shop.id, product_id="102").count() >= 1


def test_apply_fix_unknown_code(service, shop):
    result = service.apply_fix(shop, "101", "missing_brand")
    assert not result["applied"]


def test_apply_fix_missing_product(service, shop):
    result = service.apply_fix(shop, "999", "missing_barcode")
    assert not result["applied"]


def test_bulk_fix_requires_pro(service, shop):
    result = service.bulk_fix(shop, "missing_barcode", ["102"])
    assert not result["applied"]
    assert "Pro" in result["detail"]


def test_bulk_fix_pro(db, pro_shop, mock_shopify):
    from app.config import get_settings

    service = CatalogService(db=db, shopify=mock_shopify, settings=get_settings())
    result = service.bulk_fix(pro_shop, "missing_barcode", ["102", "103"])
    assert result["applied"] == 2
    assert result["failed"] == 0


def test_bulk_fix_invalid_code_rejected(db, pro_shop, mock_shopify):
    from app.config import get_settings

    service = CatalogService(db=db, shopify=mock_shopify, settings=get_settings())
    result = service.bulk_fix(pro_shop, "missing_brand", ["102"])
    assert not result["applied"]


def test_bulk_fixable_codes_sane():
    assert BULK_FIXABLE == {"missing_barcode", "short_description", "missing_alt_text", "no_faq"}


def test_rescore_product_missing_returns_none(service, shop):
    assert service.rescore_product(shop, "nope") is None


def test_product_limit_for(service, shop, db, pro_shop):
    assert service.product_limit_for(shop) == service.settings.free_plan_product_limit
    assert service.product_limit_for(pro_shop) is None
