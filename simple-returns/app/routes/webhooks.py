"""Shopify webhook receiver.

POST /webhooks/{topic} — Shopify posts here with:
  X-Shopify-Hmac-Sha256, X-Shopify-Shop-Domain, X-Shopify-Topic headers.
We verify HMAC (constant-time), log to WebhookEvent, dispatch to webhooks.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import security, webhooks
from ..config import Settings, get_settings
from ..deps import get_db
from ..models import Shop, WebhookEvent

log = logging.getLogger("simple_returns.routes.webhooks")

router = APIRouter()


@router.post("/webhooks/{topic:path}")
async def receive_webhook(
    topic: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raw = await request.body()
    hmac_header = request.headers.get("x-shopify-hmac-sha256", "")
    if not security.verify_webhook_hmac(raw, hmac_header, settings.shopify_api_secret):
        raise HTTPException(401, "Invalid webhook HMAC")

    shop_domain = request.headers.get("x-shopify-shop-domain", "")
    topic_header = request.headers.get("x-shopify-topic", "") or topic.replace("/", "_")
    canonical = topic_header.lower().replace("_", "/")

    shop = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()

    try:
        payload = await request.json()
    except ValueError:
        payload = {}

    db.add(WebhookEvent(shop_id=shop.id if shop else None, topic=canonical, payload=payload))
    db.commit()

    handler = webhooks.HANDLERS.get(canonical)
    if handler is None:
        log.warning("No handler for webhook topic %s", canonical)
        return JSONResponse({"ok": True, "handled": False})
    if shop is None:
        log.warning("Webhook for unknown shop %s", shop_domain)
        return JSONResponse({"ok": True, "handled": False})

    result = handler(db, shop, payload if isinstance(payload, dict) else {})
    if isinstance(result, dict):
        return JSONResponse({"ok": True, **result})
    return JSONResponse({"ok": True})
