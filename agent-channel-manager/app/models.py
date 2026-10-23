"""SQLAlchemy models.

Design notes:
- `Shop.access_token_enc` stores the Shopify offline access token encrypted
  with Fernet (see app/security.py). Never log it.
- `Shop.channel_mode` is "blocklist" (agents see everything except hidden
  items) or "allowlist" (agents see only explicitly shown items).
- Visibility is enforced at the app's agent-channel layer (the agent sales
  channel / Catalog publication), never by mutating the merchant's catalog.
- Money is stored as Decimal-mapped NUMERIC plus a currency code.
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    FREE = "free"  # $0, limited rules
    PRO = "pro"    # $19/mo, unlimited


PLAN_LIMITS = {
    # None = unlimited
    Plan.FREE: {
        "price_cents": 0,
        "visibility_rules": 10,
        "pricing_rules": 2,
        "promotions": 1,
    },
    Plan.PRO: {
        "price_cents": 1900,
        "visibility_rules": None,
        "pricing_rules": None,
        "promotions": None,
    },
}

# Subscription statuses that mean the merchant IS paying (or trialing).
ACTIVE_SUBSCRIPTION_STATUSES = {"ACTIVE", "TRIALING"}


class ChannelMode(str, enum.Enum):
    BLOCKLIST = "blocklist"  # agents see everything except hidden items
    ALLOWLIST = "allowlist"  # agents see only explicitly shown items


class VisibilityAction(str, enum.Enum):
    HIDE = "hide"
    SHOW = "show"


class PricingRuleType(str, enum.Enum):
    FLOOR = "floor"                  # minimum price agents will see
    MARKUP_PCT = "markup_pct"        # +% vs storefront price
    DISCOUNT_PCT = "discount_pct"    # -% vs storefront price
    CODE_EXCLUSION = "code_exclusion"  # discount codes agents may NOT use


class PricingScope(str, enum.Enum):
    ALL = "all"
    PRODUCT = "product"
    COLLECTION = "collection"


class PromotionType(str, enum.Enum):
    BUNDLE = "bundle"                # bundle of products at a set price, agents only
    FREE_SHIPPING = "free_shipping"  # free-shipping threshold for agent orders


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

    channel_mode: Mapped[ChannelMode] = mapped_column(
        Enum(ChannelMode), default=ChannelMode.BLOCKLIST
    )

    settings: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {"brand_name": ""},
    )

    visibility_rules: Mapped[list["VisibilityRule"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    pricing_rules: Mapped[list["PricingRule"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    promotions: Mapped[list["AgentPromotion"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )


class VisibilityRule(Base):
    """Hide or show one product/collection for the agent channel."""

    __tablename__ = "visibility_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), default="product")
    subject_gid: Mapped[str] = mapped_column(String(255), index=True)
    subject_title: Mapped[str] = mapped_column(String(512), default="")
    action: Mapped[VisibilityAction] = mapped_column(Enum(VisibilityAction))
    reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    shop: Mapped[Shop] = relationship(back_populates="visibility_rules")

    __table_args__ = (
        UniqueConstraint("shop_id", "subject_gid", "action", name="uq_vis_shop_gid_action"),
    )


class PricingRule(Base):
    """Agent-specific pricing rule.

    GOOGLE SHOPPING PRICE-MATCH CONSTRAINT (see services.py):
    Agent prices must never undercut the merchant's advertised Google Shopping
    feed price for the same item. Shopify/Google policy treats a checkout price
    lower than the advertised price as a price-accuracy violation and can
    disapprove the product feed. The app therefore:
      * warns whenever an agent price would fall BELOW the storefront price,
      * BLOCKS discount rules that would push the agent price below a floor
        unless the merchant explicitly acknowledges the feed risk,
      * recommends achieving "agent wins on value" via agent-only promotions
        (bundles, free shipping) instead of base-price cuts.
    """

    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="Pricing rule")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rule_type: Mapped[PricingRuleType] = mapped_column(Enum(PricingRuleType))
    scope: Mapped[PricingScope] = mapped_column(Enum(PricingScope), default=PricingScope.ALL)
    target_gid: Mapped[str] = mapped_column(String(255), default="")
    target_title: Mapped[str] = mapped_column(String(512), default="")
    # Semantics depend on rule_type:
    #   floor        -> minimum agent price (currency amount)
    #   markup_pct   -> percent added to storefront price (e.g. 5 = +5%)
    #   discount_pct -> percent taken off storefront price (e.g. 10 = -10%)
    #   code_exclusion -> ignored (see excluded_code)
    value: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    # For CODE_EXCLUSION: the discount code agents may not use.
    excluded_code: Mapped[str] = mapped_column(String(128), default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    shop: Mapped[Shop] = relationship(back_populates="pricing_rules")


class AgentPromotion(Base):
    """Agent-only promotion, synced to Shopify as a discount."""

    __tablename__ = "agent_promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="Agent promotion")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    promo_type: Mapped[PromotionType] = mapped_column(Enum(PromotionType))
    # JSON config:
    #   bundle:        {"product_gids": [...], "bundle_price": "49.99", "currency": "USD"}
    #   free_shipping: {"threshold": "75.00", "currency": "USD"}
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Shopify discount ID once synced (discountCodeBasic / discountAutomaticBasic).
    shopify_discount_id: Mapped[str] = mapped_column(String(255), default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    shop: Mapped[Shop] = relationship(back_populates="promotions")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
