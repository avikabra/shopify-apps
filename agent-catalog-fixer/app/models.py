"""SQLAlchemy models."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


ACTIVE_SUBSCRIPTION_STATUSES = {"ACTIVE", "TRIALING"}


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Expiring offline tokens: Shopify rejects non-expiring tokens for the
    # Admin API, so installs request expiring tokens (1h) plus a refresh
    # token (90d, rotates on use), both stored alongside the access token.
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.FREE)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Shopify Billing API: the active AppSubscription GID and its status.
    # Set when the merchant approves a subscription; cleared on downgrade
    # to Free. Synced via the app_subscriptions/update webhook and the
    # /app/billing/callback return URL.
    app_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    audits: Mapped[list["ProductAudit"]] = relationship(back_populates="shop", cascade="all, delete-orphan")
    runs: Mapped[list["AuditRun"]] = relationship(back_populates="shop", cascade="all, delete-orphan")


class AuditRun(Base):
    """One full-catalog audit pass for a shop."""

    __tablename__ = "audit_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    products_scored: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|done|failed

    shop: Mapped[Shop] = relationship(back_populates="runs")


class ProductAudit(Base):
    """Latest audit result for one product in a shop."""

    __tablename__ = "product_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(64), index=True)  # Shopify numeric ID as string
    product_gid: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[list] = mapped_column(JSON, default=list)  # [{code, severity, message, fix, auto_fixable}]
    fixes_applied: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # product data at audit time
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    shop: Mapped[Shop] = relationship(back_populates="audits")


class FaqEntry(Base):
    """Merchant-approved FAQs per product, used for FAQPage JSON-LD."""

    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
