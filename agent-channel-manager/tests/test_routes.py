"""Route tests: dashboard, visibility, pricing, promotions, simulator, auth, webhooks."""
import base64
import hashlib
import hmac
import json
import urllib.parse

from app import security
from app.models import (
    AgentPromotion,
    ChannelMode,
    PricingRule,
    PricingRuleType,
    PricingScope,
    PromotionType,
    Shop,
    VisibilityAction,
    VisibilityRule,
)

SHOP_QS = "?shop=test.myshopify.com"


def _add_vis(db, shop, gid="gid://shopify/Product/1", action=VisibilityAction.HIDE):
    r = VisibilityRule(
        shop_id=shop.id, subject_type="product", subject_gid=gid,
        subject_title="t", action=action,
    )
    db.add(r)
    db.commit()
    return r


# -- basics ---------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_root_redirects_to_app(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/app")


def test_dashboard_loads(client, shop):
    r = client.get(f"/app{SHOP_QS}")
    assert r.status_code == 200
    assert "Agent channel overview" in r.text


def test_dashboard_requires_shop(client):
    # Unknown shop in mock mode resolves the domain but has no install record
    # -> exit-iframe bounce page that restarts OAuth at the top level
    # (a blind 302 inside the admin iframe dead-ends on Shopify's OAuth
    # page, which refuses to be framed, or loops through the callback).
    r = client.get("/app?shop=nobody.myshopify.com", follow_redirects=False)
    assert r.status_code == 200
    assert "/auth?shop=nobody.myshopify.com" in r.text
    assert "window.top" in r.text


def test_set_mode(client, db, shop):
    r = client.post(f"/app/mode{SHOP_QS}", data={"mode": "allowlist"},
                    follow_redirects=False)
    assert r.status_code == 302
    db.refresh(shop)
    assert shop.channel_mode == ChannelMode.ALLOWLIST


def test_set_mode_invalid(client, shop):
    r = client.post(f"/app/mode{SHOP_QS}", data={"mode": "bogus"})
    assert r.status_code == 400


# -- visibility -----------------------------------------------------------

def test_visibility_page_loads(client, shop):
    r = client.get(f"/app/visibility{SHOP_QS}")
    assert r.status_code == 200
    assert "Product visibility" in r.text


def test_visibility_add_hide(client, db, shop):
    r = client.post(
        f"/app/visibility{SHOP_QS}",
        data={"subject_gids": "gid://shopify/Product/2",
              "subject_type": "product", "action": "hide",
              "reason": "low margin"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    rule = db.query(VisibilityRule).filter_by(
        shop_id=shop.id, subject_gid="gid://shopify/Product/2").one_or_none()
    assert rule is not None
    assert rule.reason == "low margin"


def test_visibility_add_bulk(client, db, shop):
    r = client.post(
        f"/app/visibility{SHOP_QS}",
        data={"subject_gids": "gid://shopify/Product/2, gid://shopify/Product/3",
              "subject_type": "product", "action": "hide", "reason": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.query(VisibilityRule).filter_by(shop_id=shop.id).count() == 2


def test_visibility_add_duplicate_ignored(client, db, shop):
    _add_vis(db, shop)
    r = client.post(
        f"/app/visibility{SHOP_QS}",
        data={"subject_gids": "gid://shopify/Product/1",
              "subject_type": "product", "action": "hide", "reason": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.query(VisibilityRule).filter_by(shop_id=shop.id).count() == 1


def test_visibility_delete(client, db, shop):
    rule = _add_vis(db, shop)
    r = client.post(f"/app/visibility/{rule.id}/delete{SHOP_QS}",
                    follow_redirects=False)
    assert r.status_code == 302
    assert db.query(VisibilityRule).filter_by(id=rule.id).one_or_none() is None


def test_visibility_plan_limit(client, db, shop):
    for i in range(10):
        _add_vis(db, shop, gid=f"gid://shopify/Product/{100 + i}")
    r = client.post(
        f"/app/visibility{SHOP_QS}",
        data={"subject_gids": "gid://shopify/Product/999",
              "subject_type": "product", "action": "hide", "reason": ""},
    )
    assert r.status_code == 402


def test_visibility_plan_limit_not_hit_on_pro(client, db, pro_shop):
    for i in range(12):
        _add_vis(db, pro_shop, gid=f"gid://shopify/Product/{100 + i}")
    r = client.post(
        "/app/visibility?shop=pro.myshopify.com",
        data={"subject_gids": "gid://shopify/Product/999",
              "subject_type": "product", "action": "hide", "reason": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302


# -- pricing --------------------------------------------------------------

def _pricing_form(**over):
    form = {"name": "test rule", "rule_type": "floor", "scope": "all",
            "target_gid": "", "target_title": "", "value": "35.00",
            "excluded_code": "", "acknowledge_feed_risk": ""}
    form.update(over)
    return form


def test_pricing_page_loads(client, shop):
    r = client.get(f"/app/pricing{SHOP_QS}")
    assert r.status_code == 200
    assert "Agent pricing rules" in r.text


def test_pricing_add_floor(client, db, shop):
    r = client.post(f"/app/pricing{SHOP_QS}", data=_pricing_form(),
                    follow_redirects=False)
    assert r.status_code == 302
    rule = db.query(PricingRule).filter_by(shop_id=shop.id).one()
    assert rule.rule_type == PricingRuleType.FLOOR
    assert float(rule.value) == 35.00


def test_pricing_discount_requires_ack(client, db, shop):
    form = _pricing_form(rule_type="discount_pct", value="10")
    r = client.post(f"/app/pricing{SHOP_QS}", data=form)
    assert r.status_code == 400
    assert "Google Shopping" in r.text or "feed" in r.text.lower()


def test_pricing_discount_with_ack(client, db, shop):
    form = _pricing_form(rule_type="discount_pct", value="10",
                         acknowledge_feed_risk="1")
    r = client.post(f"/app/pricing{SHOP_QS}", data=form,
                    follow_redirects=False)
    assert r.status_code == 302
    rule = db.query(PricingRule).filter_by(shop_id=shop.id).one()
    assert rule.rule_type == PricingRuleType.DISCOUNT_PCT


def test_pricing_rejects_out_of_range_percent(client, shop):
    form = _pricing_form(rule_type="markup_pct", value="95")
    r = client.post(f"/app/pricing{SHOP_QS}", data=form)
    assert r.status_code == 400


def test_pricing_code_exclusion(client, db, shop):
    form = _pricing_form(rule_type="code_exclusion", excluded_code="WELCOME10")
    r = client.post(f"/app/pricing{SHOP_QS}", data=form,
                    follow_redirects=False)
    assert r.status_code == 302
    rule = db.query(PricingRule).filter_by(shop_id=shop.id).one()
    assert rule.excluded_code == "WELCOME10"


def test_pricing_toggle_and_delete(client, db, shop):
    rule = PricingRule(shop_id=shop.id, name="x",
                       rule_type=PricingRuleType.FLOOR, value=10)
    db.add(rule)
    db.commit()
    r = client.post(f"/app/pricing/{rule.id}/toggle{SHOP_QS}",
                    follow_redirects=False)
    assert r.status_code == 302
    db.refresh(rule)
    assert rule.enabled is False
    r = client.post(f"/app/pricing/{rule.id}/delete{SHOP_QS}",
                    follow_redirects=False)
    assert r.status_code == 302
    assert db.query(PricingRule).filter_by(id=rule.id).one_or_none() is None


# -- promotions -----------------------------------------------------------

def test_promotions_page_loads(client, shop):
    r = client.get(f"/app/promotions{SHOP_QS}")
    assert r.status_code == 200
    assert "Agent-only promotions" in r.text


def test_promotion_bundle_creates_shopify_discount(client, db, shop):
    from app.deps import _shopify_client

    r = client.post(
        f"/app/promotions{SHOP_QS}",
        data={"name": "Tee + Bottle", "promo_type": "bundle",
              "product_gids": "gid://shopify/Product/1, gid://shopify/Product/3",
              "bundle_price": "49.99", "threshold": "", "discount_pct": "15"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    promo = db.query(AgentPromotion).filter_by(shop_id=shop.id).one()
    assert promo.promo_type == PromotionType.BUNDLE
    assert promo.shopify_discount_id.startswith("gid://shopify/DiscountCodeNode/")
    mock = _shopify_client()
    assert len(mock.created_discounts) == 1
    assert mock.created_discounts[0]["code"].startswith("AGENT-")


def test_promotion_bundle_validation(client, db, shop):
    r = client.post(
        f"/app/promotions{SHOP_QS}",
        data={"name": "bad", "promo_type": "bundle",
              "product_gids": "gid://shopify/Product/1",
              "bundle_price": "49.99", "threshold": "", "discount_pct": "10"},
    )
    assert r.status_code == 400
    assert db.query(AgentPromotion).filter_by(shop_id=shop.id).count() == 0


def test_promotion_free_shipping(client, db, shop):
    r = client.post(
        f"/app/promotions{SHOP_QS}",
        data={"name": "FS over 75", "promo_type": "free_shipping",
              "product_gids": "", "bundle_price": "",
              "threshold": "75.00", "discount_pct": "100"},
        follow_redirects=False,
    )
    # discount_pct 100 is out of range -> 400
    assert r.status_code == 400
    r = client.post(
        f"/app/promotions{SHOP_QS}",
        data={"name": "FS over 75", "promo_type": "free_shipping",
              "product_gids": "", "bundle_price": "",
              "threshold": "75.00", "discount_pct": "10"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    promo = db.query(AgentPromotion).filter_by(shop_id=shop.id).one()
    assert promo.config["threshold"] == "75.00"


def test_promotion_toggle_and_delete(client, db, shop):
    from app.deps import _shopify_client

    promo = AgentPromotion(
        shop_id=shop.id, name="x", promo_type=PromotionType.FREE_SHIPPING,
        config={"threshold": "50.00"},
        shopify_discount_id="gid://shopify/DiscountCodeNode/4242",
    )
    db.add(promo)
    db.commit()
    r = client.post(f"/app/promotions/{promo.id}/toggle{SHOP_QS}",
                    follow_redirects=False)
    assert r.status_code == 302
    db.refresh(promo)
    assert promo.enabled is False
    r = client.post(f"/app/promotions/{promo.id}/delete{SHOP_QS}",
                    follow_redirects=False)
    assert r.status_code == 302
    assert db.query(AgentPromotion).filter_by(id=promo.id).one_or_none() is None
    mock = _shopify_client()
    assert "gid://shopify/DiscountCodeNode/4242" in mock.deleted_discounts


def test_promotion_plan_limit(client, db, shop):
    db.add(AgentPromotion(shop_id=shop.id, name="existing",
                          promo_type=PromotionType.FREE_SHIPPING,
                          config={"threshold": "10.00"}))
    db.commit()
    r = client.post(
        f"/app/promotions{SHOP_QS}",
        data={"name": "second", "promo_type": "free_shipping",
              "product_gids": "", "bundle_price": "",
              "threshold": "20.00", "discount_pct": "10"},
    )
    assert r.status_code == 402


# -- simulator ------------------------------------------------------------

def test_simulator_loads(client, shop):
    r = client.get(f"/app/simulator{SHOP_QS}")
    assert r.status_code == 200
    assert "Margin guard simulator" in r.text
    assert "Everyday Cotton T-Shirt" in r.text


# -- auth -----------------------------------------------------------------

def test_auth_start_redirects(client):
    r = client.get("/auth?shop=demo.myshopify.com", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(
        "https://demo.myshopify.com/admin/oauth/authorize")


def test_auth_callback_creates_shop(client, db):
    from app.config import get_settings

    settings = get_settings()
    state = security.new_oauth_state(settings)
    params = {"shop": "brand-new.myshopify.com", "state": state, "code": "abc"}
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    params["hmac"] = hmac.new(
        settings.shopify_api_secret.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    r = client.get("/auth/callback?" + urllib.parse.urlencode(params),
                   follow_redirects=False)
    assert r.status_code == 302
    shop = db.query(Shop).filter_by(
        shop_domain="brand-new.myshopify.com").one_or_none()
    assert shop is not None
    assert shop.plan.value == settings.resolved_default_plan


def test_auth_callback_bad_hmac(client):
    r = client.get("/auth/callback?shop=x.myshopify.com&state=y&code=z&hmac=nope")
    assert r.status_code == 401


# -- webhooks -------------------------------------------------------------

def _webhook(client, topic, shop_domain, payload=None):
    body = b"{}" if payload is None else json.dumps(payload).encode()
    digest = hmac.new(b"test-api-secret", body, hashlib.sha256).digest()
    header = base64.b64encode(digest).decode()
    return client.post(
        f"/webhooks/{topic}",
        content=body,
        headers={
            "x-shopify-hmac-sha256": header,
            "x-shopify-shop-domain": shop_domain,
            "x-shopify-topic": topic,
            "content-type": "application/json",
        },
    )


def test_webhook_uninstalled(client, db, shop):
    r = _webhook(client, "app/uninstalled", "test.myshopify.com")
    assert r.status_code == 200
    assert r.json()["handled"] is True
    db.refresh(shop)
    assert shop.uninstalled_at is not None


def test_webhook_bad_hmac(client):
    r = client.post(
        "/webhooks/app/uninstalled",
        content=b"{}",
        headers={"x-shopify-hmac-sha256": "bogus",
                 "x-shopify-shop-domain": "test.myshopify.com",
                 "content-type": "application/json"},
    )
    assert r.status_code == 401


def test_webhook_shop_redact_erases_data(client, db, shop):
    _add_vis(db, shop)
    shop_id = shop.id
    r = _webhook(client, "shop/redact", "test.myshopify.com")
    assert r.status_code == 200
    assert db.query(Shop).filter_by(shop_domain="test.myshopify.com").one_or_none() is None
    assert db.query(VisibilityRule).filter_by(shop_id=shop_id).count() == 0
