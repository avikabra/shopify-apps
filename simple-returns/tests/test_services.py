"""Service-layer tests: full return lifecycle against mock adapters."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.easypost.mock import MockShippingProvider
from app.models import PlanUsage, Resolution, ReturnRequest, ReturnStatus
from app.services import PlanLimitExceeded, ReturnService


@pytest.fixture()
async def order_1001(mock_shopify):
    return await mock_shopify.get_order_for_return(
        "test.myshopify.com", "#1001", "customer@example.com"
    )


def item_payload(line_item_id, quantity=1, reason="DEFECTIVE", **kw):
    payload = {
        "line_item_id": line_item_id,
        "quantity": quantity,
        "reason": reason,
        "note": "",
    }
    payload.update(kw)
    return payload


async def test_submit_auto_approves_within_window(service, shop, order_1001, mock_shopify, mock_shipping, mock_email):
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(
        shop, order_1001, [item_payload(li)], resolution="refund"
    )
    assert req.status == ReturnStatus.LABEL_CREATED  # approved + label (mock shipping works)
    assert req.shopify_return_id is not None
    assert req.tracking_number.startswith("1Z999MOCK")
    assert req.label_url
    assert len(mock_shopify.created_returns) == 1
    assert len(mock_shipping.labels) == 1
    assert any("approved" in e["subject"].lower() or "label" in e["subject"].lower() for e in mock_email.sent)


async def test_submit_goes_to_review_outside_window(service, shop, order_1001, mock_email, db):
    order_1001.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(
        shop, order_1001, [item_payload(li)], resolution="refund"
    )
    assert req.status == ReturnStatus.PENDING_REVIEW
    assert req.shopify_return_id is None
    assert any("review" in e["subject"].lower() for e in mock_email.sent)


async def test_submit_excluded_tag_goes_to_review(service, shop, order_1001):
    cap = order_1001.line_items[1]  # tagged final-sale
    assert "final-sale" in cap.product_tags
    req = await service.submit_request(
        shop, order_1001, [item_payload(cap.line_item_id)], resolution="refund"
    )
    assert req.status == ReturnStatus.PENDING_REVIEW


async def test_submit_rejects_unfulfilled_item(service, shop, mock_shopify):
    order = await mock_shopify.get_order_for_return(
        "test.myshopify.com", "#1002", "customer@example.com"
    )
    li = order.line_items[0].line_item_id
    with pytest.raises(ValueError, match="hasn't shipped"):
        await service.submit_request(shop, order, [item_payload(li)], resolution="refund")


async def test_submit_rejects_bad_quantity(service, shop, order_1001):
    li = order_1001.line_items[0].line_item_id  # purchased qty 2
    with pytest.raises(ValueError, match="Invalid quantity"):
        await service.submit_request(shop, order_1001, [item_payload(li, quantity=5)], resolution="refund")


async def test_submit_rejects_bad_reason(service, shop, order_1001):
    li = order_1001.line_items[0].line_item_id
    with pytest.raises(ValueError, match="Unknown return reason"):
        await service.submit_request(shop, order_1001, [item_payload(li, reason="BOGUS")], resolution="refund")


async def test_plan_limit_blocks_new_requests(service, shop, order_1001, db):
    month = service._month_key()
    db.add(PlanUsage(shop_id=shop.id, month=month, returns_count=50))
    db.commit()
    li = order_1001.line_items[0].line_item_id
    with pytest.raises(PlanLimitExceeded):
        await service.submit_request(shop, order_1001, [item_payload(li)], resolution="refund")


async def test_manual_approve_then_reject_paths(service, shop, order_1001, mock_shopify, db):
    order_1001.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(shop, order_1001, [item_payload(li)], resolution="refund")
    assert req.status == ReturnStatus.PENDING_REVIEW

    req = await service.approve(req, decided_by="merchant")
    assert req.status == ReturnStatus.LABEL_CREATED
    assert req.shopify_return_id in mock_shopify.approved_returns

    # Approve is idempotent: second call changes nothing.
    n = len(mock_shopify.created_returns)
    req = await service.approve(req, decided_by="merchant")
    assert len(mock_shopify.created_returns) == n


async def test_reject_marks_rejected(service, shop, order_1001, mock_email):
    order_1001.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(shop, order_1001, [item_payload(li)], resolution="refund")
    req = await service.reject(req, "outside policy", decided_by="merchant")
    assert req.status == ReturnStatus.REJECTED
    assert req.decline_note == "outside policy"
    assert any("update on your return" in e["subject"].lower() for e in mock_email.sent)


async def test_settle_refund(service, shop, order_1001, mock_shopify, mock_email):
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(shop, order_1001, [item_payload(li)], resolution="refund")
    req = await service.settle(req, restock=True)
    assert req.status == ReturnStatus.REFUNDED
    assert req.refund_id is not None
    assert req.total_refund_amount == Decimal("49.99")
    assert len(mock_shopify.refunds) == 1
    assert any("refund issued" in e["subject"].lower() for e in mock_email.sent)
    # Idempotent: second settle is a no-op.
    req = await service.settle(req, restock=True)
    assert len(mock_shopify.refunds) == 1


async def test_settle_store_credit(service, shop, order_1001, mock_shopify, mock_email):
    li = order_1001.line_items[0].line_item_id
    req = await service.submit_request(
        shop, order_1001, [item_payload(li)], resolution="store_credit"
    )
    req = await service.settle(req)
    assert req.status == ReturnStatus.STORE_CREDITED
    assert req.gift_card_code.startswith("MOCK-")
    assert len(mock_shopify.gift_cards) == 1
    assert any("store credit" in e["subject"].lower() for e in mock_email.sent)


async def test_settle_exchange(service, shop, order_1001, mock_shopify, mock_email):
    li = order_1001.line_items[0]
    variant = "gid://shopify/ProductVariant/213"
    req = await service.submit_request(
        shop,
        order_1001,
        [item_payload(li.line_item_id, exchange_variant_id=variant)],
        resolution="exchange",
    )
    req = await service.settle(req)
    assert req.status == ReturnStatus.EXCHANGED
    assert req.draft_order_id is not None
    assert len(mock_shopify.draft_orders) == 1
    assert mock_shopify.draft_orders[0]["variant_id"] == variant
    assert mock_shopify.invoices_sent == [req.draft_order_id]
    assert any("exchange" in e["subject"].lower() for e in mock_email.sent)


async def test_exchange_requires_variant_choice(service, shop, order_1001):
    li = order_1001.line_items[0].line_item_id
    with pytest.raises(ValueError, match="replacement variant"):
        await service.submit_request(shop, order_1001, [item_payload(li)], resolution="exchange")


async def test_refund_preview_sums_line_items(service, shop, order_1001):
    li1 = order_1001.line_items[0].line_item_id
    li2 = order_1001.line_items[1].line_item_id
    req = await service.submit_request(
        shop,
        order_1001,
        [item_payload(li1, quantity=2), item_payload(li2, quantity=1)],
        resolution="refund",
    )
    assert service.refund_preview(req) == Decimal("124.97")


async def test_label_failure_does_not_block_approval(db, shop, order_1001, mock_shopify, mock_email):
    """No warehouse address configured -> approval still succeeds, label retried later."""
    shop.settings = {"brand_name": "Test Store"}  # wipe warehouse address
    db.commit()
    svc = ReturnService(
        db=db,
        shopify=mock_shopify,
        shipping=MockShippingProvider(),
        email=mock_email,
        app_url="http://testserver",
    )
    li = order_1001.line_items[0].line_item_id
    req = await svc.submit_request(shop, order_1001, [item_payload(li)], resolution="refund")
    assert req.status == ReturnStatus.APPROVED  # not label_created
    assert req.label_url is None
    assert req.shopify_return_id is not None
    # Customer is told the return is approved and the label is on its way.
    assert any(e["subject"] == "Your return for #1001 is approved" for e in mock_email.sent)
