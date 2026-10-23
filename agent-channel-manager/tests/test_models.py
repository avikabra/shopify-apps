"""Tests for plan limits and model defaults."""
from app.models import (
    PLAN_LIMITS,
    ChannelMode,
    Plan,
    Shop,
)


def test_plan_limits_free():
    limits = PLAN_LIMITS[Plan.FREE]
    assert limits["price_cents"] == 0
    assert limits["visibility_rules"] == 10
    assert limits["pricing_rules"] == 2
    assert limits["promotions"] == 1


def test_plan_limits_pro_unlimited():
    limits = PLAN_LIMITS[Plan.PRO]
    assert limits["price_cents"] == 1900
    assert limits["visibility_rules"] is None
    assert limits["pricing_rules"] is None
    assert limits["promotions"] is None


def test_shop_defaults(db):
    shop = Shop(shop_domain="d.myshopify.com", access_token_enc=b"x")
    db.add(shop)
    db.commit()
    db.refresh(shop)
    assert shop.plan == Plan.FREE
    assert shop.channel_mode == ChannelMode.BLOCKLIST
