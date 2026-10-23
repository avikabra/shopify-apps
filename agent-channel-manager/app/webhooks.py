"""Webhook handlers: app lifecycle + GDPR.

Registered topics (see routes/auth.py): app/uninstalled, shop/update.
GDPR topics (customers/data_request, customers/redact, shop/redact) are
handled even though not explicitly registered — Shopify sends them to every
registered callback URL pattern when a relevant event occurs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import billing as billing_svc
from .models import Shop
from .shopify.protocol import AppSubscription

log = logging.getLogger("agent_channel_manager.webhooks")


def _utcnow():
    return datetime.now(timezone.utc)


def handle_app_uninstalled(db: Session, shop: Shop, payload: dict) -> dict:
    shop.uninstalled_at = _utcnow()
    db.commit()
    log.info("Shop %s uninstalled; marked inactive.", shop.shop_domain)
    return {"ok": True, "action": "marked_uninstalled"}


def handle_shop_update(db: Session, shop: Shop, payload: dict) -> dict:
    # Keep the brand name fresh for the dashboard header.
    name = (payload.get("name") or "").strip()
    if name:
        settings = dict(shop.settings or {})
        settings["brand_name"] = name
        shop.settings = settings
        db.commit()
    return {"ok": True, "action": "shop_updated"}


def handle_app_subscriptions_update(db: Session, shop: Shop, payload: dict) -> dict:
    """Sync the shop's plan from a Shopify app subscription change.

    Payload shape: {"app_subscription": {"id": gid, "name": str,
    "status": "ACTIVE"|"CANCELLED"|..., "trial_days": int}}.
    A terminal status (cancelled/expired/declined/frozen) drops the shop to
    the Free plan; anything else maps the subscription name back to a plan.
    Idempotent: re-applying the same state is a no-op.
    """
    raw = payload.get("app_subscription") or {}
    sub = AppSubscription(
        id=raw.get("id") or "",
        name=raw.get("name") or "",
        status=(raw.get("status") or "").upper(),
        trial_days=raw.get("trial_days") or 0,
    )
    plan = billing_svc.sync_subscription_to_shop(db, shop, sub)
    return {"ok": True, "action": "billing_synced", "plan": plan.value}


def handle_customers_data_request(db: Session, shop: Shop, payload: dict) -> dict:
    # This app stores no customer PII — only shop-level channel config.
    log.info("GDPR data request for shop %s: no customer data held.", shop.shop_domain)
    return {"ok": True, "action": "no_customer_data"}


def handle_customers_redact(db: Session, shop: Shop, payload: dict) -> dict:
    log.info("GDPR customer redact for shop %s: nothing to redact.", shop.shop_domain)
    return {"ok": True, "action": "nothing_to_redact"}


def handle_shop_redact(db: Session, shop: Shop, payload: dict) -> dict:
    # 48h after uninstall Shopify asks us to erase shop data.
    for rel in ("visibility_rules", "pricing_rules", "promotions"):
        for obj in list(getattr(shop, rel)):
            db.delete(obj)
    db.delete(shop)
    db.commit()
    log.info("GDPR shop redact for %s: all data erased.", shop.shop_domain)
    return {"ok": True, "action": "shop_erased"}


HANDLERS = {
    "app/uninstalled": handle_app_uninstalled,
    "app_subscriptions/update": handle_app_subscriptions_update,
    "shop/update": handle_shop_update,
    "customers/data_request": handle_customers_data_request,
    "customers/redact": handle_customers_redact,
    "shop/redact": handle_shop_redact,
}
