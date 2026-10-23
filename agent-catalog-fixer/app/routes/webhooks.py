"""Webhook receivers: HMAC-verified, minimal processing."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .. import security
from ..config import Settings
from ..deps import get_db, get_settings_dep, shopify_client_for
from ..models import Shop
from ..services import CatalogService

router = APIRouter()


def _shop_from_domain(db: Session, domain: str) -> Shop | None:
    return db.query(Shop).filter_by(shop_domain=domain).one_or_none()


def handle_app_subscriptions_update(db: Session, shop: Shop, payload: dict) -> None:
    """Sync the shop's plan from a Shopify app subscription change.

    Payload shape: {"app_subscription": {"id": gid, "name": str,
    "status": "ACTIVE"|"CANCELLED"|..., "trial_days": int}}.
    A terminal status (cancelled/expired/declined/frozen) drops the shop to
    the Free plan; anything else maps the subscription name back to a plan.
    Idempotent: re-applying the same state is a no-op.
    """
    from .. import billing as billing_svc
    from ..shopify.client import AppSubscription

    raw = payload.get("app_subscription") or {}
    sub = AppSubscription(
        id=raw.get("id") or "",
        name=raw.get("name") or "",
        status=(raw.get("status") or "").upper(),
        trial_days=raw.get("trial_days") or 0,
    )
    billing_svc.sync_subscription_to_shop(db, shop, sub)


@router.post("/webhooks")
async def webhooks(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    hmac_header: str | None = Header(default=None, alias="X-Shopify-Hmac-Sha256"),
    topic: str | None = Header(default=None, alias="X-Shopify-Topic"),
    shop_domain: str | None = Header(default=None, alias="X-Shopify-Shop-Domain"),
):
    raw = await request.body()
    if not security.verify_webhook_hmac(raw, hmac_header, settings.shopify_api_secret):
        # In mock mode without a secret, accept (dev convenience).
        if not (settings.use_mock_shopify and not settings.shopify_api_secret):
            raise HTTPException(401, "Invalid webhook HMAC")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        payload = {}

    if topic == "app/uninstalled":
        shop = _shop_from_domain(db, shop_domain or "")
        if shop:
            from datetime import datetime, timezone

            shop.uninstalled_at = datetime.now(timezone.utc)
            db.commit()
        return {"ok": True}

    if topic in ("products/update", "products/create"):
        # Continuous monitoring: re-score the changed product.
        shop = _shop_from_domain(db, shop_domain or "")
        product_id = str(payload.get("id") or "")
        if shop and product_id and not shop.uninstalled_at:
            try:
                service = CatalogService(db, shopify_client_for(shop), settings)
                service.rescore_product(shop, product_id)
            except Exception:  # noqa: BLE001 - a dead token (e.g. a shop that
                # uninstalled) must not turn webhooks into 500s; Shopify
                # retries 5xx deliveries for days.
                pass
        return {"ok": True}

    if topic == "app_subscriptions/update":
        # Keep the shop's plan in sync with the Shopify subscription.
        shop = _shop_from_domain(db, shop_domain or "")
        if shop and not shop.uninstalled_at:
            handle_app_subscriptions_update(db, shop, payload)
        return {"ok": True}

    if topic in ("customers/data_request", "customers/redact", "shop/redact"):
        # No customer data is stored by this app; acknowledge.
        return {"ok": True}

    return {"ok": True, "ignored_topic": topic}
