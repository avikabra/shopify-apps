"""Tests for dashboard routes and JSON API."""
import json

SHOP_QS = "?shop=test.myshopify.com"
PRO_QS = "?shop=pro.myshopify.com"


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_root_redirects_to_app(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/app")


def test_dashboard_requires_shop(client):
    r = client.get("/app")
    assert r.status_code == 401


def test_dashboard_renders(client, shop):
    r = client.get(f"/app{SHOP_QS}")
    assert r.status_code == 200
    assert "Agent-Ready Catalog Fixer" in r.text
    assert "No products scored yet" in r.text


def test_dashboard_after_audit(client, shop, db, mock_shopify):
    from app.config import get_settings
    from app.services import CatalogService

    CatalogService(db=db, shopify=mock_shopify, settings=get_settings()).run_full_audit(shop)
    r = client.get(f"/app{SHOP_QS}")
    assert r.status_code == 200
    assert "Organic Cotton T-Shirt" in r.text
    assert "Widget" in r.text


def test_api_summary(client, shop):
    r = client.get(f"/api/summary{SHOP_QS}")
    data = r.json()
    assert data["shop"] == "test.myshopify.com"
    assert data["plan"] == "free"
    assert data["products_scored"] == 0


def test_api_run_audit(client, shop):
    r = client.post(f"/api/audit/run{SHOP_QS}")
    data = r.json()
    assert data["status"] == "done"
    assert data["products_scored"] == 3
    assert data["avg_score"] is not None


def test_api_products_filter(client, shop):
    client.post(f"/api/audit/run{SHOP_QS}")
    r = client.get(f"/api/products{SHOP_QS}&issue=missing_barcode")
    ids = {p["product_id"] for p in r.json()}
    assert ids == {"102", "103"}


def test_api_product_detail(client, shop):
    client.post(f"/api/audit/run{SHOP_QS}")
    r = client.get(f"/api/products/101{SHOP_QS}")
    data = r.json()
    assert data["title"] == "Organic Cotton T-Shirt"
    assert data["grade"] == "A"


def test_api_product_detail_404(client, shop):
    r = client.get(f"/api/products/999{SHOP_QS}")
    assert r.status_code == 404


def test_api_apply_fix(client, shop):
    client.post(f"/api/audit/run{SHOP_QS}")
    r = client.post(f"/api/products/102/fix{SHOP_QS}&fix_code=missing_alt_text")
    assert r.json()["applied"] is True


def test_api_apply_fix_bad_code(client, shop):
    r = client.post(f"/api/products/101/fix{SHOP_QS}&fix_code=bogus")
    assert r.json()["applied"] is False


def test_api_bulk_fix_free_rejected(client, shop):
    client.post(f"/api/audit/run{SHOP_QS}")
    r = client.post(
        f"/api/bulk-fix{SHOP_QS}",
        content=json.dumps({"fix_code": "missing_barcode", "product_ids": ["102"]}),
    )
    # Free plan is gated at the route with 402 Payment Required.
    assert r.status_code == 402
    assert "/app/billing" in r.json()["detail"]


def test_api_bulk_fix_pro(client, pro_shop):
    r = client.post(
        f"/api/bulk-fix{PRO_QS}",
        content=json.dumps({"fix_code": "missing_barcode", "product_ids": ["102"]}),
    )
    data = r.json()
    assert data["applied"] == 1


def test_api_faqs(client, shop):
    r = client.get(f"/api/products/103/faqs{SHOP_QS}")
    data = r.json()
    assert len(data["faqs"]) >= 2
    assert all("question" in f and "answer" in f for f in data["faqs"])


def test_api_plan(client, shop):
    r = client.get(f"/api/plan{SHOP_QS}")
    data = r.json()
    assert data["plan"] == "free"
    assert data["plans"]["pro"]["price"] == 19
    assert data["plans"]["free"]["bulk_fix"] is False
