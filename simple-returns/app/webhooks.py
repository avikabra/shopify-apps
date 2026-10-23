"""Shopify webhook handlers.

Called from the /webhooks/{topic} route AFTER HMAC verification. Handlers are
idempotent: every mutation checks current state before writing, and every
payload is logged to WebhookEvent for audit.

Note: approvals we create ourselves also fire returns/approve, so handlers
must tolerate events for requests already in the target state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import billing as billing_svc
from .models import ACTIVE_SUBSCRIPTION_STATUSES, ReturnRequest, ReturnStatus, Shop
from .shopify.protocol import AppSubscription

log = logging.getLogger("simple_returns.webhooks")


def _find_by_shopify_return_id(db: Session, shop_id: int, gid: str) -> ReturnRequest | None:
    return (
        db.query(ReturnRequest)
        .filter_by(shop_id=shop_id, shopify_return_id=gid)
        .one_or_none()
    )


def handle_app_uninstalled(db: Session, shop: Shop, payload: dict) -> None:
    shop.uninstalled_at = datetime.now(timezone.utc)
    db.commit()
    log.info("App uninstalled for shop %s", shop.shop_domain)


def handle_returns_request(db: Session, shop: Shop, payload: dict) -> None:
    # A return created outside our app (e.g. Shopify admin). We log it but do
    # not import it: our dashboard owns requests submitted via the portal.
    log.info("External return request on shop %s: %s", shop.shop_domain, payload.get("id"))


def handle_returns_approve(db: Session, shop: Shop, payload: dict) -> None:
    req = _find_by_shopify_return_id(db, shop.id, payload.get("id") or "")
    if req and req.status == ReturnStatus.PENDING_REVIEW:
        req.status = ReturnStatus.APPROVED
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = "shopify-admin"
        db.commit()


def handle_returns_decline(db: Session, shop: Shop, payload: dict) -> None:
    req = _find_by_shopify_return_id(db, shop.id, payload.get("id") or "")
    if req and req.status in (ReturnStatus.PENDING_REVIEW, ReturnStatus.REQUESTED):
        req.status = ReturnStatus.REJECTED
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = "shopify-admin"
        db.commit()


def handle_returns_close(db: Session, shop: Shop, payload: dict) -> None:
    req = _find_by_shopify_return_id(db, shop.id, payload.get("id") or "")
    if req and req.status in (
        ReturnStatus.REFUNDED,
        ReturnStatus.STORE_CREDITED,
        ReturnStatus.EXCHANGED,
    ):
        req.status = ReturnStatus.CLOSED
        db.commit()


def handle_refunds_create(db: Session, shop: Shop, payload: dict) -> None:
    # Refunds issued in Shopify admin against an order with a settled request:
    # just log; our own refunds are tracked via refund_id on settle().
    log.info("Refund created on shop %s: %s", shop.shop_domain, payload.get("id"))


def handle_customers_data_request(db: Session, shop: Shop, payload: dict) -> dict:
    """GDPR: return the customer data we hold. (Mandatory topic.)"""
    customer_id = (payload.get("customer") or {}).get("id")
    email = payload.get("email")
    data = []
    if customer_id or email:
        q = db.query(ReturnRequest).filter_by(shop_id=shop.id)
        if email:
            q = q.filter(ReturnRequest.customer_email == email)
        for r in q.all():
            data.append(
                {
                    "return_request_id": r.id,
                    "order_name": r.order_name,
                    "status": r.status.value,
                    "items": r.items,
                }
            )
    return {"customer_id": customer_id, "email": email, "return_requests": data}


def handle_customers_redact(db: Session, shop: Shop, payload: dict) -> None:
    """GDPR: erase PII for a customer. (Mandatory topic.)"""
    email = payload.get("email")
    customer_id = (payload.get("customer") or {}).get("id")
    if not email and not customer_id:
        return
    q = db.query(ReturnRequest).filter_by(shop_id=shop.id)
    if email:
        q = q.filter(ReturnRequest.customer_email == email)
    for r in q.all():
        r.customer_email = "redacted"
        r.customer_name = None
        r.customer_id = None
    db.commit()


def handle_app_subscriptions_update(db: Session, shop: Shop, payload: dict) -> None:
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
    billing_svc.sync_subscription_to_shop(db, shop, sub)


def handle_shop_redact(db: Session, shop: Shop, payload: dict) -> None:
    """GDPR: erase everything for a shop. (Mandatory topic.)"""
    for r in db.query(ReturnRequest).filter_by(shop_id=shop.id).all():
        db.delete(r)
    shop.access_token_enc = b""
    shop.uninstalled_at = datetime.now(timezone.utc)
    db.commit()


HANDLERS = {
    "app/uninstalled": handle_app_uninstalled,
    "app_subscriptions/update": handle_app_subscriptions_update,
    "returns/request": handle_returns_request,
    "returns/approve": handle_returns_approve,
    "returns/decline": handle_returns_decline,
    "returns/close": handle_returns_close,
    "returns/cancel": handle_returns_decline,
    "returns/reopen": handle_returns_request,
    "returns/process": handle_returns_request,
    "returns/update": handle_returns_request,
    "refunds/create": handle_refunds_create,
    "customers/data_request": handle_customers_data_request,
    "customers/redact": handle_customers_redact,
    "shop/redact": handle_shop_redact,
}
