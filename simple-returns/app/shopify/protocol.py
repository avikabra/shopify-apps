"""Shopify adapter interface (seam) + shared data structures.

The app talks to Shopify ONLY through `ShopifyClientProtocol`. Two
implementations exist:
- `app.shopify.client.ShopifyGraphQLClient` — real Admin GraphQL API calls.
- `app.shopify.mock.MockShopifyClient` — in-memory fixtures, no network.

Swapping is a config change (SHOPIFY_CLIENT_MODE / missing API key).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass
class LineItemSnapshot:
    line_item_id: str          # gid://shopify/LineItem/...
    fulfillment_line_item_id: str | None  # needed for returnRequest; None if unfulfilled
    product_id: str | None
    product_tags: list[str] = field(default_factory=list)
    title: str = ""
    variant_title: str = ""
    sku: str = ""
    quantity: int = 0
    unit_price: Decimal = Decimal("0")
    variant_id: str | None = None


@dataclass
class OrderSnapshot:
    order_id: str              # gid://shopify/Order/...
    order_name: str            # "#1001"
    created_at: datetime
    email: str
    customer_id: str | None
    customer_name: str = ""
    currency: str = "USD"
    line_items: list[LineItemSnapshot] = field(default_factory=list)


@dataclass
class VariantOption:
    variant_id: str  # gid://shopify/ProductVariant/...
    title: str
    sku: str
    price: Decimal


@dataclass
class RequestedLineItem:
    fulfillment_line_item_id: str
    quantity: int
    return_reason: str         # Shopify ReturnReason enum value
    customer_note: str = ""


@dataclass
class RefundLineItem:
    line_item_id: str          # gid://shopify/LineItem/... (for refundCreate)
    quantity: int
    restock: bool = True


@dataclass
class GiftCard:
    id: str
    code: str
    balance_amount: Decimal
    balance_currency: str


@dataclass
class DraftOrder:
    id: str
    name: str
    invoice_url: str | None = None


@dataclass
class AppSubscription:
    id: str                 # gid://shopify/AppSubscription/...
    name: str               # e.g. "Simple Returns Starter"
    status: str             # ACTIVE, CANCELLED, EXPIRED, FROZEN, DECLINED, PENDING...
    trial_days: int = 0
    current_period_end: str | None = None


class ShopifyError(Exception):
    """Raised when a Shopify API call fails (transport or userErrors)."""


class ShopifyClientProtocol(Protocol):
    """The seam between the app and Shopify. All methods are async."""

    async def get_order_for_return(
        self, shop_domain: str, order_name: str, email: str
    ) -> OrderSnapshot | None:
        """Look up an order by name and verify the customer email matches.

        Returns None if the order doesn't exist or the email doesn't match
        (we deliberately don't distinguish the two).
        """
        ...

    async def create_return_request(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RequestedLineItem],
        notify_customer: bool = False,
    ) -> str:
        """Create a REQUESTED return via `returnRequest`. Returns the Return GID."""
        ...

    async def approve_return(self, shop_domain: str, shopify_return_id: str) -> None:
        """Approve via `returnApproveRequest` (status -> OPEN)."""
        ...

    async def decline_return(self, shop_domain: str, shopify_return_id: str) -> None:
        """Decline via `returnDeclineRequest` (status -> DECLINED)."""
        ...

    async def refund_line_items(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RefundLineItem],
        idempotency_key: str,
        notify_customer: bool = False,
        note: str = "",
    ) -> str:
        """Refund via `refundCreate` (with required @idempotent key). Returns refund GID."""
        ...

    async def create_gift_card(
        self,
        shop_domain: str,
        customer_id: str,
        amount: Decimal,
        currency: str,
        note: str = "",
    ) -> GiftCard:
        """Issue store credit via `giftCardCreate`."""
        ...

    async def create_exchange_draft_order(
        self,
        shop_domain: str,
        customer_id: str,
        variant_id: str,
        quantity: int,
        note: str = "",
    ) -> DraftOrder:
        """Create a draft order for the exchange item via `draftOrderCreate`."""
        ...

    async def send_draft_order_invoice(
        self, shop_domain: str, draft_order_id: str
    ) -> None:
        """Email the draft order invoice to the customer."""
        ...

    async def register_webhooks(self, shop_domain: str, base_url: str) -> None:
        """Register all webhook topics in settings for this shop."""
        ...

    async def get_product_variants(
        self, shop_domain: str, product_id: str
    ) -> list[VariantOption]:
        """List sellable variants for a product (for exchange selection)."""
        ...

    # -- billing (Shopify Billing API) -----------------------------------

    async def get_active_app_subscriptions(
        self, shop_domain: str
    ) -> list[AppSubscription]:
        """List the shop's active app subscriptions (usually 0 or 1)."""
        ...

    async def create_app_subscription(
        self,
        shop_domain: str,
        name: str,
        price_cents: int,
        currency: str,
        trial_days: int,
        return_url: str,
        test: bool = True,
    ) -> tuple[str, str]:
        """Create a recurring app subscription via `appSubscriptionCreate`.

        Returns (subscription_id, confirmation_url). The merchant must visit
        the confirmation URL to approve; Shopify redirects back to return_url.
        """
        ...

    async def cancel_app_subscription(
        self, shop_domain: str, subscription_id: str
    ) -> bool:
        """Cancel via `appSubscriptionCancel`. Returns True on success."""
        ...
