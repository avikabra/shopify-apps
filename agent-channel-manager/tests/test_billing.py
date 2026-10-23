"""Billing tests: plan catalog, sync logic, webhooks, mock client, routes,
plan-limit enforcement (402) on rule creation."""
import asyncio

import pytest

from app import billing as billing_svc
from app import deps
from app.models import (
    PLAN_LIMITS,
    Plan,
    VisibilityAction,
    VisibilityRule,
)
from app.shopify.mock import MockShopifyClient
from app.shopify.protocol import AppSubscription, ShopifyClientProtocol
from app.webhooks import handle_app_subscriptions_update

SHOP_QS = "?shop=test.myshopify.com"
PRO_QS = "?shop=pro.myshopify.com"


def _sub(name="Agent Channel Manager Pro", status="ACTIVE", trial_days=14):
    return AppSubscription(
        id="gid://shopify/AppSubscription/1",
        name=name,
        status=status,
        trial_days=trial_days,
    )


def _add_vis(db, shop, gid, action=VisibilityAction.HIDE):
    r = VisibilityRule(
        shop_id=shop.id,
        subject_type="product",
        subject_gid=gid,
        subject_title="t",
        action=action,
    )
    db.add(r)
    db.commit()
    return r


# -- plan catalog -----------------------------------------------------------


def test_plan_limits_cover_all_plans():
    assert set(PLAN_LIMITS) == {Plan.FREE, Plan.PRO}
    assert PLAN_LIMITS[Plan.FREE]["price_cents"] == 0
    assert PLAN_LIMITS[Plan.FREE]["visibility_rules"] == 10
    assert PLAN_LIMITS[Plan.FREE]["pricing_rules"] == 2
    assert PLAN_LIMITS[Plan.FREE]["promotions"] == 1
    assert PLAN_LIMITS[Plan.PRO]["price_cents"] == 1900
    assert PLAN_LIMITS[Plan.PRO]["visibility_rules"] is None  # unlimited
    assert PLAN_LIMITS[Plan.PRO]["pricing_rules"] is None
    assert PLAN_LIMITS[Plan.PRO]["promotions"] is None


def test_trial_days():
    assert billing_svc.TRIAL_DAYS[Plan.FREE] == 0
    assert billing_svc.TRIAL_DAYS[Plan.PRO] == 14


def test_subscription_name_roundtrip():
    for plan in Plan:
        name = billing_svc.subscription_name_for_plan(plan)
        assert billing_svc.plan_from_subscription_name(name) == plan


def test_plan_from_subscription_name_variants():
    assert billing_svc.plan_from_subscription_name("Agent Channel Manager Pro") == Plan.PRO
    assert billing_svc.plan_from_subscription_name("agent channel manager pro") == Plan.PRO
    assert billing_svc.plan_from_subscription_name("Agent Channel Manager Free") == Plan.FREE
    assert billing_svc.plan_from_subscription_name("Something Else") is None
    assert billing_svc.plan_from_subscription_name("") is None


def test_is_subscription_active():
    assert billing_svc.is_subscription_active(_sub(status="ACTIVE"))
    assert billing_svc.is_subscription_active(_sub(status="TRIALING"))
    assert not billing_svc.is_subscription_active(_sub(status="CANCELLED"))
    assert not billing_svc.is_subscription_active(_sub(status="PENDING"))


def test_plan_order():
    assert billing_svc.PLAN_ORDER == [Plan.FREE, Plan.PRO]


def test_plan_features_describe_rule_limits():
    free = " ".join(billing_svc.PLAN_FEATURES[Plan.FREE])
    assert "10" in free and "visibility" in free
    assert "2" in free and "pricing" in free


# -- sync logic -------------------------------------------------------------


def test_sync_active_subscription_sets_pro(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub())
    assert plan == Plan.PRO
    assert shop.plan == Plan.PRO
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/1"
    assert shop.subscription_status == "ACTIVE"


def test_sync_trialing_sets_pro(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status="TRIALING"))
    assert plan == Plan.PRO
    assert shop.plan == Plan.PRO


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "DECLINED", "FROZEN"])
def test_sync_terminal_statuses_downgrade(db, shop, status):
    shop.plan = Plan.PRO
    shop.app_subscription_id = "gid://shopify/AppSubscription/1"
    db.commit()
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status=status))
    assert plan == Plan.FREE
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None
    assert shop.subscription_status == status


def test_sync_none_subscription_means_free(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, None)
    assert plan == Plan.FREE
    assert shop.plan == Plan.FREE


def test_sync_unknown_name_falls_back_to_free(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(name="Mystery Plan"))
    assert plan == Plan.FREE


def test_downgrade_to_free(db, shop):
    shop.plan = Plan.PRO
    shop.app_subscription_id = "gid://shopify/AppSubscription/9"
    shop.subscription_status = "ACTIVE"
    db.commit()
    billing_svc.downgrade_to_free(db, shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None
    assert shop.subscription_status is None


# -- webhook handler --------------------------------------------------------


def test_webhook_app_subscriptions_update_active(db, shop):
    result = handle_app_subscriptions_update(
        db,
        shop,
        {
            "app_subscription": {
                "id": "gid://shopify/AppSubscription/2",
                "name": "Agent Channel Manager Pro",
                "status": "ACTIVE",
            }
        },
    )
    assert shop.plan == Plan.PRO
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/2"
    assert result["plan"] == "pro"


def test_webhook_app_subscriptions_update_cancelled(db, shop):
    shop.plan = Plan.PRO
    db.commit()
    handle_app_subscriptions_update(
        db,
        shop,
        {
            "app_subscription": {
                "id": "gid://shopify/AppSubscription/2",
                "name": "Agent Channel Manager Pro",
                "status": "CANCELLED",
            }
        },
    )
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None


# -- mock client + full sync round-trip ------------------------------------


def test_mock_client_implements_protocol():
    # Structural check: MockShopifyClient must satisfy the billing seam.
    client = MockShopifyClient()
    for method in (
        "get_active_app_subscriptions",
        "create_app_subscription",
        "cancel_app_subscription",
    ):
        assert callable(getattr(client, method)), method


def test_mock_create_approve_sync(db, shop):
    client = MockShopifyClient()

    async def flow():
        sid, confirmation_url = await client.create_app_subscription(
            "mock",
            shop.shop_domain,
            name=billing_svc.subscription_name_for_plan(Plan.PRO),
            price_cents=PLAN_LIMITS[Plan.PRO]["price_cents"],
            currency="USD",
            trial_days=billing_svc.TRIAL_DAYS[Plan.PRO],
            return_url="http://testserver/app/billing/callback?shop=test.myshopify.com",
            test=True,
        )
        assert confirmation_url.startswith(
            "http://testserver/app/billing/callback"
        )
        # Before approval the mock subscription is still PENDING; the sync
        # prefers ACTIVE/TRIALING but falls back to the first subscription.
        pending = await billing_svc.current_subscription(client, "mock", shop.shop_domain)
        assert pending is not None and pending.status == "PENDING"
        client.approve_mock_subscription(sid)
        return await billing_svc.sync_shop_billing(db, shop, client, "mock")

    plan = asyncio.run(flow())
    assert plan == Plan.PRO
    assert shop.plan == Plan.PRO
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/1"
    assert shop.subscription_status == "ACTIVE"


def test_mock_cancel_marks_cancelled():
    client = MockShopifyClient()

    async def flow():
        sid, _ = await client.create_app_subscription(
            "mock",
            "test.myshopify.com",
            name="Agent Channel Manager Pro",
            price_cents=1900,
            currency="USD",
            trial_days=14,
            return_url="http://x/callback",
            test=True,
        )
        client.approve_mock_subscription(sid)
        await client.cancel_app_subscription("mock", "test.myshopify.com", sid)
        assert client.cancelled_subscriptions == [sid]
        assert (
            await billing_svc.current_subscription(client, "mock", "test.myshopify.com")
            is None
        )

    asyncio.run(flow())


# -- routes ------------------------------------------------------------------


def test_billing_page_loads(client, shop):
    r = client.get(f"/app/billing{SHOP_QS}")
    assert r.status_code == 200
    assert "Billing" in r.text
    assert "visibility rules" in r.text.lower()
    assert "current" in r.text
    # Two plan cards: Free and Pro.
    assert "Pro" in r.text
    assert "$19" in r.text


def test_billing_page_shows_usage_vs_limits(client, db, shop):
    _add_vis(db, shop, "gid://shopify/Product/11")
    r = client.get(f"/app/billing{SHOP_QS}")
    assert r.status_code == 200
    # usage table shows the count next to the Free limit of 10
    assert ">1<" in r.text or ">1</td>" in r.text


def test_subscribe_pro_mock_mode(client, db, shop):
    r = client.post(
        f"/app/billing/subscribe{SHOP_QS}",
        data={"plan": "pro"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.PRO
    assert shop.subscription_status == "ACTIVE"


def test_subscribe_free_downgrades(client, db, pro_shop):
    r = client.post(
        f"/app/billing/subscribe{PRO_QS}",
        data={"plan": "free"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(pro_shop)
    assert pro_shop.plan == Plan.FREE


def test_subscribe_same_plan_redirects_unchanged(client, db, shop):
    r = client.post(
        f"/app/billing/subscribe{SHOP_QS}",
        data={"plan": "free"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.FREE


def test_subscribe_unknown_plan_400(client, shop):
    r = client.post(
        f"/app/billing/subscribe{SHOP_QS}", data={"plan": "enterprise"}
    )
    assert r.status_code == 400


def test_cancel_route_downgrades(client, db, shop):
    shop.plan = Plan.PRO
    shop.subscription_status = "ACTIVE"
    db.commit()
    r = client.post(f"/app/billing/cancel{SHOP_QS}", follow_redirects=False)
    assert r.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None
    assert shop.subscription_status is None


def test_billing_callback_syncs_approved_mock_subscription(client, db, shop):
    # Arrange: approved subscription sitting in the (cached) mock client.
    mock = deps.shopify_client()

    async def arrange():
        sid, _ = await mock.create_app_subscription(
            "mock",
            shop.shop_domain,
            name=billing_svc.subscription_name_for_plan(Plan.PRO),
            price_cents=1900,
            currency="USD",
            trial_days=14,
            return_url="http://testserver/app/billing/callback?shop=test.myshopify.com",
            test=True,
        )
        mock.approve_mock_subscription(sid)

    asyncio.run(arrange())
    r = client.get(f"/app/billing/callback{SHOP_QS}", follow_redirects=False)
    assert r.status_code == 303
    assert "subscription-active" in r.headers["location"]
    db.refresh(shop)
    assert shop.plan == Plan.PRO


# -- plan limit enforcement ---------------------------------------------------


def test_free_plan_visibility_limit_returns_402(client, db, shop):
    for i in range(10):
        _add_vis(db, shop, f"gid://shopify/Product/{100 + i}")
    r = client.post(
        f"/app/visibility{SHOP_QS}",
        data={
            "subject_gids": "gid://shopify/Product/999",
            "subject_type": "product",
            "action": "hide",
        },
        follow_redirects=False,
    )
    assert r.status_code == 402
    assert "/app/billing" in r.json()["detail"]


def test_pro_plan_visibility_unlimited(client, db, pro_shop):
    for i in range(10):
        _add_vis(db, pro_shop, f"gid://shopify/Product/{200 + i}")
    r = client.post(
        f"/app/visibility{PRO_QS}",
        data={
            "subject_gids": "gid://shopify/Product/999",
            "subject_type": "product",
            "action": "hide",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302  # matches the admin visibility route's redirect
    count = db.query(VisibilityRule).filter_by(shop_id=pro_shop.id).count()
    assert count == 11
    assert count == 11
