"""Mock Shopify client: in-memory fixture catalog for tests and dev mode."""
from __future__ import annotations

import copy


def _variant(vid, title, barcode="", price="19.99", qty=5, options=None, sku=""):
    return {
        "id": vid, "gid": f"gid://shopify/ProductVariant/{vid}", "title": title,
        "barcode": barcode, "sku": sku, "price": price, "inventory_quantity": qty,
        "option_values": options or [],
    }


def _image(iid, alt="", src="https://example.com/img.jpg"):
    return {"id": iid, "gid": f"gid://shopify/ProductImage/{iid}",
            "alt": alt, "src": src}


def fixture_products() -> list[dict]:
    """Three products: one excellent, one terrible, one middling."""
    good = {
        "id": "101", "gid": "gid://shopify/Product/101", "handle": "organic-cotton-tee",
        "title": "Organic Cotton T-Shirt",
        "description_html": (
            "<p>Our bestselling organic cotton t-shirt, cut for everyday comfort. "
            "Made from 100% GOTS-certified organic cotton with a soft, breathable weave "
            "that keeps its shape wash after wash. The classic crew neckline and relaxed "
            "fit make it a wardrobe staple you will reach for daily.</p>"
            "<p>Features a ribbed collar, reinforced shoulder seams, and a tear-away label "
            "for itch-free wear. Machine washable and pre-shrunk for a consistent fit.</p>"
            "<p>Ethically made in a certified fair-trade facility, this tee pairs effortlessly "
            "with jeans, shorts, or layered under a jacket for cooler evenings.</p>"
        ),
        "vendor": "Evergreen Basics", "product_type": "T-Shirts",
        "tags": ["organic", "cotton", "basics"],
        "status": "active", "published": True, "currency": "USD",
        "variants": [
            _variant("1001", "Small", barcode="012345678905", qty=10,
                     options=[{"name": "Size", "value": "S"}], sku="TEE-S"),
            _variant("1002", "Medium", barcode="012345678912", qty=7,
                     options=[{"name": "Size", "value": "M"}], sku="TEE-M"),
        ],
        "images": [_image("2001", alt="Organic Cotton T-Shirt - front view"),
                   _image("2002", alt="Organic Cotton T-Shirt - back view")],
        "metafields": [{"namespace": "agent_fixer", "key": "faqs",
                        "value": '[{"question":"Is it true to size?","answer":"Yes."}]'}],
    }
    bad = {
        "id": "102", "gid": "gid://shopify/Product/102", "handle": "mystery-widget",
        "title": "Widget",
        "description_html": "<p>Buy now.</p>",
        "vendor": "", "product_type": "", "tags": [],
        "status": "active", "published": True, "currency": "USD",
        "variants": [_variant("1003", "Default Title", barcode="", price="9.99", qty=0)],
        "images": [_image("2003", alt="")],
        "metafields": [],
    }
    middling = {
        "id": "103", "gid": "gid://shopify/Product/103", "handle": "trail-running-shoes",
        "title": "Trail Running Shoes",
        "description_html": (
            "<p>Lightweight trail running shoes with aggressive lugs for grip on mud, "
            "rock, and loose gravel. The cushioned midsole absorbs impact on long descents "
            "while the breathable mesh upper keeps feet cool on summer climbs.</p>"
        ),
        "vendor": "Summit Footwear", "product_type": "Shoes",
        "tags": ["running", "trail"],
        "status": "active", "published": True, "currency": "USD",
        "variants": [
            _variant("1004", "US 9", barcode="", price="129.99", qty=4,
                     options=[{"name": "Size", "value": "US 9"}], sku="TRAIL-9"),
            _variant("1005", "US 10", barcode="036000291452", price="129.99", qty=0,
                     options=[{"name": "Size", "value": "US 10"}], sku="TRAIL-10"),
        ],
        "images": [_image("2004", alt="Trail Running Shoes on a rocky path"),
                   _image("2005", alt="")],
        "metafields": [{"namespace": "custom", "key": "material", "value": "mesh"}],
    }
    return [good, bad, middling]


class MockShopifyClient:
    """In-memory catalog. Writes mutate the fixtures so tests can assert."""

    def __init__(self, products: list[dict] | None = None):
        self._products: dict[str, dict] = {
            p["id"]: copy.deepcopy(p) for p in (products or fixture_products())
        }
        self.calls: list[tuple] = []
        # Billing state: gid -> {name, status, price_cents, trial_days}
        self.subscriptions: dict[str, dict] = {}
        self.created_subscriptions: list[dict] = []
        self.cancelled_subscriptions: list[str] = []
        self._subscription_counter = 0

    def list_products(self, first: int = 50, after: str | None = None):
        self.calls.append(("list_products", first, after))
        items = list(self._products.values())[:first]
        return [copy.deepcopy(p) for p in items], {"has_next_page": False, "end_cursor": None}

    def get_product(self, product_id: str):
        self.calls.append(("get_product", product_id))
        p = self._products.get(str(product_id))
        return copy.deepcopy(p) if p else None

    def update_product_description(self, product_id: str, description_html: str) -> bool:
        self.calls.append(("update_product_description", product_id))
        p = self._products.get(str(product_id))
        if not p:
            return False
        p["description_html"] = description_html
        return True

    def update_variant_barcode(self, variant_id: str, barcode: str) -> bool:
        self.calls.append(("update_variant_barcode", variant_id, barcode))
        for p in self._products.values():
            for v in p["variants"]:
                if v["id"] == str(variant_id):
                    v["barcode"] = barcode
                    return True
        return False

    def update_image_alt(self, image_id: str, product_id: str, alt: str) -> bool:
        self.calls.append(("update_image_alt", image_id, product_id, alt))
        p = self._products.get(str(product_id))
        if not p:
            return False
        for img in p["images"]:
            if img["id"] == str(image_id):
                img["alt"] = alt
                return True
        return False

    def set_product_metafields(self, product_id: str, metafields: list[dict]) -> bool:
        self.calls.append(("set_product_metafields", product_id, metafields))
        p = self._products.get(str(product_id))
        if not p:
            return False
        existing = {(m["namespace"], m["key"]): m for m in p["metafields"]}
        for m in metafields:
            existing[(m["namespace"], m["key"])] = {
                "namespace": m["namespace"], "key": m["key"], "value": m["value"],
            }
        p["metafields"] = list(existing.values())
        return True

    def get_shop(self) -> dict:
        return {"name": "Mock Store", "domain": "mock.myshopify.com", "currency": "USD"}

    # -- billing (mock) ----------------------------------------------------

    def get_active_app_subscriptions(self) -> list:
        from .client import AppSubscription

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

    def create_app_subscription(
        self,
        name: str,
        price_cents: int,
        currency: str,
        trial_days: int,
        return_url: str,
        test: bool = True,
    ) -> tuple[str, str]:
        self._subscription_counter += 1
        sid = f"gid://shopify/AppSubscription/{self._subscription_counter}"
        self.subscriptions[sid] = {
            "name": name,
            "status": "PENDING",
            "price_cents": price_cents,
            "trial_days": trial_days,
        }
        self.created_subscriptions.append(
            {"id": sid, "name": name, "price_cents": price_cents, "trial_days": trial_days}
        )
        # In the mock, the merchant "approves" by visiting the callback URL.
        return sid, f"{return_url}&subscription_id={sid}"

    def cancel_app_subscription(self, subscription_id: str) -> bool:
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
