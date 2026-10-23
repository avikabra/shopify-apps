"""Shopify Billing API wiring (recurring app subscriptions).

Freemium plans:
  Free    $0/mo   10 returns/mo   customer portal, basic return rules
  Starter $12/mo  50 returns/mo   + refunds, labels, status emails (14-day trial)
  Growth  $24/mo  unlimited       + exchanges, priority support (14-day trial)

Flow (production):
  1. Merchant picks a paid plan -> POST /app/billing/subscribe
  2. We call appSubscriptionCreate -> 303 redirect to Shopify's
     confirmationUrl -> merchant approves
  3. Shopify redirects back to /app/billing/callback -> we query
     currentAppInstallation.activeSubscriptions and sync the shop record
  4. app_subscriptions/update webhook keeps the record fresh afterwards

The Free plan has no Shopify charge: downgrading cancels any active
subscription and records Plan.FREE locally.

In mock/dev mode the Shopify calls are simulated by MockShopifyClient.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import ACTIVE_SUBSCRIPTION_STATUSES, Plan, Shop
from .shopify.protocol import AppSubscription

log = logging.getLogger("simple_returns.billing")

APP_NAME = "Simple Returns"

# Plan -> Shopify subscription display name. Webhook/callback payloads are
# matched back to plans by keyword, so keep the plan key in the name.
SUBSCRIPTION_NAMES = {
    Plan.FREE: f"{APP_NAME} Free",
    Plan.STARTER: f"{APP_NAME} Starter",
    Plan.GROWTH: f"{APP_NAME} Growth",
}

TRIAL_DAYS = {
    Plan.FREE: 0,
    Plan.STARTER: 14,
    Plan.GROWTH: 14,
}

PLAN_FEATURES = {
    Plan.FREE: [
        "Branded customer returns portal",
        "Configurable return rules",
        "10 returns / month",
    ],
    Plan.STARTER: [
        "Everything in Free",
        "50 returns / month",
        "Refunds to original payment method",
        "Prepaid return labels",
        "Automated status emails",
        "14-day free trial",
    ],
    Plan.GROWTH: [
        "Everything in Starter",
        "Unlimited returns",
        "Exchanges via draft orders",
        "Store credit via gift cards",
        "Priority support",
        "14-day free trial",
    ],
}

PLAN_ORDER = [Plan.FREE, Plan.STARTER, Plan.GROWTH]

# Subscription statuses that mean the merchant is NOT paying anymore.
TERMINAL_SUBSCRIPTION_STATUSES = {
    "CANCELLED",
    "EXPIRED",
    "DECLINED",
    "FROZEN",
}


def subscription_name_for_plan(plan: Plan) -> str:
    return SUBSCRIPTION_NAMES[plan]


def plan_from_subscription_name(name: str) -> Plan | None:
    """Map a Shopify subscription name back to our Plan.

    Matches on the plan keyword so renames of the app prefix don't break it.
    """
    lowered = (name or "").lower()
    for plan in PLAN_ORDER:
        if plan.value in lowered:
            return plan
    return None


def is_subscription_active(sub: AppSubscription) -> bool:
    return sub.status in ACTIVE_SUBSCRIPTION_STATUSES


def sync_subscription_to_shop(
    db: Session, shop: Shop, sub: AppSubscription | None
) -> Plan:
    """Persist a Shopify subscription state onto the shop record.

    Returns the resulting plan. A missing/cancelled subscription means Free.
    """
    if sub is None or sub.status in TERMINAL_SUBSCRIPTION_STATUSES:
        shop.plan = Plan.FREE
        shop.app_subscription_id = None
        shop.subscription_status = sub.status if sub else None
        db.commit()
        return Plan.FREE

    plan = plan_from_subscription_name(sub.name) or Plan.FREE
    shop.plan = plan
    shop.app_subscription_id = sub.id
    shop.subscription_status = sub.status
    if sub.trial_days and not shop.trial_ends_at:
        shop.trial_ends_at = datetime.now(timezone.utc)
    db.commit()
    log.info(
        "Billing synced for shop %s: plan=%s sub=%s status=%s",
        shop.shop_domain,
        plan.value,
        sub.id,
        sub.status,
    )
    return plan


async def current_subscription(client, shop_domain: str) -> AppSubscription | None:
    """Return the shop's active subscription, if any."""
    subs = await client.get_active_app_subscriptions(shop_domain)
    for sub in subs:
        if is_subscription_active(sub):
            return sub
    return subs[0] if subs else None


async def sync_shop_billing(db: Session, shop: Shop, client) -> Plan:
    """Query Shopify for the current subscription and persist it."""
    sub = await current_subscription(client, shop.shop_domain)
    return sync_subscription_to_shop(db, shop, sub)


def downgrade_to_free(db: Session, shop: Shop) -> None:
    """Record a downgrade to the Free plan (no Shopify charge)."""
    shop.plan = Plan.FREE
    shop.app_subscription_id = None
    shop.subscription_status = None
    db.commit()
    log.info("Shop %s downgraded to Free plan", shop.shop_domain)
