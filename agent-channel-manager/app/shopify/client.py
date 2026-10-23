"""Real Shopify Admin GraphQL client.

Covers the surfaces Agent Channel Manager needs:
- product catalog reads (products, unit cost, collections)
- discount code create/delete (agent-only promotions)

All calls go through one small `_graphql` helper so auth headers and error
handling stay in one place.
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from .protocol import (
    AppSubscription,
    CollectionSnapshot,
    DiscountResult,
    ProductSnapshot,
    ProductVariantSnapshot,
)

_PRODUCTS_QUERY = """
query ($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id title productType vendor tags
        featuredImage { url }
        variants(first: 10) {
          edges { node { id title sku price } }
        }
      }
    }
  }
}
"""

_COLLECTIONS_QUERY = """
query ($first: Int!) {
  collections(first: $first) {
    edges { node { id title productsCount { count } } }
  }
}
"""

_DISCOUNT_CREATE_MUTATION = """
mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
"""

_DISCOUNT_DELETE_MUTATION = """
mutation discountCodeDelete($id: ID!) {
  discountCodeDelete(id: $id) {
    deletedCodeDiscountId
    userErrors { field message }
  }
}
"""

_GET_ACTIVE_SUBSCRIPTIONS_QUERY = """
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

_APP_SUBSCRIPTION_CREATE_MUTATION = """
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

_APP_SUBSCRIPTION_CANCEL_MUTATION = """
mutation AppSubscriptionCancel($id: ID!, $prorate: Boolean) {
  appSubscriptionCancel(id: $id, prorate: $prorate) {
    appSubscription { id status }
    userErrors { field message }
  }
}
"""


class ShopifyGraphQLClient:
    def __init__(self, api_version: str = "2026-07", timeout: float = 25.0):
        self.api_version = api_version
        self.timeout = timeout

    def _url(self, shop_domain: str) -> str:
        return f"https://{shop_domain}/admin/api/{self.api_version}/graphql.json"

    async def _graphql(
        self, access_token: str, shop_domain: str, query: str, variables: dict
    ) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.post(
                self._url(shop_domain),
                headers={
                    "X-Shopify-Access-Token": access_token,
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
        return data["data"]

    async def list_products(
        self, access_token: str, shop_domain: str, limit: int = 50
    ) -> list[ProductSnapshot]:
        products: list[ProductSnapshot] = []
        after = None
        while len(products) < limit:
            data = await self._graphql(
                access_token, shop_domain, _PRODUCTS_QUERY,
                {"first": min(50, limit - len(products)), "after": after},
            )
            conn = data["products"]
            for edge in conn["edges"]:
                node = edge["node"]
                variants = []
                first_price = Decimal("0")
                for i, vedge in enumerate(node["variants"]["edges"]):
                    v = vedge["node"]
                    price = Decimal(str(v["price"]))
                    if i == 0:
                        first_price = price
                    variants.append(
                        ProductVariantSnapshot(
                            variant_id=v["id"], title=v["title"] or "",
                            sku=v["sku"] or "", price=price,
                        )
                    )
                products.append(
                    ProductSnapshot(
                        product_id=node["id"],
                        title=node["title"],
                        product_type=node.get("productType") or "",
                        vendor=node.get("vendor") or "",
                        tags=list(node.get("tags") or []),
                        image_url=(node.get("featuredImage") or {}).get("url") or "",
                        price=first_price,
                        variants=variants,
                    )
                )
            if not conn["pageInfo"]["hasNextPage"]:
                break
            after = conn["pageInfo"]["endCursor"]
        return products

    async def list_collections(
        self, access_token: str, shop_domain: str, limit: int = 50
    ) -> list[CollectionSnapshot]:
        data = await self._graphql(
            access_token, shop_domain, _COLLECTIONS_QUERY, {"first": limit}
        )
        out = []
        for edge in data["collections"]["edges"]:
            node = edge["node"]
            out.append(
                CollectionSnapshot(
                    collection_id=node["id"],
                    title=node["title"],
                    products_count=(node.get("productsCount") or {}).get("count", 0),
                )
            )
        return out

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
        # NOTE: this creates a standard percentage discount code. The
        # "agent-only" property is enforced by this app: the code is generated,
        # never published on the storefront, and only shared with the agent
        # channel (agentic checkout / UCP context). Merchants should keep the
        # code unlisted to preserve the agent-only intent.
        customer_gets: dict = {
            "value": {"percentage": float(discount_pct) / 100.0},
            "items": {"all": True},
        }
        if minimum_order_amount is not None:
            customer_gets["minimumOrderAmount"] = {
                "greaterThan": {"amount": str(minimum_order_amount), "currencyCode": "USD"}
            }
        payload: dict = {
            "title": title,
            "code": code,
            "startsAt": None,
            "customerSelection": {"all": True},
            "customerGets": customer_gets,
        }
        if usage_limit is not None:
            payload["usageLimit"] = usage_limit
        data = await self._graphql(
            access_token, shop_domain,
            _DISCOUNT_CREATE_MUTATION, {"basicCodeDiscount": payload},
        )
        result = data["discountCodeBasicCreate"]
        errors = result.get("userErrors") or []
        if errors:
            return DiscountResult(
                discount_id="", title=title, ok=False,
                error="; ".join(e.get("message", "") for e in errors),
            )
        return DiscountResult(
            discount_id=result["codeDiscountNode"]["id"], title=title
        )

    async def delete_discount(
        self, access_token: str, shop_domain: str, discount_id: str
    ) -> bool:
        data = await self._graphql(
            access_token, shop_domain,
            _DISCOUNT_DELETE_MUTATION, {"id": discount_id},
        )
        errors = data["discountCodeDelete"].get("userErrors") or []
        return not errors

    # -- billing (Shopify Billing API) ------------------------------------

    async def get_active_app_subscriptions(
        self, access_token: str, shop_domain: str
    ) -> list[AppSubscription]:
        data = await self._graphql(
            access_token, shop_domain, _GET_ACTIVE_SUBSCRIPTIONS_QUERY, {}
        )
        subs = []
        for node in (
            (data.get("currentAppInstallation") or {}).get("activeSubscriptions") or []
        ):
            subs.append(
                AppSubscription(
                    id=node["id"],
                    name=node.get("name") or "",
                    status=node.get("status") or "",
                    trial_days=node.get("trialDays") or 0,
                    current_period_end=node.get("currentPeriodEnd"),
                )
            )
        return subs

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
        amount = f"{price_cents / 100:.2f}"
        data = await self._graphql(
            access_token,
            shop_domain,
            _APP_SUBSCRIPTION_CREATE_MUTATION,
            {
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
            },
        )
        result = data["appSubscriptionCreate"]
        errors = result.get("userErrors") or []
        if errors:
            raise RuntimeError(
                "Shopify appSubscriptionCreate failed: "
                + "; ".join(e.get("message", "") for e in errors)
            )
        sub = result["appSubscription"]
        return sub["id"], result["confirmationUrl"]

    async def cancel_app_subscription(
        self, access_token: str, shop_domain: str, subscription_id: str
    ) -> bool:
        data = await self._graphql(
            access_token,
            shop_domain,
            _APP_SUBSCRIPTION_CANCEL_MUTATION,
            {"id": subscription_id, "prorate": True},
        )
        errors = data["appSubscriptionCancel"].get("userErrors") or []
        if errors:
            raise RuntimeError(
                "Shopify appSubscriptionCancel failed: "
                + "; ".join(e.get("message", "") for e in errors)
            )
        return True
