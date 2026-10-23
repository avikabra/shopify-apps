"""Billing tests: plan catalog, Shopify Billing API flow, webhooks, routes."""
import pytest

from app import billing as billing_svc
from app.models import PLAN_LIMITS, Plan, PlanUsage
from app.shopify.mock import MockShopifyClient
from app.shopify.protocol import AppSubscription, ShopifyError
from app.webhooks import handle_app_subscriptions_update


def _sub(name="Simple Returns Starter", status="ACTIVE", trial_days=14):
    return AppSubscription(
        id="gid://shopify/AppSubscription/1",
        name=name,
        status=status,
        trial_days=trial_days,
    )


# -- plan catalog -----------------------------------------------------------


def test_plan_limits_cover_all_plans():
    assert set(PLAN_LIMITS) == {Plan.FREE, Plan.STARTER, Plan.GROWTH}
    assert PLAN_LIMITS[Plan.FREE]["price_cents"] == 0
    assert PLAN_LIMITS[Plan.FREE]["monthly_returns"] == 10
    assert PLAN_LIMITS[Plan.STARTER]["price_cents"] == 1200
    assert PLAN_LIMITS[Plan.STARTER]["monthly_returns"] == 50
    assert PLAN_LIMITS[Plan.GROWTH]["price_cents"] == 2400
    assert PLAN_LIMITS[Plan.GROWTH]["monthly_returns"] is None  # unlimited


def test_trial_days():
    assert billing_svc.TRIAL_DAYS[Plan.FREE] == 0
    assert billing_svc.TRIAL_DAYS[Plan.STARTER] == 14
    assert billing_svc.TRIAL_DAYS[Plan.GROWTH] == 14


def test_subscription_name_roundtrip():
    for plan in Plan:
        name = billing_svc.subscription_name_for_plan(plan)
        assert billing_svc.plan_from_subscription_name(name) == plan


def test_plan_from_subscription_name_variants():
    assert billing_svc.plan_from_subscription_name("Simple Returns Starter") == Plan.STARTER
    assert billing_svc.plan_from_subscription_name("simple returns growth") == Plan.GROWTH
    assert billing_svc.plan_from_subscription_name("Simple Returns Free") == Plan.FREE
    assert billing_svc.plan_from_subscription_name("Something Else") is None
    assert billing_svc.plan_from_subscription_name("") is None


def test_is_subscription_active():
    assert billing_svc.is_subscription_active(_sub(status="ACTIVE"))
    assert billing_svc.is_subscription_active(_sub(status="TRIALING"))
    assert not billing_svc.is_subscription_active(_sub(status="CANCELLED"))
    assert not billing_svc.is_subscription_active(_sub(status="PENDING"))


# -- sync logic -------------------------------------------------------------


def test_sync_active_subscription_sets_plan(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub())
    assert plan == Plan.STARTER
    assert shop.plan == Plan.STARTER
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/1"
    assert shop.subscription_status == "ACTIVE"


def test_sync_growth_subscription(db, shop):
    plan = billing_svc.sync_subscription_to_shop(
        db, shop, _sub(name="Simple Returns Growth")
    )
    assert plan == Plan.GROWTH
    assert shop.plan == Plan.GROWTH


def test_sync_cancelled_subscription_downgrades_to_free(db, shop):
    shop.plan = Plan.GROWTH
    shop.app_subscription_id = "gid://shopify/AppSubscription/1"
    db.commit()
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status="CANCELLED"))
    assert plan == Plan.FREE
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "DECLINED", "FROZEN"])
def test_sync_terminal_statuses_downgrade(db, shop, status):
    plan = billing_svc.sync_subscription_to_shop(db, shop, _sub(status=status))
    assert plan == Plan.FREE
    assert shop.plan == Plan.FREE


def test_sync_none_subscription_means_free(db, shop):
    plan = billing_svc.sync_subscription_to_shop(db, shop, None)
    assert plan == Plan.FREE
    assert shop.plan == Plan.FREE


def test_sync_unknown_name_falls_back_to_free(db, shop):
    plan = billing_svc.sync_subscription_to_shop(
        db, shop, _sub(name="Mystery Plan")
    )
    assert plan == Plan.FREE


def test_downgrade_to_free(db, shop):
    shop.plan = Plan.STARTER
    shop.app_subscription_id = "gid://shopify/AppSubscription/9"
    shop.subscription_status = "ACTIVE"
    db.commit()
    billing_svc.downgrade_to_free(db, shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None
    assert shop.subscription_status is None


# -- webhook handler --------------------------------------------------------


def test_webhook_app_subscriptions_update_active(db, shop):
    handle_app_subscriptions_update(
        db,
        shop,
        {
            "app_subscription": {
                "id": "gid://shopify/AppSubscription/5",
                "name": "Simple Returns Growth",
                "status": "ACTIVE",
                "trial_days": 14,
            }
        },
    )
    assert shop.plan == Plan.GROWTH
    assert shop.app_subscription_id == "gid://shopify/AppSubscription/5"


def test_webhook_app_subscriptions_update_cancelled(db, shop):
    shop.plan = Plan.STARTER
    db.commit()
    handle_app_subscriptions_update(
        db,
        shop,
        {
            "app_subscription": {
                "id": "gid://shopify/AppSubscription/5",
                "name": "Simple Returns Starter",
                "status": "CANCELLED",
            }
        },
    )
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None


def test_webhook_app_subscriptions_update_missing_payload(db, shop):
    # Missing app_subscription key -> treated as no subscription -> Free.
    handle_app_subscriptions_update(db, shop, {})
    assert shop.plan == Plan.FREE


# -- mock billing client ----------------------------------------------------


@pytest.mark.asyncio
async def test_mock_create_and_list_subscriptions():
    client = MockShopifyClient()
    assert await client.get_active_app_subscriptions("x.myshopify.com") == []
    sid, url = await client.create_app_subscription(
        "x.myshopify.com",
        name="Simple Returns Starter",
        price_cents=1200,
        currency="USD",
        trial_days=14,
        return_url="https://example.com/callback",
    )
    assert sid.startswith("gid://shopify/AppSubscription/")
    assert "example.com/callback" in url
    # Pending until the merchant approves.
    assert await client.get_active_app_subscriptions("x.myshopify.com") != []
    client.approve_mock_subscription(sid)
    subs = await client.get_active_app_subscriptions("x.myshopify.com")
    assert len(subs) == 1
    assert subs[0].status == "ACTIVE"


@pytest.mark.asyncio
async def test_mock_cancel_subscription():
    client = MockShopifyClient()
    sid, _ = await client.create_app_subscription(
        "x.myshopify.com", "Simple Returns Growth", 2400, "USD", 14,
        "https://example.com/callback",
    )
    assert await client.cancel_app_subscription("x.myshopify.com", sid) is True
    assert await client.get_active_app_subscriptions("x.myshopify.com") == []
    with pytest.raises(ShopifyError):
        await client.cancel_app_subscription("x.myshopify.com", "gid://shopify/AppSubscription/999")


@pytest.mark.asyncio
async def test_current_subscription_picks_active():
    client = MockShopifyClient()
    assert await billing_svc.current_subscription(client, "x.myshopify.com") is None
    sid, _ = await client.create_app_subscription(
        "x.myshopify.com", "Simple Returns Starter", 1200, "USD", 14,
        "https://example.com/callback",
    )
    client.approve_mock_subscription(sid)
    sub = await billing_svc.current_subscription(client, "x.myshopify.com")
    assert sub is not None
    assert sub.name == "Simple Returns Starter"


# -- plan limit enforcement --------------------------------------------------


def _set_usage(db, shop, n):
    from datetime import datetime, timezone

    month = f"{datetime.now(timezone.utc).year:04d}-{datetime.now(timezone.utc).month:02d}"
    usage = db.query(PlanUsage).filter_by(shop_id=shop.id, month=month).one_or_none()
    if usage is None:
        usage = PlanUsage(shop_id=shop.id, month=month, returns_count=n)
        db.add(usage)
    else:
        usage.returns_count = n
    db.commit()


def test_free_plan_limit_allows_10(db, shop, service):
    from app.services import PlanLimitExceeded

    shop.plan = Plan.FREE
    db.commit()
    _set_usage(db, shop, 9)
    service.check_plan_limit(shop)  # 9 < 10 -> ok
    _set_usage(db, shop, 10)
    with pytest.raises(PlanLimitExceeded):
        service.check_plan_limit(shop)


def test_growth_plan_unlimited(db, shop, service):
    shop.plan = Plan.GROWTH
    db.commit()
    _set_usage(db, shop, 10000)
    service.check_plan_limit(shop)  # no raise


# -- routes ------------------------------------------------------------------


def test_billing_page_renders(client, shop):
    resp = client.get("/app/billing?shop=test.myshopify.com")
    assert resp.status_code == 200
    assert "Free" in resp.text
    assert "Starter" in resp.text
    assert "Growth" in resp.text
    assert "14-day free trial" in resp.text


def test_billing_subscribe_mock_changes_plan(client, shop, db):
    from app.models import Shop

    resp = client.post(
        "/app/billing/subscribe?shop=test.myshopify.com",
        data={"plan": "growth"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    shop = db.query(Shop).filter_by(shop_domain="test.myshopify.com").one()
    assert shop.plan == Plan.GROWTH


def test_billing_subscribe_free_downgrades(client, shop, db):
    from app.models import Shop

    shop = db.query(Shop).filter_by(shop_domain="test.myshopify.com").one()
    shop.plan = Plan.STARTER
    shop.app_subscription_id = "gid://shopify/AppSubscription/1"
    db.commit()
    resp = client.post(
        "/app/billing/subscribe?shop=test.myshopify.com",
        data={"plan": "free"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.FREE
    assert shop.app_subscription_id is None


def test_billing_subscribe_unknown_plan_rejected(client, shop):
    resp = client.post(
        "/app/billing/subscribe?shop=test.myshopify.com",
        data={"plan": "enterprise"},
    )
    assert resp.status_code == 400


async def test_billing_callback_syncs_plan(client, db, shop):
    from app.deps import _shopify_client
    from app.models import Shop

    mock_client = _shopify_client()
    mock_client.subscriptions.clear()
    sid, _ = await mock_client.create_app_subscription(
        "test.myshopify.com",
        "Simple Returns Growth",
        2400,
        "USD",
        14,
        "https://example.com/callback",
    )
    mock_client.approve_mock_subscription(sid)
    resp = client.get(
        "/app/billing/callback?shop=test.myshopify.com", follow_redirects=False
    )
    assert resp.status_code == 303
    shop = db.query(Shop).filter_by(shop_domain="test.myshopify.com").one()
    assert shop.plan == Plan.GROWTH
    assert shop.app_subscription_id == sid


async def test_billing_cancel_downgrades_to_free(client, db, shop):
    from app.deps import _shopify_client
    from app.models import Shop

    mock_client = _shopify_client()
    sid, _ = await mock_client.create_app_subscription(
        "test.myshopify.com",
        "Simple Returns Starter",
        1200,
        "USD",
        14,
        "https://example.com/callback",
    )
    mock_client.approve_mock_subscription(sid)
    shop = db.query(Shop).filter_by(shop_domain="test.myshopify.com").one()
    shop.plan = Plan.STARTER
    shop.app_subscription_id = sid
    db.commit()
    resp = client.post(
        "/app/billing/cancel?shop=test.myshopify.com", follow_redirects=False
    )
    assert resp.status_code == 303
    db.refresh(shop)
    assert shop.plan == Plan.FREE
