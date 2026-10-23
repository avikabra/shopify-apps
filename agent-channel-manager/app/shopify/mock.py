"""In-memory Shopify fixtures for mock mode and tests. No network."""
from __future__ import annotations

from decimal import Decimal

from .protocol import (
    AppSubscription,
    CollectionSnapshot,
    DiscountResult,
    ProductSnapshot,
    ProductVariantSnapshot,
)


def _p(pid, title, price, **kw):
    return ProductSnapshot(
        product_id=f"gid://shopify/Product/{pid}",
        title=title,
        price=Decimal(str(price)),
        variants=[
            ProductVariantSnapshot(
                variant_id=f"gid://shopify/ProductVariant/{pid}01",
                title="Default",
                sku=f"SKU-{pid}",
                price=Decimal(str(price)),
            )
        ],
        **kw,
    )


PRODUCTS = [
    _p(1, "Everyday Cotton T-Shirt", "29.99", product_type="Apparel",
       tags=["cotton", "basics"], unit_cost=Decimal("9.50"),
       collection_ids=["gid://shopify/Collection/10"]),
    _p(2, "Premium Noise-Cancelling Headphones", "299.00", product_type="Electronics",
       vendor="Acme Audio", tags=["premium", "map-protected"], unit_cost=Decimal("180.00"),
       compare_at_price=Decimal("349.00")),
    _p(3, "Stainless Steel Water Bottle", "34.95", product_type="Accessories",
       tags=["eco"], unit_cost=Decimal("11.00")),
    _p(4, "Limited Edition Sneakers", "189.00", product_type="Footwear",
       tags=["limited", "low-margin"], unit_cost=Decimal("150.00")),
    _p(5, "Organic Cotton Hoodie", "79.50", product_type="Apparel",
       tags=["cotton", "organic"], unit_cost=Decimal("28.00"),
       collection_ids=["gid://shopify/Collection/10"]),
]

COLLECTIONS = [
    CollectionSnapshot(
        collection_id="gid://shopify/Collection/10",
        title="Apparel",
        products_count=2,
    ),
    CollectionSnapshot(
        collection_id="gid://shopify/Collection/20",
        title="Clearance",
        products_count=0,
    ),
]


class MockShopifyClient:
    """Fixture-backed client: deterministic, no network."""

    def __init__(self, products: list[ProductSnapshot] | None = None):
        self._products = list(products) if products is not None else list(PRODUCTS)
        self.created_discounts: list[dict] = []
        self.deleted_discounts: list[str] = []
        # -- billing fixtures ---------------------------------------------
        self.subscriptions: dict[str, dict] = {}
        self.created_subscriptions: list[dict] = []
        self.cancelled_subscriptions: list[str] = []
        self._counter = 0

    async def list_products(self, access_token, shop_domain, limit=50):
        return self._products[:limit]

    async def list_collections(self, access_token, shop_domain, limit=50):
        return list(COLLECTIONS[:limit])

    async def create_agent_discount_code(
        self,
        access_token,
        shop_domain,
        *,
        title,
        code,
        discount_pct,
        minimum_order_amount=None,
        usage_limit=None,
    ) -> DiscountResult:
        discount_id = f"gid://shopify/DiscountCodeNode/{len(self.created_discounts) + 1000}"
        self.created_discounts.append(
            {
                "id": discount_id,
                "title": title,
                "code": code,
                "discount_pct": str(discount_pct),
                "minimum_order_amount": str(minimum_order_amount)
                if minimum_order_amount is not None
                else None,
            }
        )
        return DiscountResult(discount_id=discount_id, title=title)

    async def delete_discount(self, access_token, shop_domain, discount_id) -> bool:
        self.deleted_discounts.append(discount_id)
        return True

    # -- billing (mock) ---------------------------------------------------

    async def get_active_app_subscriptions(
        self, access_token, shop_domain
    ) -> list[AppSubscription]:
        return [
            AppSubscription(
                id=sid,
                name=sub["name"],
                status=sub["status"],
                trial_days=sub.get("trial_days", 0),
            )
            for sid, sub in self.subscriptions.items()
            if sub["status"] in ("ACTIVE", "TRIALING", "PENDING")
        ]

    async def create_app_subscription(
        self,
        access_token,
        shop_domain,
        *,
        name,
        price_cents,
        currency,
        trial_days,
        return_url,
        test=True,
    ) -> tuple[str, str]:
        self._counter += 1
        sid = f"gid://shopify/AppSubscription/{self._counter}"
        self.subscriptions[sid] = {
            "name": name,
            "status": "PENDING",
            "price_cents": price_cents,
            "trial_days": trial_days,
        }
        self.created_subscriptions.append(
            {
                "id": sid,
                "name": name,
                "price_cents": price_cents,
                "trial_days": trial_days,
            }
        )
        # In the mock, the merchant "approves" by visiting the callback URL.
        return sid, f"{return_url}&subscription_id={sid}"

    async def cancel_app_subscription(
        self, access_token, shop_domain, subscription_id
    ) -> bool:
        sub = self.subscriptions.get(subscription_id)
        if sub is None:
            raise RuntimeError(f"Unknown subscription: {subscription_id}")
        sub["status"] = "CANCELLED"
        self.cancelled_subscriptions.append(subscription_id)
        return True

    def approve_mock_subscription(self, subscription_id: str) -> None:
        """Test helper: simulate the merchant approving on Shopify."""
        sub = self.subscriptions.get(subscription_id)
        if sub is not None:
            sub["status"] = "ACTIVE"
