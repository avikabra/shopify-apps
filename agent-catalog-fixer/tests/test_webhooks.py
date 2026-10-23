"""Tests for webhook receivers."""
import base64
import hashlib
import hmac
import json

from app.config import get_settings


def _signed(body: bytes, topic: str, shop: str = "test.myshopify.com"):
    secret = get_settings().shopify_api_secret
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return {
        "X-Shopify-Hmac-Sha256": base64.b64encode(digest).decode(),
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": shop,
        "Content-Type": "application/json",
    }


def test_products_update_rescores(client, shop, db):
    from app.models import ProductAudit
    from app.services import CatalogService
    from app.shopify.mock import MockShopifyClient

    # Seed an audit first via the service layer.
    assert db.query(ProductAudit).filter_by(shop_id=shop.id).count() == 0
    body = json.dumps({"id": 101}).encode()
    # Override the deps' mock singleton by pre-seeding through the real path:
    # the webhook uses shopify_client_for(shop) (mock singleton); rescore
    # fetches product 101 from fixture data and stores the audit.
    r = client.post("/webhooks", content=body, headers=_signed(body, "products/update"))
    assert r.status_code == 200
    assert db.query(ProductAudit).filter_by(shop_id=shop.id, product_id="101").count() == 1


def test_products_update_unknown_shop_ok(client, db):
    body = json.dumps({"id": 101}).encode()
    r = client.post(
        "/webhooks", content=body,
        headers=_signed(body, "products/update", shop="nobody.myshopify.com"),
    )
    assert r.status_code == 200


def test_app_uninstalled_marks_shop(client, shop, db):
    body = json.dumps({}).encode()
    r = client.post("/webhooks", content=body, headers=_signed(body, "app/uninstalled"))
    assert r.status_code == 200
    db.refresh(shop)
    assert shop.uninstalled_at is not None


def test_bad_hmac_rejected(client):
    body = json.dumps({"id": 1}).encode()
    headers = _signed(body, "products/update")
    headers["X-Shopify-Hmac-Sha256"] = "bogus"
    r = client.post("/webhooks", content=body, headers=headers)
    assert r.status_code == 401


def test_gdpr_topics_acknowledged(client):
    for topic in ("customers/data_request", "customers/redact", "shop/redact"):
        body = json.dumps({}).encode()
        r = client.post("/webhooks", content=body, headers=_signed(body, topic))
        assert r.status_code == 200
        assert r.json()["ok"] is True
