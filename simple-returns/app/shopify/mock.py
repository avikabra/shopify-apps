"""In-memory fixture implementation of ShopifyClientProtocol.

Used when SHOPIFY_CLIENT_MODE=mock (or no API key is configured). Lets the
whole app run end-to-end on this VM with zero credentials: OAuth is bypassed
via a dev login route, orders/returns/refunds behave like fixtures.

Fixture store: one order "#1001" for customer@example.com with two fulfilled
line items, one order "#1002" (unfulfilled) for the same customer.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .protocol import (
    DraftOrder,
    GiftCard,
    LineItemSnapshot,
    OrderSnapshot,
    RefundLineItem,
    RequestedLineItem,
    ShopifyError,
    VariantOption,
)


def _gid(kind: str, num: int) -> str:
    return f"gid://shopify/{kind}/{num}"


class MockShopifyClient:
    def __init__(self):
        self._counter = itertools.count(1000)
        self.created_returns: list[dict] = []
        self.approved_returns: list[str] = []
        self.declined_returns: list[str] = []
        self.refunds: list[dict] = []
        self.gift_cards: list[dict] = []
        self.draft_orders: list[dict] = []
        self.invoices_sent: list[str] = []
        self.registered_webhooks: list[tuple[str, str]] = []

    # -- fixture data ----------------------------------------------------

    def _fixture_order(self, name: str) -> OrderSnapshot | None:
        if name == "#1001":
            created = datetime.now(timezone.utc) - timedelta(days=10)
            return OrderSnapshot(
                order_id=_gid("Order", 1),
                order_name="#1001",
                created_at=created,
                email="customer@example.com",
                customer_id=_gid("Customer", 7),
                customer_name="Test Customer",
                currency="USD",
                line_items=[
                    LineItemSnapshot(
                        line_item_id=_gid("LineItem", 11),
                        fulfillment_line_item_id=_gid("FulfillmentLineItem", 111),
                        product_id=_gid("Product", 21),
                        product_tags=["apparel"],
                        title="Test Hoodie",
                        variant_title="Black / M",
                        sku="HOODIE-BLK-M",
                        quantity=2,
                        unit_price=Decimal("49.99"),
                        variant_id=_gid("ProductVariant", 211),
                    ),
                    LineItemSnapshot(
                        line_item_id=_gid("LineItem", 12),
                        fulfillment_line_item_id=_gid("FulfillmentLineItem", 112),
                        product_id=_gid("Product", 22),
                        product_tags=["final-sale"],
                        title="Test Cap",
                        variant_title="Red",
                        sku="CAP-RED",
                        quantity=1,
                        unit_price=Decimal("24.99"),
                        variant_id=_gid("ProductVariant", 221),
                    ),
                ],
            )
        if name == "#1002":
            created = datetime.now(timezone.utc) - timedelta(days=2)
            return OrderSnapshot(
                order_id=_gid("Order", 2),
                order_name="#1002",
                created_at=created,
                email="customer@example.com",
                customer_id=_gid("Customer", 7),
                customer_name="Test Customer",
                currency="USD",
                line_items=[
                    LineItemSnapshot(
                        line_item_id=_gid("LineItem", 13),
                        fulfillment_line_item_id=None,  # unfulfilled: not returnable yet
                        product_id=_gid("Product", 23),
                        title="Test Mug",
                        variant_title="White",
                        sku="MUG-WHT",
                        quantity=1,
                        unit_price=Decimal("14.99"),
                        variant_id=_gid("ProductVariant", 231),
                    ),
                ],
            )
        return None

    # -- protocol --------------------------------------------------------

    async def get_order_for_return(
        self, shop_domain: str, order_name: str, email: str
    ) -> OrderSnapshot | None:
        name = order_name.strip()
        if not name.startswith("#"):
            name = f"#{name}"
        order = self._fixture_order(name)
        if order is None:
            return None
        if order.email.lower() != email.strip().lower():
            return None
        return order

    async def create_return_request(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RequestedLineItem],
        notify_customer: bool = False,
    ) -> str:
        for li in line_items:
            if not li.fulfillment_line_item_id:
                raise ShopifyError("Cannot request return for unfulfilled line item")
        rid = _gid("Return", next(self._counter))
        self.created_returns.append(
            {
                "id": rid,
                "order_id": order_id,
                "line_items": [
                    {
                        "fulfillment_line_item_id": li.fulfillment_line_item_id,
                        "quantity": li.quantity,
                        "return_reason": li.return_reason,
                        "customer_note": li.customer_note,
                    }
                    for li in line_items
                ],
            }
        )
        return rid

    async def approve_return(self, shop_domain: str, shopify_return_id: str) -> None:
        self.approved_returns.append(shopify_return_id)

    async def decline_return(self, shop_domain: str, shopify_return_id: str) -> None:
        self.declined_returns.append(shopify_return_id)

    async def refund_line_items(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RefundLineItem],
        idempotency_key: str,
        notify_customer: bool = False,
        note: str = "",
    ) -> str:
        if any(r["idempotency_key"] == idempotency_key for r in self.refunds):
            raise ShopifyError("Duplicate idempotency key")
        rid = _gid("Refund", next(self._counter))
        self.refunds.append(
            {
                "id": rid,
                "order_id": order_id,
                "idempotency_key": idempotency_key,
                "line_items": [
                    {"line_item_id": li.line_item_id, "quantity": li.quantity}
                    for li in line_items
                ],
            }
        )
        return rid

    async def create_gift_card(
        self,
        shop_domain: str,
        customer_id: str,
        amount: Decimal,
        currency: str,
        note: str = "",
    ) -> GiftCard:
        gid = _gid("GiftCard", next(self._counter))
        code = f"MOCK-{next(self._counter):06d}"
        self.gift_cards.append(
            {"id": gid, "code": code, "amount": str(amount), "customer_id": customer_id}
        )
        return GiftCard(
            id=gid, code=code, balance_amount=amount, balance_currency=currency
        )

    async def create_exchange_draft_order(
        self,
        shop_domain: str,
        customer_id: str,
        variant_id: str,
        quantity: int,
        note: str = "",
    ) -> DraftOrder:
        did = _gid("DraftOrder", next(self._counter))
        self.draft_orders.append(
            {
                "id": did,
                "customer_id": customer_id,
                "variant_id": variant_id,
                "quantity": quantity,
            }
        )
        return DraftOrder(id=did, name="#D1", invoice_url="https://example.com/invoice")

    async def send_draft_order_invoice(
        self, shop_domain: str, draft_order_id: str
    ) -> None:
        self.invoices_sent.append(draft_order_id)

    async def register_webhooks(self, shop_domain: str, base_url: str) -> None:
        self.registered_webhooks.append((shop_domain, base_url))

    async def get_product_variants(
        self, shop_domain: str, product_id: str
    ) -> list[VariantOption]:
        if product_id == _gid("Product", 21):
            return [
                VariantOption(_gid("ProductVariant", 211), "Black / S", "HOODIE-BLK-S", Decimal("49.99")),
                VariantOption(_gid("ProductVariant", 212), "Black / M", "HOODIE-BLK-M", Decimal("49.99")),
                VariantOption(_gid("ProductVariant", 213), "Black / L", "HOODIE-BLK-L", Decimal("49.99")),
            ]
        return []

    @staticmethod
    def new_idempotency_key() -> str:
        import uuid

        return f"sr-{uuid.uuid4().hex}"
