"""Real Shopify Admin API (GraphQL) client."""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class AppSubscription:
    id: str                 # gid://shopify/AppSubscription/...
    name: str               # e.g. "Agent Catalog Fixer Pro"
    status: str             # ACTIVE, CANCELLED, EXPIRED, FROZEN, DECLINED, PENDING...
    trial_days: int = 0
    current_period_end: str | None = None

_PRODUCTS_QUERY = """
query ListProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      handle
      title
      descriptionHtml
      vendor
      productType
      tags
      status
      publishedAt
      variants(first: 100) {
        nodes {
          id
          title
          barcode
          sku
          price
          inventoryQuantity
          selectedOptions { name value }
        }
      }
      images(first: 50) {
        nodes { id altText url }
      }
      metafields(first: 50) {
        nodes { namespace key value }
      }
    }
  }
}
"""

_PRODUCT_QUERY = """
query GetProduct($id: ID!) {
  product(id: $id) {
    id handle title descriptionHtml vendor productType tags status publishedAt
    variants(first: 100) {
      nodes { id title barcode sku price inventoryQuantity selectedOptions { name value } }
    }
    images(first: 50) { nodes { id altText url } }
    metafields(first: 50) { nodes { namespace key value } }
  }
}
"""

_PRODUCT_UPDATE = """
mutation ProductUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id }
    userErrors { field message }
  }
}
"""

_VARIANT_UPDATE = """
mutation ProductVariantUpdate($input: ProductVariantInput!) {
  productVariantUpdate(input: $input) {
    productVariant { id }
    userErrors { field message }
  }
}
"""

_IMAGE_UPDATE = """
mutation ProductImageUpdate($input: ProductImageUpdateInput!) {
  productImageUpdate(input: $input) {
    image { id }
    userErrors { field message }
  }
}
"""

_METAFIELDS_SET = """
mutation MetafieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    userErrors { field message }
  }
}
"""

_SHOP_QUERY = """
query { shop { name myshopifyDomain currencyCode } }
"""

_GET_ACTIVE_SUBSCRIPTIONS = """
query GetActiveSubscriptions {
  currentAppInstallation {
    activeSubscriptions {
      id
      name
      status
      trialDays
      currentPeriodEnd
    }
  }
}
"""

_APP_SUBSCRIPTION_CREATE = """
mutation AppSubscriptionCreate(
  $name: String!
  $returnUrl: URL!
  $test: Boolean
  $trialDays: Int
  $lineItems: [AppSubscriptionLineItemInput!]!
) {
  appSubscriptionCreate(
    name: $name
    returnUrl: $returnUrl
    test: $test
    trialDays: $trialDays
    lineItems: $lineItems
  ) {
    appSubscription {
      id
      name
      status
      trialDays
    }
    confirmationUrl
    userErrors { field message }
  }
}
"""

_APP_SUBSCRIPTION_CANCEL = """
mutation AppSubscriptionCancel($id: ID!, $prorate: Boolean) {
  appSubscriptionCancel(id: $id, prorate: $prorate) {
    appSubscription { id status }
    userErrors { field message }
  }
}
"""


def _numeric_id(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def _normalize(node: dict, currency: str = "USD") -> dict:
    variants = []
    for v in (node.get("variants") or {}).get("nodes", []):
        variants.append({
            "id": _numeric_id(v["id"]),
            "gid": v["id"],
            "title": v.get("title") or "",
            "barcode": v.get("barcode") or "",
            "sku": v.get("sku") or "",
            "price": v.get("price") or "0",
            "inventory_quantity": v.get("inventoryQuantity") or 0,
            "option_values": [
                {"name": o.get("name", ""), "value": o.get("value", "")}
                for o in v.get("selectedOptions") or []
            ],
        })
    images = []
    for img in (node.get("images") or {}).get("nodes", []):
        images.append({
            "id": _numeric_id(img["id"]),
            "gid": img["id"],
            "alt": img.get("altText") or "",
            "src": img.get("url") or "",
        })
    metafields = [
        {"namespace": m.get("namespace") or "", "key": m.get("key") or "", "value": m.get("value") or ""}
        for m in (node.get("metafields") or {}).get("nodes", [])
    ]
    status = (node.get("status") or "").lower()
    return {
        "id": _numeric_id(node["id"]),
        "gid": node["id"],
        "handle": node.get("handle") or "",
        "title": node.get("title") or "",
        "description_html": node.get("descriptionHtml") or "",
        "vendor": node.get("vendor") or "",
        "product_type": node.get("productType") or "",
        "tags": node.get("tags") or [],
        "status": status,
        "published": bool(node.get("publishedAt")),
        "currency": currency,
        "variants": variants,
        "images": images,
        "metafields": metafields,
    }


class ShopifyGraphQLClient:
    def __init__(self, shop_domain: str, access_token: str, api_version: str = "2026-07"):
        self.base = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        self._currency = "USD"

    def _post(self, query: str, variables: dict) -> dict:
        resp = httpx.post(self.base, headers=self.headers,
                          json={"query": query, "variables": variables}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
        return data["data"]

    def list_products(self, first: int = 50, after: str | None = None):
        data = self._post(_PRODUCTS_QUERY, {"first": first, "after": after})
        conn = data["products"]
        products = [_normalize(n, self._currency) for n in conn["nodes"]]
        return products, {
            "has_next_page": conn["pageInfo"]["hasNextPage"],
            "end_cursor": conn["pageInfo"]["endCursor"],
        }

    def get_product(self, product_id: str):
        gid = product_id if product_id.startswith("gid://") else f"gid://shopify/Product/{product_id}"
        data = self._post(_PRODUCT_QUERY, {"id": gid})
        node = data.get("product")
        return _normalize(node, self._currency) if node else None

    def update_product_description(self, product_id: str, description_html: str) -> bool:
        gid = f"gid://shopify/Product/{product_id}"
        data = self._post(_PRODUCT_UPDATE, {"input": {"id": gid, "descriptionHtml": description_html}})
        return not data["productUpdate"]["userErrors"]

    def update_variant_barcode(self, variant_id: str, barcode: str) -> bool:
        gid = f"gid://shopify/ProductVariant/{variant_id}"
        data = self._post(_VARIANT_UPDATE, {"input": {"id": gid, "barcode": barcode}})
        return not data["productVariantUpdate"]["userErrors"]

    def update_image_alt(self, image_id: str, product_id: str, alt: str) -> bool:
        data = self._post(_IMAGE_UPDATE, {
            "input": {
                "id": f"gid://shopify/ProductImage/{image_id}",
                "productId": f"gid://shopify/Product/{product_id}",
                "altText": alt,
            }
        })
        return not data["productImageUpdate"]["userErrors"]

    def set_product_metafields(self, product_id: str, metafields: list[dict]) -> bool:
        owner = f"gid://shopify/Product/{product_id}"
        inputs = [
            {"ownerId": owner, "namespace": m["namespace"], "key": m["key"],
             "value": m["value"], "type": m.get("type", "multi_line_text_field")}
            for m in metafields
        ]
        data = self._post(_METAFIELDS_SET, {"metafields": inputs})
        return not data["metafieldsSet"]["userErrors"]

    def get_shop(self) -> dict:
        data = self._post(_SHOP_QUERY, {})
        shop = data.get("shop") or {}
        self._currency = shop.get("currencyCode") or "USD"
        return {
            "name": shop.get("name") or "",
            "domain": shop.get("myshopifyDomain") or "",
            "currency": self._currency,
        }

    # -- billing (Shopify Billing API) -----------------------------------

    def get_active_app_subscriptions(self) -> list[AppSubscription]:
        data = self._post(_GET_ACTIVE_SUBSCRIPTIONS, {})
        conn = (data.get("currentAppInstallation") or {})
        return [
            AppSubscription(
                id=node["id"],
                name=node.get("name") or "",
                status=node.get("status") or "",
                trial_days=node.get("trialDays") or 0,
                current_period_end=node.get("currentPeriodEnd"),
            )
            for node in conn.get("activeSubscriptions") or []
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
        """Returns (subscription_id, confirmation_url) for merchant approval."""
        amount = f"{price_cents / 100:.2f}"
        data = self._post(_APP_SUBSCRIPTION_CREATE, {
            "name": name,
            "returnUrl": return_url,
            "test": test,
            "trialDays": trial_days,
            "lineItems": [
                {
                    "plan": {
                        "appRecurringPricingDetails": {
                            "price": {"amount": amount, "currencyCode": currency},
                            "interval": "EVERY_30_DAYS",
                        }
                    }
                }
            ],
        })
        sub = data["appSubscriptionCreate"]["appSubscription"]
        return sub["id"], data["appSubscriptionCreate"]["confirmationUrl"]

    def cancel_app_subscription(self, subscription_id: str) -> bool:
        data = self._post(_APP_SUBSCRIPTION_CANCEL, {"id": subscription_id, "prorate": True})
        return not data["appSubscriptionCancel"]["userErrors"]
