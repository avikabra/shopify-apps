"""Real Shopify Admin GraphQL client.

Implements ShopifyClientProtocol over httpx. All calls:
- POST https://{shop}/admin/api/{version}/graphql.json
- header X-Shopify-Access-Token (offline token, decrypted per shop)
- raise ShopifyError on transport errors, GraphQL `errors`, or `userErrors`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from .. import security
from ..config import get_settings
from ..db import SessionLocal
from ..models import Shop
from . import queries
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


def _refresh_access_token(shop: Shop, db, settings) -> None:
    """Exchange the shop's refresh token for a fresh access token.

    Shopify rotates both tokens on every refresh: persist the new pair
    (and the new expiry) in the same write, or the next refresh fails.
    """
    refresh_token = security.decrypt_token(shop.refresh_token_enc, settings)
    with httpx.Client(timeout=20) as http:
        resp = http.post(
            f"https://{shop.shop_domain}/admin/oauth/access_token",
            json={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    try:
        resp.raise_for_status()
        payload = resp.json()
        new_token = payload["access_token"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ShopifyError(f"Could not refresh access token: {exc}") from exc
    shop.access_token_enc = security.encrypt_token(new_token, settings)
    if payload.get("refresh_token"):
        shop.refresh_token_enc = security.encrypt_token(
            payload["refresh_token"], settings
        )
    if payload.get("expires_in"):
        shop.access_token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload["expires_in"])
        )
    db.commit()


def _access_token(shop_domain: str) -> str:
    settings = get_settings()
    db = SessionLocal()
    try:
        shop = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()
        if not shop or shop.uninstalled_at:
            raise ShopifyError(f"Unknown or uninstalled shop: {shop_domain}")
        # Expiring offline tokens live ~1h. Refresh a little early so a
        # long operation never starts on a token that dies mid-flight.
        if shop.refresh_token_enc and shop.access_token_expires_at is not None:
            expires_at = shop.access_token_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at - datetime.now(timezone.utc) < timedelta(minutes=5):
                _refresh_access_token(shop, db, settings)
        return security.decrypt_token(shop.access_token_enc, settings)
    finally:
        db.close()


class ShopifyGraphQLClient:
    def __init__(self, timeout: float = 20.0):
        self._settings = get_settings()
        self._timeout = timeout

    def _url(self, shop_domain: str) -> str:
        return (
            f"https://{shop_domain}/admin/api/"
            f"{self._settings.shopify_api_version}/graphql.json"
        )

    async def _call(
        self,
        shop_domain: str,
        query: str,
        variables: dict,
        data_key: str,
    ) -> dict:
        """Execute a GraphQL operation and return the payload dict at data_key."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.post(
                    self._url(shop_domain),
                    json={"query": query, "variables": variables},
                    headers={
                        "Content-Type": "application/json",
                        "X-Shopify-Access-Token": _access_token(shop_domain),
                    },
                )
            except httpx.HTTPError as exc:
                raise ShopifyError(f"Shopify transport error: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise ShopifyError(f"Non-JSON response from Shopify: {resp.status_code}") from exc
        if body.get("errors"):
            raise ShopifyError(f"Shopify GraphQL errors: {body['errors']}")
        payload = (body.get("data") or {}).get(data_key) or {}
        user_errors = payload.get("userErrors") or []
        if user_errors:
            raise ShopifyError(f"Shopify userErrors: {user_errors}")
        return payload

    # -- reads -----------------------------------------------------------

    async def get_order_for_return(
        self, shop_domain: str, order_name: str, email: str
    ) -> OrderSnapshot | None:
        name = order_name.strip()
        if not name.startswith("#"):
            name = f"#{name}"
        payload = await self._call(
            shop_domain,
            queries.GET_ORDER_BY_NAME,
            {"query": f"name:{name}"},
            "orders",
        )
        edges = payload.get("edges") or []
        if not edges:
            return None
        node = edges[0]["node"]
        # Do not distinguish "no such order" from "email mismatch".
        if (node.get("email") or "").lower() != email.strip().lower():
            return None

        # Map fulfillment line items -> order line items for returnRequest input.
        fli_by_line_item: dict[str, str] = {}
        for f_edge in (node.get("fulfillments") or {}).get("edges", []):
            for fl_edge in f_edge["node"]["fulfillmentLineItems"]["edges"]:
                fl = fl_edge["node"]
                li_id = (fl.get("lineItem") or {}).get("id")
                if li_id:
                    fli_by_line_item[li_id] = fl["id"]

        items: list[LineItemSnapshot] = []
        for li_edge in (node.get("lineItems") or {}).get("edges", []):
            li = li_edge["node"]
            money = ((li.get("discountedUnitPriceSet") or {}).get("shopMoney")) or {}
            variant = li.get("variant") or {}
            product = li.get("product") or {}
            items.append(
                LineItemSnapshot(
                    line_item_id=li["id"],
                    fulfillment_line_item_id=fli_by_line_item.get(li["id"]),
                    product_id=product.get("id"),
                    product_tags=product.get("tags") or [],
                    title=li.get("title") or "",
                    variant_title=li.get("variantTitle") or "",
                    sku=li.get("sku") or "",
                    quantity=li.get("quantity") or 0,
                    unit_price=Decimal(str(money.get("amount") or "0")),
                    variant_id=variant.get("id"),
                )
            )

        customer = node.get("customer") or {}
        cust_name = " ".join(
            p for p in [customer.get("firstName"), customer.get("lastName")] if p
        )
        created = node.get("createdAt") or ""
        return OrderSnapshot(
            order_id=node["id"],
            order_name=node.get("name") or name,
            created_at=datetime.fromisoformat(created.replace("Z", "+00:00")),
            email=node.get("email") or "",
            customer_id=customer.get("id"),
            customer_name=cust_name,
            currency=node.get("currencyCode") or "USD",
            line_items=items,
        )

    # -- writes ----------------------------------------------------------

    async def create_return_request(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RequestedLineItem],
        notify_customer: bool = False,
    ) -> str:
        payload = await self._call(
            shop_domain,
            queries.RETURN_REQUEST,
            {
                "input": {
                    "orderId": order_id,
                    "returnLineItems": [
                        {
                            "fulfillmentLineItemId": li.fulfillment_line_item_id,
                            "quantity": li.quantity,
                            "returnReason": li.return_reason,
                            "customerNote": li.customer_note or None,
                        }
                        for li in line_items
                    ],
                    "notifyCustomer": notify_customer,
                }
            },
            "returnRequest",
        )
        return payload["return"]["id"]

    async def approve_return(self, shop_domain: str, shopify_return_id: str) -> None:
        await self._call(
            shop_domain,
            queries.RETURN_APPROVE_REQUEST,
            {"input": {"id": shopify_return_id}},
            "returnApproveRequest",
        )

    async def decline_return(self, shop_domain: str, shopify_return_id: str) -> None:
        await self._call(
            shop_domain,
            queries.RETURN_DECLINE_REQUEST,
            {"input": {"id": shopify_return_id}},
            "returnDeclineRequest",
        )

    async def refund_line_items(
        self,
        shop_domain: str,
        order_id: str,
        line_items: list[RefundLineItem],
        idempotency_key: str,
        notify_customer: bool = False,
        note: str = "",
    ) -> str:
        mutation = queries.REFUND_CREATE.format(idempotency_key=idempotency_key)
        payload = await self._call(
            shop_domain,
            mutation,
            {
                "input": {
                    "orderId": order_id,
                    "notify": notify_customer,
                    "note": note or None,
                    "refundLineItems": [
                        {
                            "lineItemId": li.line_item_id,
                            "quantity": li.quantity,
                            "restockType": "RETURN" if li.restock else "NO_RESTOCK",
                        }
                        for li in line_items
                    ],
                }
            },
            "refundCreate",
        )
        return payload["refund"]["id"]

    async def create_gift_card(
        self,
        shop_domain: str,
        customer_id: str,
        amount: Decimal,
        currency: str,
        note: str = "",
    ) -> GiftCard:
        # NOTE: exact GiftCardCreateInput shape to be confirmed on a dev store.
        payload = await self._call(
            shop_domain,
            queries.GIFT_CARD_CREATE,
            {
                "input": {
                    "initialValue": str(amount),
                    "customerId": customer_id,
                    "note": note or None,
                }
            },
            "giftCardCreate",
        )
        gc = payload["giftCard"]
        bal = gc.get("balance") or {}
        return GiftCard(
            id=gc["id"],
            code=gc.get("code") or "",
            balance_amount=Decimal(str(bal.get("amount") or amount)),
            balance_currency=bal.get("currencyCode") or currency,
        )

    async def create_exchange_draft_order(
        self,
        shop_domain: str,
        customer_id: str,
        variant_id: str,
        quantity: int,
        note: str = "",
    ) -> DraftOrder:
        payload = await self._call(
            shop_domain,
            queries.DRAFT_ORDER_CREATE,
            {
                "input": {
                    "lineItems": [{"variantId": variant_id, "quantity": quantity}],
                    "customerId": customer_id,
                    "note": note or None,
                    "tags": ["simple-returns-exchange"],
                }
            },
            "draftOrderCreate",
        )
        do = payload["draftOrder"]
        return DraftOrder(
            id=do["id"], name=do.get("name") or "", invoice_url=do.get("invoiceUrl")
        )

    async def send_draft_order_invoice(
        self, shop_domain: str, draft_order_id: str
    ) -> None:
        await self._call(
            shop_domain,
            queries.DRAFT_ORDER_INVOICE_SEND,
            {"id": draft_order_id},
            "draftOrderInvoiceSend",
        )

    async def register_webhooks(self, shop_domain: str, base_url: str) -> None:
        topics = self._settings.webhook_topics
        for topic in topics:
            enum_topic = topic.upper().replace("/", "_")
            await self._call(
                shop_domain,
                queries.WEBHOOK_SUBSCRIPTION_CREATE,
                {
                    "topic": enum_topic,
                    "callbackUrl": f"{base_url}/webhooks/{topic}",
                },
                "webhookSubscriptionCreate",
            )

    async def get_product_variants(
        self, shop_domain: str, product_id: str
    ) -> list[VariantOption]:
        payload = await self._call(
            shop_domain,
            queries.GET_PRODUCT_VARIANTS,
            {"id": product_id},
            "product",
        )
        variants = []
        for edge in ((payload or {}).get("variants") or {}).get("edges", []):
            node = edge["node"]
            variants.append(
                VariantOption(
                    variant_id=node["id"],
                    title=node.get("title") or "",
                    sku=node.get("sku") or "",
                    price=Decimal(str(node.get("price") or "0")),
                )
            )
        return variants

    # -- billing (Shopify Billing API) -----------------------------------

    async def get_active_app_subscriptions(
        self, shop_domain: str
    ) -> list[AppSubscription]:
        from .protocol import AppSubscription

        payload = await self._call(
            shop_domain,
            queries.GET_ACTIVE_SUBSCRIPTIONS,
            {},
            "currentAppInstallation",
        )
        subs = []
        for node in payload.get("activeSubscriptions") or []:
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
        shop_domain: str,
        name: str,
        price_cents: int,
        currency: str,
        trial_days: int,
        return_url: str,
        test: bool = True,
    ) -> tuple[str, str]:
        amount = f"{price_cents / 100:.2f}"
        payload = await self._call(
            shop_domain,
            queries.APP_SUBSCRIPTION_CREATE,
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
            "appSubscriptionCreate",
        )
        sub = payload["appSubscription"]
        return sub["id"], payload["confirmationUrl"]

    async def cancel_app_subscription(
        self, shop_domain: str, subscription_id: str
    ) -> bool:
        await self._call(
            shop_domain,
            queries.APP_SUBSCRIPTION_CANCEL,
            {"id": subscription_id, "prorate": True},
            "appSubscriptionCancel",
        )
        return True

    @staticmethod
    def new_idempotency_key() -> str:
        return f"sr-{uuid.uuid4().hex}"
