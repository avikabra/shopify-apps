"""Shopify adapter interface (seam) + shared data structures.

The app talks to Shopify ONLY through `ShopifyClientProtocol`. Two
implementations exist:
- `app.shopify.client.ShopifyGraphQLClient` — real Admin GraphQL API calls.
- `app.shopify.mock.MockShopifyClient` — in-memory fixtures, no network.

Swapping is a config change (SHOPIFY_CLIENT_MODE / missing API key).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass
class ProductVariantSnapshot:
    variant_id: str  # gid://shopify/ProductVariant/...
    title: str
    sku: str
    price: Decimal


@dataclass
class ProductSnapshot:
    product_id: str  # gid://shopify/Product/...
    title: str
    product_type: str = ""
    vendor: str = ""
    tags: list[str] = field(default_factory=list)
    image_url: str = ""
    currency: str = "USD"
    # Storefront price = first variant price (MVP simplification).
    price: Decimal = Decimal("0")
    compare_at_price: Decimal | None = None
    # Estimated unit cost for margin math (from inventoryItem.unitCost when
    # available; mock fixtures carry a fixed value).
    unit_cost: Decimal | None = None
    variants: list[ProductVariantSnapshot] = field(default_factory=list)
    collection_ids: list[str] = field(default_factory=list)


@dataclass
class CollectionSnapshot:
    collection_id: str  # gid://shopify/Collection/...
    title: str
    products_count: int = 0


@dataclass
class DiscountResult:
    discount_id: str  # gid://shopify/DiscountCodeNode/... etc.
    title: str
    ok: bool = True
    error: str = ""


@dataclass
class AppSubscription:
    id: str  # gid://shopify/AppSubscription/...
    name: str  # e.g. "Agent Channel Manager Pro"
    status: str  # ACTIVE, CANCELLED, EXPIRED, FROZEN, DECLINED, PENDING...
    trial_days: int = 0
    current_period_end: str | None = None


class ShopifyClientProtocol(Protocol):
    """Minimal Shopify surface the app needs."""

    async def list_products(
        self, access_token: str, shop_domain: str, limit: int = 50
    ) -> list[ProductSnapshot]:
        ...

    async def list_collections(
        self, access_token: str, shop_domain: str, limit: int = 50
    ) -> list[CollectionSnapshot]:
        ...

    async def create_agent_discount_code(
        self,
        access_token: str,
        shop_domain: str,
        *,
        title: str,
        code: str,
        discount_pct: Decimal,
        minimum_order_amount: Decimal | None = None,
        usage_limit: int | None = None,
    ) -> DiscountResult:
        """Create a discount code for an agent-only promotion."""
        ...

    async def delete_discount(
        self, access_token: str, shop_domain: str, discount_id: str
    ) -> bool:
        ...

    # -- billing (Shopify Billing API) ------------------------------------

    async def get_active_app_subscriptions(
        self, access_token: str, shop_domain: str
    ) -> list[AppSubscription]:
        """List currentAppInstallation.activeSubscriptions."""
        ...

    async def create_app_subscription(
        self,
        access_token: str,
        shop_domain: str,
        *,
        name: str,
        price_cents: int,
        currency: str,
        trial_days: int,
        return_url: str,
        test: bool = True,
    ) -> tuple[str, str]:
        """Create a recurring app subscription. Returns (subscription_id, confirmation_url)."""
        ...

    async def cancel_app_subscription(
        self, access_token: str, shop_domain: str, subscription_id: str
    ) -> bool:
        """Cancel an app subscription (prorated)."""
        ...
