"""Route tests: customer portal API + merchant admin pages."""
from app.models import ReturnStatus


def portal(client, path, body, origin="https://test.myshopify.com"):
    return client.post(
        f"/portal/{path}",
        json=body,
        headers={"Origin": origin} if origin else {},
    )


def test_portal_lookup_success(client, shop):
    resp = portal(client, "lookup", {
        "shop": "test.myshopify.com",
        "order_name": "1001",
        "email": "customer@example.com",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["order_name"] == "#1001"
    assert len(data["items"]) == 2
    eligible = [i for i in data["items"] if i["eligible"]]
    assert len(eligible) == 2


def test_portal_lookup_wrong_email_is_404(client, shop):
    resp = portal(client, "lookup", {
        "shop": "test.myshopify.com",
        "order_name": "#1001",
        "email": "stranger@example.com",
    })
    assert resp.status_code == 404


def test_portal_lookup_unknown_order_is_404(client, shop):
    resp = portal(client, "lookup", {
        "shop": "test.myshopify.com",
        "order_name": "#9999",
        "email": "customer@example.com",
    })
    assert resp.status_code == 404


def test_portal_origin_check_allows_shop_domain_only():
    """Unit-test the origin gate with production (non-mock) settings."""
    from starlette.requests import Request

    from app.config import get_settings
    from app.models import Shop
    from app.routes.portal import check_portal_origin

    real = get_settings().model_copy(update={"shopify_client_mode": "real"})
    shop = Shop(shop_domain="test.myshopify.com", access_token_enc=b"x",
                settings={"custom_domains": "shop.example.com"})

    def req_with_origin(origin):
        return Request({"type": "http", "headers": [(b"origin", origin.encode())]})

    # Own myshopify domain: allowed.
    check_portal_origin(req_with_origin("https://test.myshopify.com"), shop, real)
    # Listed custom domain: allowed.
    check_portal_origin(req_with_origin("https://shop.example.com"), shop, real)
    # Unrelated origin: blocked.
    from fastapi import HTTPException

    try:
        check_portal_origin(req_with_origin("https://evil.example"), shop, real)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("expected 403 for disallowed origin")
    # Mock mode: permissive (dev only).
    mock = get_settings().model_copy(update={"shopify_client_mode": "mock"})
    check_portal_origin(req_with_origin("https://anything.example"), shop, mock)


def test_portal_submit_refund_auto_approved(client, shop, db):
    from app.models import ReturnRequest

    lookup = portal(client, "lookup", {
        "shop": "test.myshopify.com",
        "order_name": "#1001",
        "email": "customer@example.com",
    }).json()
    li = lookup["items"][0]["line_item_id"]
    resp = portal(client, "submit", {
        "shop": "test.myshopify.com",
        "order_name": "#1001",
        "email": "customer@example.com",
        "resolution": "refund",
        "items": [{"line_item_id": li, "quantity": 1, "reason": "DEFECTIVE"}],
    })
    assert resp.status_code == 200, resp.text
    req = db.query(ReturnRequest).one()
    assert req.status == ReturnStatus.LABEL_CREATED
    assert req.tracking_number


def test_portal_submit_bad_quantity_is_400(client, shop):
    lookup = portal(client, "lookup", {
        "shop": "test.myshopify.com",
        "order_name": "#1001",
        "email": "customer@example.com",
    }).json()
    li = lookup["items"][0]["line_item_id"]
    resp = portal(client, "submit", {
        "shop": "test.myshopify.com",
        "order_name": "#1001",
        "email": "customer@example.com",
        "resolution": "refund",
        "items": [{"line_item_id": li, "quantity": 99, "reason": "DEFECTIVE"}],
    })
    assert resp.status_code == 400


def test_portal_variants_for_exchange(client, shop):
    resp = portal(client, "variants", {
        "shop": "test.myshopify.com",
        "product_id": "gid://shopify/Product/21",
    })
    assert resp.status_code == 200
    assert len(resp.json()["variants"]) == 3


def test_portal_reasons_list(client):
    resp = client.get("/portal/reasons")
    assert resp.status_code == 200
    values = [r["value"] for r in resp.json()["reasons"]]
    assert "DEFECTIVE" in values


# -- admin ---------------------------------------------------------------

def test_admin_dashboard_renders(client, shop):
    resp = client.get("/app?shop=test.myshopify.com")
    assert resp.status_code == 200
    assert "Return requests" in resp.text


def test_admin_requires_shop(client):
    resp = client.get("/app")
    assert resp.status_code == 401


def test_admin_approve_flow(client, shop, db):
    from app.models import ReturnRequest

    # Submit via portal so it lands pending (make order old first via service-level).
    # Simpler: create a pending request directly.
    req = ReturnRequest(
        shop_id=shop.id,
        order_id="gid://shopify/Order/1",
        order_name="#1001",
        customer_email="customer@example.com",
        status=ReturnStatus.PENDING_REVIEW,
        resolution="refund",
        items=[{
            "line_item_id": "gid://shopify/LineItem/11",
            "fulfillment_line_item_id": "gid://shopify/FulfillmentLineItem/111",
            "title": "Test Hoodie",
            "quantity": 1,
            "unit_price": "49.99",
            "reason": "DEFECTIVE",
            "note": "",
            "product_tags": [],
        }],
        currency="USD",
        idempotency_key="admin-test-1",
    )
    db.add(req)
    db.commit()

    resp = client.post(f"/app/requests/{req.id}/approve?shop=test.myshopify.com",
                       follow_redirects=False)
    assert resp.status_code in (303, 307)
    db.refresh(req)
    assert req.status == ReturnStatus.LABEL_CREATED

    resp = client.post(
        f"/app/requests/{req.id}/settle?shop=test.myshopify.com",
        data={"restock": "yes"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 307)
    db.refresh(req)
    assert req.status == ReturnStatus.REFUNDED
    assert req.refund_id


def test_admin_settings_save(client, shop, db):
    resp = client.post(
        "/app/settings/general?shop=test.myshopify.com",
        data={
            "brand_name": "New Brand",
            "support_email": "help@example.com",
            "custom_domains": "",
            "wh_street1": "5 Warehouse Rd",
            "wh_street2": "",
            "wh_city": "Denver",
            "wh_state": "CO",
            "wh_zip": "80202",
            "wh_country": "US",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (303, 307)
    db.refresh(shop)
    assert shop.settings["brand_name"] == "New Brand"
    assert shop.settings["warehouse_address"]["zip"] == "80202"


def test_health(client):
    assert client.get("/health").json() == {"ok": True}
