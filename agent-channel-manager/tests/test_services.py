"""Tests for channel evaluation, pricing rules, and promotions."""
from decimal import Decimal

import pytest

from app import services
from app.models import (
    ChannelMode,
    PricingRule,
    PricingRuleType,
    PricingScope,
    PromotionType,
    VisibilityAction,
    VisibilityRule,
)
from app.shopify.mock import PRODUCTS


def _product(pid=1):
    return next(p for p in PRODUCTS if p.product_id.endswith(f"/{pid}"))


def _hide(db, shop, gid, subject_type="product", reason="test"):
    r = VisibilityRule(
        shop_id=shop.id, subject_type=subject_type, subject_gid=gid,
        subject_title="t", action=VisibilityAction.HIDE, reason=reason,
    )
    db.add(r)
    db.commit()
    return r


def _show(db, shop, gid, subject_type="product"):
    r = VisibilityRule(
        shop_id=shop.id, subject_type=subject_type, subject_gid=gid,
        subject_title="t", action=VisibilityAction.SHOW,
    )
    db.add(r)
    db.commit()
    return r


def _pricing(db, shop, rule_type, value=0, scope=PricingScope.ALL,
             target_gid="", excluded_code="", priority=0, name="r"):
    r = PricingRule(
        shop_id=shop.id, name=name, enabled=True, rule_type=rule_type,
        scope=scope, target_gid=target_gid, value=Decimal(str(value)),
        excluded_code=excluded_code, priority=priority,
    )
    db.add(r)
    db.commit()
    return r


# -- visibility -----------------------------------------------------------

def test_blocklist_default_visible(db, shop):
    visible, _ = services.is_visible(_product(1), shop, {})
    assert visible


def test_blocklist_hide_product(db, shop):
    p = _product(1)
    _hide(db, shop, p.product_id)
    lookup = {p.product_id: db.query(VisibilityRule).first()}
    visible, reason = services.is_visible(p, shop, lookup)
    assert not visible
    assert "hidden by rule" in reason


def test_allowlist_default_hidden(db, shop):
    shop.channel_mode = ChannelMode.ALLOWLIST
    db.commit()
    visible, reason = services.is_visible(_product(1), shop, {})
    assert not visible
    assert "allowlist default" in reason


def test_allowlist_show_product(db, shop):
    shop.channel_mode = ChannelMode.ALLOWLIST
    db.commit()
    p = _product(1)
    _show(db, shop, p.product_id)
    lookup = {p.product_id: db.query(VisibilityRule).first()}
    visible, _ = services.is_visible(p, shop, lookup)
    assert visible


def test_collection_hide_cascades_in_blocklist(db, shop):
    p = _product(1)  # in collection 10
    coll_gid = "gid://shopify/Collection/10"
    _hide(db, shop, coll_gid, subject_type="collection")
    lookup = {coll_gid: db.query(VisibilityRule).first()}
    visible, reason = services.is_visible(p, shop, lookup)
    assert not visible
    assert "collection" in reason


def test_collection_show_cascades_in_allowlist(db, shop):
    shop.channel_mode = ChannelMode.ALLOWLIST
    db.commit()
    p = _product(1)
    coll_gid = "gid://shopify/Collection/10"
    _show(db, shop, coll_gid, subject_type="collection")
    lookup = {coll_gid: db.query(VisibilityRule).first()}
    visible, _ = services.is_visible(p, shop, lookup)
    assert visible


def test_product_rule_beats_collection_rule(db, shop):
    p = _product(1)
    coll_gid = "gid://shopify/Collection/10"
    _hide(db, shop, coll_gid, subject_type="collection")
    _show(db, shop, p.product_id)
    rules = {r.subject_gid: r for r in db.query(VisibilityRule).all()}
    visible, _ = services.is_visible(p, shop, rules)
    assert visible  # explicit product SHOW wins over collection HIDE in blocklist


# -- pricing --------------------------------------------------------------

def test_floor_raises_price():
    price, applied, _ = services.apply_pricing(
        Decimal("29.99"),
        [PricingRule(name="floor", rule_type=PricingRuleType.FLOOR,
                     value=Decimal("39.99"))],
    )
    assert price == Decimal("39.99")
    assert applied == ["floor"]


def test_floor_noop_when_above():
    price, applied, _ = services.apply_pricing(
        Decimal("50.00"),
        [PricingRule(name="floor", rule_type=PricingRuleType.FLOOR,
                     value=Decimal("39.99"))],
    )
    assert price == Decimal("50.00")
    assert applied == []


def test_markup_pct():
    price, applied, _ = services.apply_pricing(
        Decimal("100.00"),
        [PricingRule(name="markup", rule_type=PricingRuleType.MARKUP_PCT,
                     value=Decimal("5"))],
    )
    assert price == Decimal("105.00")
    assert applied == ["markup"]


def test_discount_pct():
    price, _, _ = services.apply_pricing(
        Decimal("100.00"),
        [PricingRule(name="disc", rule_type=PricingRuleType.DISCOUNT_PCT,
                     value=Decimal("10"))],
    )
    assert price == Decimal("90.00")


def test_code_exclusion_records_code_without_price_change():
    price, applied, excluded = services.apply_pricing(
        Decimal("100.00"),
        [PricingRule(name="no-welcome", rule_type=PricingRuleType.CODE_EXCLUSION,
                     excluded_code="WELCOME10")],
    )
    assert price == Decimal("100.00")
    assert excluded == ["WELCOME10"]
    assert applied == ["no-welcome"]


def test_priority_order_discount_then_floor(db, shop):
    # Discount 50% on $100 -> $50, then floor $80 -> $80 (floor wins).
    _pricing(db, shop, PricingRuleType.DISCOUNT_PCT, 50, priority=0, name="disc")
    _pricing(db, shop, PricingRuleType.FLOOR, 80, priority=1, name="floor")
    rules = db.query(PricingRule).order_by(PricingRule.priority).all()
    price, applied, _ = services.apply_pricing(Decimal("100.00"), rules)
    assert price == Decimal("80.00")
    assert applied == ["disc", "floor"]


def test_scoped_rule_only_applies_to_target(db, shop):
    p1, p2 = _product(1), _product(2)
    _pricing(db, shop, PricingRuleType.MARKUP_PCT, 10,
             scope=PricingScope.PRODUCT, target_gid=p1.product_id, name="m")
    views = services.evaluate_channel([p1, p2], shop, db)
    v1 = next(v for v in views if v.product.product_id == p1.product_id)
    v2 = next(v for v in views if v.product.product_id == p2.product_id)
    assert v1.agent_price == Decimal("32.99")  # 29.99 + 10%
    assert v2.agent_price == v2.storefront_price


# -- evaluate_channel / summarize -----------------------------------------

def test_evaluate_channel_marks_feed_risk(db, shop):
    _pricing(db, shop, PricingRuleType.DISCOUNT_PCT, 20, name="disc")
    views = services.evaluate_channel([_product(1)], shop, db)
    v = views[0]
    assert v.visible
    assert v.agent_price == Decimal("23.99")
    assert v.feed_risk  # below storefront price


def test_evaluate_channel_hidden_product(db, shop):
    p = _product(1)
    _hide(db, shop, p.product_id)
    views = services.evaluate_channel([p], shop, db)
    v = views[0]
    assert not v.visible
    assert v.agent_price == v.storefront_price  # pricing not applied when hidden


def test_evaluate_channel_margin(db, shop):
    # T-shirt: price 29.99, unit_cost 9.50 -> margin 20.49
    views = services.evaluate_channel([_product(1)], shop, db)
    v = views[0]
    assert v.margin_amount == Decimal("20.49")
    assert v.margin_pct == Decimal("68.32")


def test_summarize(db, shop):
    _hide(db, shop, _product(2).product_id)
    _pricing(db, shop, PricingRuleType.DISCOUNT_PCT, 10, name="disc")
    views = services.evaluate_channel(list(PRODUCTS), shop, db)
    s = services.summarize(views)
    assert s.total_products == 5
    assert s.visible_products == 4
    assert s.hidden_products == 1
    assert s.feed_risk_count == 4
    assert s.avg_discount_pct == Decimal("10.00")
    assert s.estimated_margin_total is not None


# -- promotions -----------------------------------------------------------

def test_bundle_validation_needs_two_products(db, shop):
    from app.models import AgentPromotion
    promo = AgentPromotion(
        shop_id=shop.id, promo_type=PromotionType.BUNDLE,
        config={"product_gids": ["gid://shopify/Product/1"], "bundle_price": "49.99"},
    )
    errors = services.validate_promotion_config(PromotionType.BUNDLE, promo.config)
    assert any("at least 2" in e for e in errors)


def test_bundle_validation_bad_price():
    errors = services.validate_promotion_config(
        PromotionType.BUNDLE,
        {"product_gids": ["a", "b"], "bundle_price": "-5"},
    )
    assert errors


def test_free_shipping_validation():
    assert services.validate_promotion_config(
        PromotionType.FREE_SHIPPING, {"threshold": "75.00"}
    ) == []
    errors = services.validate_promotion_config(
        PromotionType.FREE_SHIPPING, {"threshold": "0"}
    )
    assert errors


def test_promotion_discount_code_stable_and_prefixed(db, shop):
    from app.models import AgentPromotion
    promo = AgentPromotion(shop_id=shop.id, promo_type=PromotionType.BUNDLE, config={})
    db.add(promo)
    db.commit()
    code1 = services.promotion_discount_code(promo, shop.shop_domain)
    code2 = services.promotion_discount_code(promo, shop.shop_domain)
    assert code1 == code2
    assert code1.startswith("AGENT-")
