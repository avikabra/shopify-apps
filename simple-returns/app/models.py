"""SQLAlchemy models.

Design notes:
- `Shop.access_token_enc` stores the Shopify offline access token encrypted
  with Fernet (see app/security.py). Never log it.
- `ReturnRequest` is OUR domain object. `shopify_return_id` links to the
  Shopify Return object, created only when the merchant (or rules engine)
  approves the request. This keeps Shopify as the source of truth post-approval
  while we own the pre-approval queue.
- Money is stored as Decimal-mapped NUMERIC plus a currency code; JSON blobs
  hold snapshots we don't need to query on.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    FREE = "free"  # $0/mo, 10 returns/mo
    STARTER = "starter"  # $12/mo, 50 returns/mo
    GROWTH = "growth"  # $24/mo, unlimited


PLAN_LIMITS = {
    Plan.FREE: {"price_cents": 0, "monthly_returns": 10},
    Plan.STARTER: {"price_cents": 1200, "monthly_returns": 50},
    Plan.GROWTH: {"price_cents": 2400, "monthly_returns": None},  # None = unlimited
}


# Shopify AppSubscription statuses we treat as "billing is live".
ACTIVE_SUBSCRIPTION_STATUSES = {"ACTIVE", "TRIALING"}


class ReturnStatus(str, enum.Enum):
    REQUESTED = "requested"          # customer submitted, not yet evaluated
    PENDING_REVIEW = "pending_review"  # needs merchant decision
    APPROVED = "approved"            # Shopify return created + approved
    REJECTED = "rejected"            # merchant declined
    LABEL_CREATED = "label_created"  # return label generated
    REFUNDED = "refunded"            # money returned to original payment method
    STORE_CREDITED = "store_credited"  # gift card issued
    EXCHANGED = "exchanged"          # exchange draft order sent
    CLOSED = "closed"                # terminal; nothing further to do


class Resolution(str, enum.Enum):
    REFUND = "refund"
    EXCHANGE = "exchange"
    STORE_CREDIT = "store_credit"


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)  # Fernet-encrypted
    # Expiring offline tokens: Shopify rejects non-expiring tokens for the
    # Admin API, so installs request expiring tokens (1h) plus a refresh
    # token (90d, rotates on use), both stored alongside the access token.
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(String(1024), default="")
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.FREE)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Shopify Billing API: the active AppSubscription GID and its status.
    # Set when the merchant approves a subscription; cleared on downgrade
    # to Free. Synced via the app_subscriptions/update webhook and the
    # /app/billing/callback return URL.
    app_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Merchant-facing settings (branding, defaults). JSON for flexibility.
    settings: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {
            "brand_name": "",
            "support_email": "",
            "default_window_days": 30,
            "auto_email_customer": True,
        },
    )

    return_requests: Mapped[list["ReturnRequest"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    rules: Mapped[list["ReturnRule"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )


class ReturnRule(Base):
    """One auto-approval rule. Evaluated in priority order (lowest first)."""

    __tablename__ = "return_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="Default rule")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    window_days: Mapped[int] = mapped_column(Integer, default=30)
    # Shopify ReturnReason values that this rule auto-approves, e.g.
    # ["DEFECTIVE", "WRONG_ITEM", "SIZE_TOO_SMALL", ...]. Empty = all reasons.
    auto_approve_reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Product GIDs (gid://shopify/Product/...) that are never auto-approved
    # (final sale). Tags excluded similarly.
    excluded_product_ids: Mapped[list] = mapped_column(JSON, default=list)
    excluded_product_tags: Mapped[list] = mapped_column(JSON, default=list)
    # Max total quantity per request this rule will auto-approve.
    max_quantity: Mapped[int] = mapped_column(Integer, default=10)

    shop: Mapped[Shop] = relationship(back_populates="rules")


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    __table_args__ = (
        UniqueConstraint("shop_id", "idempotency_key", name="uq_shop_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)

    # Shopify references
    order_id: Mapped[str] = mapped_column(String(255), index=True)  # gid://shopify/Order/...
    order_name: Mapped[str] = mapped_column(String(64))  # e.g. "#1001"
    shopify_return_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    customer_email: Mapped[str] = mapped_column(String(255), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus), default=ReturnStatus.REQUESTED, index=True
    )
    resolution: Mapped[Resolution] = mapped_column(Enum(Resolution))

    # Snapshot of the requested line items at request time:
    # [{"line_item_id": gid, "fulfillment_line_item_id": gid|None, "product_id": gid|None,
    #   "title": str, "variant_title": str, "sku": str, "quantity": int,
    #   "unit_price": "19.99", "reason": "DEFECTIVE", "note": str,
    #   "exchange_variant_id": gid|None}]
    items: Mapped[list] = mapped_column(JSON, default=list)
    currency: Mapped[str] = mapped_column(String(8), default="USD")

    # Money movement results
    total_refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gift_card_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gift_card_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Label results
    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    label_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Idempotency: one key per request, reused for Shopify mutations.
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(32), default="")  # "rules" | "merchant" | "webhook"
    decline_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    shop: Mapped[Shop] = relationship(back_populates="return_requests")


class WebhookEvent(Base):
    """Append-only audit log of inbound Shopify webhooks."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    shopify_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class EmailLog(Base):
    """Append-only record of emails the app attempted to send."""

    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    return_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("return_requests.id"), nullable=True
    )
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(64))  # e.g. "request_approved"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PlanUsage(Base):
    """Monthly return-request counter per shop for plan enforcement."""

    __tablename__ = "plan_usage"
    __table_args__ = (UniqueConstraint("shop_id", "month", name="uq_shop_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # "2026-10"
    returns_count: Mapped[int] = mapped_column(Integer, default=0)
