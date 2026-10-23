"""Shopify adapter protocol."""
from __future__ import annotations

from typing import Protocol


class ShopifyClient(Protocol):
    """Normalized product catalog access. Products are plain dicts:

    {
      "id": "123", "gid": "gid://shopify/Product/123", "handle": "...",
      "title": "...", "description_html": "<p>...</p>",
      "vendor": "...", "product_type": "...", "tags": [...],
      "status": "active", "published": True, "currency": "USD",
      "variants": [{"id": "...", "gid": "...", "title": "...",
                    "barcode": "...", "sku": "...", "price": "19.99",
                    "inventory_quantity": 3,
                    "option_values": [{"name": "Size", "value": "M"}]}],
      "images": [{"id": "...", "gid": "...", "alt": "...", "src": "...",
                  "variant_title": "..."}],
      "metafields": [{"namespace": "...", "key": "...", "value": "..."}],
    }
    """

    def list_products(self, first: int = 50, after: str | None = None) -> tuple[list[dict], dict]:
        """Return (products, page_info {has_next_page, end_cursor})."""
        ...

    def get_product(self, product_id: str) -> dict | None:
        ...

    def update_product_description(self, product_id: str, description_html: str) -> bool:
        ...

    def update_variant_barcode(self, variant_id: str, barcode: str) -> bool:
        ...

    def update_image_alt(self, image_id: str, product_id: str, alt: str) -> bool:
        ...

    def set_product_metafields(self, product_id: str, metafields: list[dict]) -> bool:
        """metafields: [{namespace, key, value, type}]."""
        ...

    def get_shop(self) -> dict:
        ...

    def get_active_app_subscriptions(self) -> list:
        """Current app subscriptions for the shop (AppSubscription dataclasses)."""
        ...

    def create_app_subscription(
        self,
        name: str,
        price_cents: int,
        currency: str,
        trial_days: int,
        return_url: str,
        test: bool = True,
    ) -> tuple[str, str]:
        """Create a recurring subscription; returns (subscription_id, confirmation_url)."""
        ...

    def cancel_app_subscription(self, subscription_id: str) -> bool:
        ...
