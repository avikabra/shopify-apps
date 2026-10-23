"""Tests for Shopify Billing API wiring (freemium plans)."""
import json

import pytest

from app import billing as billing_svc
from app.models import ACTIVE_SUBSCRIPTION_STATUSES, Plan
from app.shopify.client import AppSubscription

SHOP_QS = "?shop=test.myshopify.com"
PRO_QS = "?shop=pro.myshopify.com"


def _sub(name="Agent Catalog Fixer Pro", status="ACTIVE", trial_days=14):
    return AppSubscription(
        id="gid://shopify/AppSubscription/42",
        name=name,
        status=status,
        trial_days=trial_days,
    )


# -- name mapping ----------------------------------------------------------


def test_subscription_name_for_plan():
    assert billing_svc.subscription_name_for_plan(Plan.FREE) == "Agent Catalog Fixer Free"
    assert billing_svc.subscription_name_for_plan(Plan.PRO) == "Agent Catalog Fixer Pro"


def test_plan_from_subscription_name():
    assert billing_svc.plan_from_subscription_name("Agent Catalog Fixer Pro") == Plan.PRO
    assert billing_svc.plan_from_subscription_name("Agent Catalog Fixer Free") == Plan.FREE
    # Keyword match survives app-prefix renames.
    assert billing_svc.plan_from_subscription_name("Renamed App Pro") == Plan.PRO
    assert billing_svc.plan_from_subscription_name("Some Other App") is None
    assert billing_svc.plan_from_subscription_name("") is None


def test_active_subscription_statuses():
    assert "ACTIVE" in ACTIVE_SUBSCRIPTION_STATUSES
    assert "TRIALING" in ACTIVE_SUBSCRIPTION_STATUSES


# -- sync_subscription_to_shop ---------------------------------------------


def test_sync_active_subscription_sets_pro(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub())
    assert plan == Plan.PRO
    db.refresh(shop)
    assert shop.plan == Plan.PRO
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/42"
    assert shop.subscription_status == "ACTIVE"
    assert shop.trial_ends_at is not None


def test_sync_trialling_subscription_sets_pro(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status="TRIALING"))
    assert plan == Plan.PRO
    db.refresh(shop)
    assert shop.plan == Plan.PRO


def test_sync_cancelled_subscription_drops_to_free(db, shop):
    shop.plan = Plan.PRO
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status="CANCELLED"))
    assert plan == Plan.FREE
    db.refresh(shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None


def test_sync_no_subscription_means_free(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, None)
    assert plan == Plan.FREE
    db.refresh(shop)
    assert shop.plan == Plan.FREE


def test_downgrade_to_free(db, shop):
    shop.plan = Plan.PRO
    shop.app_subscription_id = "gid://shopify/AppSubscription/42"
    shop.subscription_status = "ACTIVE"
    billing_svc.downgrade_to_free(db, shop)
    db.refresh(shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None
    assert shop.subscription_status is None


# -- require_pro_plan ------------------------------------------------------


def test_require_pro_plan_allows_pro(pro_shop):
    billing_svc.require_pro_plan(pro_shop)  # no raise


def test_require_pro_plan_raises_402_for_free(shop):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        billing_svc.require_pro_plan(shop)
    assert exc_info.value.status_code == 402
    assert "/app/billing" in exc_info.value.detail


# -- mock client billing ---------------------------------------------------


def test_mock_create_approve_cancel_subscription(mock_shopify):
    sid, confirmation_url = mock_shopify.create_app_subscription(
        name="Agent Catalog Fixer Pro",
        price_cents=1900,
        currency="USD",
        trial_days=14,
        return_url="http://testserver/app/billing/callback",
        test=True,
    )
    assert sid.startswith("gid://shopify/AppSubscription/")
    assert confirmation_url.startswith("http://testserver/app/billing/callback")

    mock_shopify.approve_mock_subscription(sid)
    subs = mock_shopify.get_active_app_subscriptions()
    assert [s.id for s in subs] == [sid]
    assert subs[0].status == "ACTIVE"

    mock_shopify.cancel_app_subscription(sid)
    assert mock_shopify.get_active_app_subscriptions() == []
    assert sid in mock_shopify.cancelled_subscriptions


def test_sync_shop_billing_via_mock(db, shop, mock_shopify):
    mock_shopify.subscriptions["gid://shopify/AppSubscription/7"] = {
        "name": "Agent Catalog Fixer Pro",
        "status": "ACTIVE",
        "price_cents": 1900,
        "trial_days": 14,
    }
    plan = billing_svc.sync_shop_billing(db, shop, mock_shopify)
    assert plan == Plan.PRO
    db.refresh(shop)
    assert shop.plan == Plan.PRO


# -- billing routes --------------------------------------------------------


def test_billing_page_renders(client, shop):
    r = client.get(f"/app/billing{SHOP_QS}")
    assert r.status_code == 200
    assert "Upgrade to Pro" in r.text
    assert "current plan" in r.text
    assert "/app/billing/subscribe" in r.text


def test_subscribe_upgrade_mock_mode(client, shop, db):
    r = client.post(
        f"/app/billing/subscribe{SHOP_QS}", data={"plan": "pro"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.PRO


def test_subscribe_cancel_downgrade_mock_mode(client, pro_shop, db):
    r = client.post(f"/app/billing/cancel{PRO_QS}", follow_redirects=False)
    assert r.status_code == 303
    db.refresh(pro_shop)
    assert pro_shop.plan == Plan.FREE


def test_subscribe_unknown_plan_rejected(client, shop):
    r = client.post(f"/app/billing/subscribe{SHOP_QS}", data={"plan": "enterprise"})
    assert r.status_code == 400


# -- plan enforcement -------------------------------------------------------


def test_bulk_fix_route_rejects_free_with_402(client, shop):
    r = client.post(
        f"/api/bulk-fix{SHOP_QS}",
        json={"fix_code": "missing_alt_text", "product_ids": ["101"]},
    )
    assert r.status_code == 402
    assert "/app/billing" in r.json()["detail"]


def test_bulk_fix_route_allows_pro(client, pro_shop, mock_shopify):
    # The deps' mock singleton is process-wide; start from a fresh catalog
    # and leave it clean so later tests get pristine fixture data too.
    from app import deps

    deps._mock_singleton.pop("pro.myshopify.com", None)
    try:
        r = client.post(
            f"/api/bulk-fix{PRO_QS}",
            json={"fix_code": "missing_barcode", "product_ids": ["102"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["applied"] == 1
    finally:
        deps._mock_singleton.pop("pro.myshopify.com", None)


# -- app_subscriptions/update webhook ---------------------------------------


def test_webhook_app_subscriptions_update_syncs_plan(client, shop, db):
    import base64
    import hashlib
    import hmac

    from app.config import get_settings

    body = json.dumps({
        "app_subscription": {
            "id": "gid://shopify/AppSubscription/9",
            "name": "Agent Catalog Fixer Pro",
            "status": "ACTIVE",
            "trial_days": 14,
        }
    }).encode()
    secret = get_settings().shopify_api_secret
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    headers = {
        "X-Shopify-Hmac-Sha256": base64.b64encode(digest).decode(),
        "X-Shopify-Topic": "app_subscriptions/update",
        "X-Shopify-Shop-Domain": "test.myshopify.com",
        "Content-Type": "application/json",
    }
    r = client.post("/webhooks", content=body, headers=headers)
    assert r.status_code == 200
    db.refresh(shop)
    assert shop.plan == Plan.PRO
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/9"


def test_webhook_app_subscriptions_cancelled_downgrades(client, pro_shop, db):
    from app.routes.webhooks import handle_app_subscriptions_update

    handle_app_subscriptions_update(
        db, pro_shop,
        {"app_subscription": {"id": "gid://shopify/AppSubscription/9",
                             "name": "Agent Catalog Fixer Pro",
                             "status": "CANCELLED"}},
    )
    db.refresh(pro_shop)
    assert pro_shop.plan == Plan.FREE
