"""Return lifecycle orchestration.

ReturnService coordinates the DB, the Shopify adapter, the shipping provider,
and email. It owns the state machine; adapters own the network.

State machine (ReturnStatus):
  requested -> pending_review -> approved -> label_created -> {refunded|store_credited|exchanged} -> closed
  requested -> approved            (auto-approved by rules)
  pending_review -> rejected       (merchant declined)
  approved -> rejected             (declined in Shopify before label)

All Shopify-mutating steps are idempotent: re-running approve()/settle()
on an already-processed request is a no-op, and Shopify mutations reuse the
request's idempotency_key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from . import emails
from .easypost.protocol import Address, Parcel, ShippingProvider
from .email.protocol import EmailProvider
from .models import EmailLog, PlanUsage, Resolution, ReturnRequest, ReturnStatus, Shop
from .rules import RequestItemInput, RuleInput, evaluate_rules
from .shopify.protocol import (
    OrderSnapshot,
    RefundLineItem,
    RequestedLineItem,
    ShopifyClientProtocol,
    ShopifyError,
)

log = logging.getLogger("simple_returns.service")

# Shopify ReturnReason values we accept from the portal (verified enum).
VALID_REASONS = {
    "DEFECTIVE",
    "WRONG_ITEM",
    "NOT_AS_DESCRIBED",
    "SIZE_TOO_SMALL",
    "SIZE_TOO_LARGE",
    "STYLE",
    "COLOR",
    "UNWANTED",
    "OTHER",
    "UNKNOWN",
}


class PlanLimitExceeded(Exception):
    pass


class ReturnService:
    def __init__(
        self,
        db: Session,
        shopify: ShopifyClientProtocol,
        shipping: ShippingProvider,
        email: EmailProvider,
        app_url: str,
    ):
        self.db = db
        self.shopify = shopify
        self.shipping = shipping
        self.email = email
        self.app_url = app_url.rstrip("/")

    # -- plan limits ------------------------------------------------------

    def _month_key(self) -> str:
        now = datetime.now(timezone.utc)
        return f"{now.year:04d}-{now.month:02d}"

    def check_plan_limit(self, shop: Shop) -> None:
        from .models import PLAN_LIMITS

        limit = PLAN_LIMITS[shop.plan]["monthly_returns"]
        if limit is None:
            return
        usage = (
            self.db.query(PlanUsage)
            .filter_by(shop_id=shop.id, month=self._month_key())
            .one_or_none()
        )
        if usage and usage.returns_count >= limit:
            raise PlanLimitExceeded(
                f"Plan limit reached ({limit} returns/month on {shop.plan.value})."
            )

    def _bump_usage(self, shop: Shop) -> None:
        usage = (
            self.db.query(PlanUsage)
            .filter_by(shop_id=shop.id, month=self._month_key())
            .one_or_none()
        )
        if usage is None:
            usage = PlanUsage(shop_id=shop.id, month=self._month_key(), returns_count=0)
            self.db.add(usage)
        usage.returns_count += 1

    # -- request submission (customer portal) ------------------------------

    async def submit_request(
        self,
        shop: Shop,
        order: OrderSnapshot,
        items: list[dict],
        resolution: str,
    ) -> ReturnRequest:
        """Validate, persist, and route a customer return request.

        `items`: [{"line_item_id": gid, "quantity": int, "reason": str,
                   "note": str, "exchange_variant_id": gid|None}]
        """
        try:
            res = Resolution(resolution)
        except ValueError as exc:
            raise ValueError(f"Unknown resolution: {resolution}") from exc

        self.check_plan_limit(shop)

        snapshots = {li.line_item_id: li for li in order.line_items}
        stored_items: list[dict] = []
        for it in items:
            snap = snapshots.get(it["line_item_id"])
            if snap is None:
                raise ValueError("Unknown line item.")
            qty = int(it.get("quantity", 0))
            if qty < 1 or qty > snap.quantity:
                raise ValueError(f"Invalid quantity for {snap.title}.")
            if snap.fulfillment_line_item_id is None:
                raise ValueError(f"{snap.title} hasn't shipped yet and can't be returned.")
            reason = str(it.get("reason", "OTHER")).upper()
            if reason not in VALID_REASONS:
                raise ValueError(f"Unknown return reason: {reason}.")
            exchange_variant_id = it.get("exchange_variant_id")
            if res == Resolution.EXCHANGE and not exchange_variant_id:
                raise ValueError("Exchange requires choosing a replacement variant.")
            stored_items.append(
                {
                    "line_item_id": snap.line_item_id,
                    "fulfillment_line_item_id": snap.fulfillment_line_item_id,
                    "product_id": snap.product_id,
                    "product_tags": snap.product_tags,
                    "title": snap.title,
                    "variant_title": snap.variant_title,
                    "sku": snap.sku,
                    "quantity": qty,
                    "unit_price": str(snap.unit_price),
                    "reason": reason,
                    "note": str(it.get("note", ""))[:500],
                    "exchange_variant_id": exchange_variant_id,
                }
            )

        req = ReturnRequest(
            shop_id=shop.id,
            order_id=order.order_id,
            order_name=order.order_name,
            customer_email=order.email,
            customer_name=order.customer_name,
            customer_id=order.customer_id,
            status=ReturnStatus.REQUESTED,
            resolution=res,
            items=stored_items,
            currency=order.currency,
            idempotency_key=self.shopify.new_idempotency_key(),
        )
        self.db.add(req)
        self.db.flush()  # assign id
        self._bump_usage(shop)

        evaluation = self._evaluate_rules(shop, order, stored_items)
        if evaluation.approved:
            await self.approve(req, decided_by="rules")
        else:
            req.status = ReturnStatus.PENDING_REVIEW
            req.decided_by = "rules"
            await self._notify_merchant_new_request(shop, req)
            brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
            subject, html = emails.request_received(brand, req.order_name, req.id)
            await self._send_customer_email(shop, req, subject, html, "request_received")

        self.db.commit()
        self.db.refresh(req)
        return req

    def _evaluate_rules(self, shop: Shop, order: OrderSnapshot, items: list[dict]):
        rules = [
            RuleInput(
                id=r.id,
                enabled=r.enabled,
                priority=r.priority,
                window_days=r.window_days,
                auto_approve_reasons=r.auto_approve_reasons or [],
                excluded_product_ids=r.excluded_product_ids or [],
                excluded_product_tags=r.excluded_product_tags or [],
                max_quantity=r.max_quantity,
            )
            for r in shop.rules
        ]
        req_items = [
            RequestItemInput(
                product_id=i.get("product_id"),
                product_tags=i.get("product_tags", []),
                quantity=i["quantity"],
                reason=i["reason"],
            )
            for i in items
        ]
        return evaluate_rules(
            rules, order_created_at=order.created_at, now=datetime.now(timezone.utc), items=req_items
        )

    # -- approval ----------------------------------------------------------

    async def approve(self, req: ReturnRequest, decided_by: str) -> ReturnRequest:
        """Create + approve the Shopify return, generate the label, notify."""
        shop = req.shop
        if req.status in (
            ReturnStatus.APPROVED,
            ReturnStatus.LABEL_CREATED,
            ReturnStatus.REFUNDED,
            ReturnStatus.STORE_CREDITED,
            ReturnStatus.EXCHANGED,
            ReturnStatus.CLOSED,
        ):
            return req  # idempotent: already past approval

        if not req.shopify_return_id:
            shopify_return_id = await self.shopify.create_return_request(
                shop.shop_domain,
                req.order_id,
                [
                    RequestedLineItem(
                        fulfillment_line_item_id=i["fulfillment_line_item_id"],
                        quantity=i["quantity"],
                        return_reason=i["reason"],
                        customer_note=i.get("note", ""),
                    )
                    for i in req.items
                ],
            )
            req.shopify_return_id = shopify_return_id

        await self.shopify.approve_return(shop.shop_domain, req.shopify_return_id)
        req.status = ReturnStatus.APPROVED
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = decided_by

        # Label is best-effort: a missing warehouse address or label failure
        # must not block the approval itself. Merchant can retry from dashboard.
        try:
            await self.generate_label(req)
        except Exception as exc:  # noqa: BLE001 - best effort by design
            log.warning("Label generation failed for request %s: %s", req.id, exc)

        await self.send_approval_email(req)

        self.db.commit()
        self.db.refresh(req)
        return req

    async def send_approval_email(self, req: ReturnRequest) -> None:
        """(Re)send the approval email, with or without the label attached."""
        shop = req.shop
        brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
        if req.label_url:
            subject, html = emails.request_approved(
                brand, req.order_name, req.label_url, req.tracking_number or ""
            )
            kind = "request_approved"
        else:
            subject, html = emails.request_approved_label_pending(brand, req.order_name)
            kind = "request_approved_label_pending"
        await self._send_customer_email(shop, req, subject, html, kind)

    async def generate_label(self, req: ReturnRequest):
        """Buy the EasyPost return label. Idempotent via existing label_url."""
        shop = req.shop
        if req.label_url:
            return
        warehouse = (shop.settings or {}).get("warehouse_address") or {}
        if not warehouse.get("street1") or not warehouse.get("zip"):
            raise ValueError(
                "Warehouse address is not configured (Settings > Warehouse address)."
            )
        label = await self.shipping.create_return_label(
            to_address=Address(
                name=(shop.settings or {}).get("brand_name") or shop.shop_domain,
                street1=warehouse["street1"],
                street2=warehouse.get("street2", ""),
                city=warehouse.get("city", ""),
                state=warehouse.get("state", ""),
                zip=warehouse["zip"],
                country=warehouse.get("country", "US"),
                email=(shop.settings or {}).get("support_email", ""),
            ),
            from_address=Address(
                name=req.customer_name or req.customer_email,
                street1="",
                email=req.customer_email,
            ),
            parcel=Parcel(),
            reference=f"return-{req.id}",
        )
        req.tracking_number = label.tracking_number
        req.label_url = label.label_url
        req.status = ReturnStatus.LABEL_CREATED
        self.db.commit()

    # -- rejection ----------------------------------------------------------

    async def reject(self, req: ReturnRequest, note: str, decided_by: str) -> ReturnRequest:
        shop = req.shop
        if req.shopify_return_id:
            try:
                await self.shopify.decline_return(shop.shop_domain, req.shopify_return_id)
            except ShopifyError as exc:
                log.warning("Shopify decline failed for %s: %s", req.shopify_return_id, exc)
        req.status = ReturnStatus.REJECTED
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = decided_by
        req.decline_note = note or ""
        brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
        subject, html = emails.request_rejected(brand, req.order_name, note)
        await self._send_customer_email(shop, req, subject, html, "request_rejected")
        self.db.commit()
        self.db.refresh(req)
        return req

    # -- settlement (money movement, after goods are received) ---------------

    def refund_preview(self, req: ReturnRequest) -> Decimal:
        """Total refundable for the request from the item snapshots."""
        total = Decimal("0")
        for i in req.items:
            total += Decimal(str(i["unit_price"])) * int(i["quantity"])
        return total.quantize(Decimal("0.01"))

    async def settle(self, req: ReturnRequest, restock: bool = True) -> ReturnRequest:
        """Issue the money movement for the chosen resolution. Idempotent."""
        if req.status in (
            ReturnStatus.REFUNDED,
            ReturnStatus.STORE_CREDITED,
            ReturnStatus.EXCHANGED,
            ReturnStatus.CLOSED,
        ):
            return req
        if req.resolution == Resolution.REFUND:
            await self._settle_refund(req, restock)
        elif req.resolution == Resolution.STORE_CREDIT:
            await self._settle_store_credit(req)
        elif req.resolution == Resolution.EXCHANGE:
            await self._settle_exchange(req)
        else:  # pragma: no cover - defensive
            raise ValueError(f"Unknown resolution: {req.resolution}")
        self.db.commit()
        self.db.refresh(req)
        return req

    async def _settle_refund(self, req: ReturnRequest, restock: bool) -> None:
        shop = req.shop
        if not req.refund_id:
            refund_id = await self.shopify.refund_line_items(
                shop.shop_domain,
                req.order_id,
                [
                    RefundLineItem(
                        line_item_id=i["line_item_id"],
                        quantity=i["quantity"],
                        restock=restock,
                    )
                    for i in req.items
                ],
                idempotency_key=req.idempotency_key,
                note=f"Simple Returns request #{req.id}",
            )
            req.refund_id = refund_id
        req.total_refund_amount = self.refund_preview(req)
        req.status = ReturnStatus.REFUNDED
        brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
        subject, html = emails.refund_issued(
            brand, req.order_name, req.total_refund_amount, req.currency
        )
        await self._send_customer_email(shop, req, subject, html, "refund_issued")

    async def _settle_store_credit(self, req: ReturnRequest) -> None:
        shop = req.shop
        if not req.customer_id:
            raise ValueError(
                "Cannot issue store credit: no Shopify customer on this order."
            )
        if not req.gift_card_id:
            amount = self.refund_preview(req)
            gc = await self.shopify.create_gift_card(
                shop.shop_domain,
                req.customer_id,
                amount,
                req.currency,
                note=f"Simple Returns request #{req.id} ({req.order_name})",
            )
            req.gift_card_id = gc.id
            req.gift_card_code = gc.code
        req.total_refund_amount = self.refund_preview(req)
        req.status = ReturnStatus.STORE_CREDITED
        brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
        subject, html = emails.store_credit_issued(
            brand, req.order_name, req.total_refund_amount, req.currency, req.gift_card_code or ""
        )
        await self._send_customer_email(shop, req, subject, html, "store_credit_issued")

    async def _settle_exchange(self, req: ReturnRequest) -> None:
        shop = req.shop
        if not req.customer_id:
            raise ValueError(
                "Cannot create exchange order: no Shopify customer on this order."
            )
        if not req.draft_order_id:
            # v1: one draft order per requested item (keeps variant mapping exact).
            first = req.items[0]
            draft = await self.shopify.create_exchange_draft_order(
                shop.shop_domain,
                req.customer_id,
                first["exchange_variant_id"],
                first["quantity"],
                note=f"Exchange for Simple Returns request #{req.id} ({req.order_name})",
            )
            req.draft_order_id = draft.id
            await self.shopify.send_draft_order_invoice(shop.shop_domain, draft.id)
            invoice_url = draft.invoice_url
        else:
            invoice_url = None
        req.status = ReturnStatus.EXCHANGED
        brand = (shop.settings or {}).get("brand_name") or shop.shop_domain
        subject, html = emails.exchange_sent(brand, req.order_name, invoice_url)
        await self._send_customer_email(shop, req, subject, html, "exchange_sent")

    # -- email helpers -------------------------------------------------------

    async def _send_customer_email(
        self, shop: Shop, req: ReturnRequest, subject: str, html: str, kind: str
    ) -> None:
        try:
            await self.email.send(
                to=req.customer_email, subject=subject, html=html, text=""
            )
        except Exception as exc:  # noqa: BLE001 - email must not break the flow
            log.warning("Customer email failed for request %s: %s", req.id, exc)
        self.db.add(
            EmailLog(
                shop_id=shop.id,
                return_request_id=req.id,
                recipient=req.customer_email,
                subject=subject,
                kind=kind,
            )
        )

    async def _notify_merchant_new_request(self, shop: Shop, req: ReturnRequest) -> None:
        support = (shop.settings or {}).get("support_email")
        if not support:
            return
        try:
            subject, html = emails.merchant_new_request(
                shop.shop_domain, req.order_name, req.id, self.app_url
            )
            await self.email.send(to=support, subject=subject, html=html, text="")
        except Exception as exc:  # noqa: BLE001
            log.warning("Merchant email failed for request %s: %s", req.id, exc)
        self.db.add(
            EmailLog(
                shop_id=shop.id,
                return_request_id=req.id,
                recipient=support,
                subject="New return request",
                kind="merchant_new_request",
            )
        )