"""Webhook tests: HMAC at the route boundary + handler behavior."""
import base64
import hashlib
import hmac
import json

import pytest

from app.config import get_settings
from app.models import ReturnRequest, ReturnStatus, Shop, WebhookEvent


def signed_post(client, topic, payload, shop_domain="test.myshopify.com", secret=None):
    settings = get_settings()
    raw = json.dumps(payload).encode()
    digest = hmac.new(
        (secret or settings.shopify_api_secret).encode(), raw, hashlib.sha256
    ).digest()
    return client.post(
        f"/webhooks/{topic}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": base64.b64encode(digest).decode(),
            "X-Shopify-Shop-Domain": shop_domain,
            "X-Shopify-Topic": topic.replace("/", "_"),
        },
    )


@pytest.fixture()
def seeded_request(db, shop):
    req = ReturnRequest(
        shop_id=shop.id,
        order_id="gid://shopify/Order/1",
        order_name="#1001",
        customer_email="customer@example.com",
        status=ReturnStatus.PENDING_REVIEW,
        resolution="refund",
        items=[],
        currency="USD",
        idempotency_key="test-key-1",
        shopify_return_id="gid://shopify/Return/999",
    )
    db.add(req)
    db.commit()
    return req


def test_webhook_rejects_bad_hmac(client, db):
    resp = signed_post(client, "app/uninstalled", {}, secret="wrong-secret")
    assert resp.status_code == 401
    assert db.query(WebhookEvent).count() == 0


def test_app_uninstalled_marks_shop(client, db, shop, seeded_request):
    resp = signed_post(client, "app/uninstalled", {"id": 1})
    assert resp.status_code == 200
    db.refresh(shop)
    assert shop.uninstalled_at is not None


def test_returns_approve_syncs_pending_request(client, db, shop, seeded_request):
    resp = signed_post(client, "returns/approve", {"id": "gid://shopify/Return/999"})
    assert resp.status_code == 200
    db.refresh(seeded_request)
    assert seeded_request.status == ReturnStatus.APPROVED
    assert seeded_request.decided_by == "shopify-admin"


def test_returns_approve_is_idempotent_for_unknown_return(client, db, shop):
    resp = signed_post(client, "returns/approve", {"id": "gid://shopify/Return/12345"})
    assert resp.status_code == 200  # logged, not an error


def test_returns_decline_syncs(client, db, shop, seeded_request):
    resp = signed_post(client, "returns/decline", {"id": "gid://shopify/Return/999"})
    assert resp.status_code == 200
    db.refresh(seeded_request)
    assert seeded_request.status == ReturnStatus.REJECTED


def test_every_delivery_is_logged(client, db, shop):
    signed_post(client, "refunds/create", {"id": "gid://shopify/Refund/5"})
    event = db.query(WebhookEvent).one()
    assert event.topic == "refunds/create"
    assert event.shop_id == shop.id
    assert event.payload["id"] == "gid://shopify/Refund/5"


def test_unknown_topic_returns_ok_unhandled(client, db, shop):
    resp = signed_post(client, "products/create", {"id": 1})
    assert resp.status_code == 200
    assert resp.json()["handled"] is False


def test_customers_redact_anonymizes(db, shop, seeded_request):
    from app import webhooks

    webhooks.handle_customers_redact(
        db, shop, {"email": "customer@example.com", "customer": {"id": 7}}
    )
    db.refresh(seeded_request)
    assert seeded_request.customer_email == "redacted"
    assert seeded_request.customer_name is None


def test_customers_data_request_returns_owned_data(db, shop, seeded_request):
    from app import webhooks

    result = webhooks.handle_customers_data_request(
        db, shop, {"email": "customer@example.com", "customer": {"id": 7}}
    )
    assert len(result["return_requests"]) == 1
    assert result["return_requests"][0]["order_name"] == "#1001"


def test_shop_redact_wipes_shop_data(db, shop, seeded_request):
    from app import webhooks

    webhooks.handle_shop_redact(db, shop, {"shop_domain": "test.myshopify.com"})
    assert db.query(ReturnRequest).count() == 0
    db.refresh(shop)
    assert shop.access_token_enc == b""
