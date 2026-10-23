"""Channel evaluation + margin-guard simulator.

This module is the heart of Agent Channel Manager. It answers two questions:

1. `evaluate_channel(products, shop, db)` — "What does an AI agent see?"
   Applies visibility rules (blocklist/allowlist) then pricing rules to each
   product, returning a per-product view: visible?, storefront price, agent
   price, which rules applied, margin estimate.

2. `simulate_margin(...)` — "What does this cost / protect me?"
   Aggregates the channel view into totals the merchant can act on.

GOOGLE SHOPPING PRICE-MATCH CONSTRAINT
--------------------------------------
Google's price-accuracy policy (and Shopify's Google & YouTube channel terms)
require the price a shopper pays at checkout to match the advertised price in
the product feed. If an AI agent checks out at a price BELOW the feed price,
the merchant risks feed disapproval.

Therefore this app enforces the following:
- A DISCOUNT_PCT pricing rule may never set an agent price below the
  storefront price *unless* the merchant explicitly acknowledges the feed risk
  (the UI surfaces a warning; the rule stores `acknowledged_feed_risk` via the
  `reason` field convention — see admin route validation).
- The simulator flags every product whose agent price < storefront price as
  FEED_RISK so the merchant can see the exposure in one place.
- The recommended "win on value, not price" levers are agent-only promotions
  (bundles, free-shipping thresholds), which do not change the advertised
  base price and therefore do not trip price-accuracy checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from .models import (
    AgentPromotion,
    ChannelMode,
    PricingRule,
    PricingRuleType,
    PricingScope,
    PromotionType,
    Shop,
    VisibilityAction,
    VisibilityRule,
)
from .shopify.protocol import ProductSnapshot

_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass
class ProductChannelView:
    product: ProductSnapshot
    visible: bool
    visibility_reason: str = ""
    storefront_price: Decimal = Decimal("0")
    agent_price: Decimal = Decimal("0")
    applied_pricing_rules: list[str] = field(default_factory=list)
    excluded_codes: list[str] = field(default_factory=list)
    feed_risk: bool = False  # agent_price < storefront_price
    margin_amount: Decimal | None = None  # agent_price - unit_cost
    margin_pct: Decimal | None = None


@dataclass
class ChannelSummary:
    total_products: int = 0
    visible_products: int = 0
    hidden_products: int = 0
    avg_discount_pct: Decimal = Decimal("0")  # avg (storefront-agent)/storefront over visible
    feed_risk_count: int = 0
    estimated_margin_total: Decimal | None = None  # sum of per-product margins (1 unit each)


def _visibility_lookup(shop: Shop, db: Session) -> dict[str, VisibilityRule]:
    rules = db.query(VisibilityRule).filter_by(shop_id=shop.id).all()
    # Last-write-wins per gid is enforced by unique constraint; dict is fine.
    return {r.subject_gid: r for r in rules}


def is_visible(
    product: ProductSnapshot,
    shop: Shop,
    lookup: dict[str, VisibilityRule],
) -> tuple[bool, str]:
    """Decide whether an AI agent may discover this product."""
    # Collection-level rules: any HIDE on a containing collection hides the
    # product in blocklist mode; any SHOW on a containing collection shows it
    # in allowlist mode. Product-level rules take precedence over collections.
    product_rule = lookup.get(product.product_id)

    def collection_rule() -> VisibilityRule | None:
        for cid in product.collection_ids:
            r = lookup.get(cid)
            if r is not None:
                return r
        return None

    if shop.channel_mode == ChannelMode.BLOCKLIST:
        if product_rule and product_rule.action == VisibilityAction.HIDE:
            return False, f"hidden by rule: {product_rule.reason or product_rule.subject_title}"
        if product_rule and product_rule.action == VisibilityAction.SHOW:
            return True, "explicitly shown"
        coll = collection_rule()
        if coll and coll.action == VisibilityAction.HIDE:
            return False, f"hidden via collection '{coll.subject_title}'"
        return True, "visible (blocklist default)"
    else:  # ALLOWLIST
        if product_rule and product_rule.action == VisibilityAction.SHOW:
            return True, "explicitly shown"
        if product_rule and product_rule.action == VisibilityAction.HIDE:
            return False, "explicitly hidden"
        coll = collection_rule()
        if coll and coll.action == VisibilityAction.SHOW:
            return True, f"shown via collection '{coll.subject_title}'"
        return False, "hidden (allowlist default)"


def _applicable_pricing_rules(
    product: ProductSnapshot, shop: Shop, db: Session
) -> list[PricingRule]:
    rules = (
        db.query(PricingRule)
        .filter_by(shop_id=shop.id, enabled=True)
        .order_by(PricingRule.priority.asc(), PricingRule.id.asc())
        .all()
    )
    out = []
    for r in rules:
        if r.scope == PricingScope.ALL:
            out.append(r)
        elif r.scope == PricingScope.PRODUCT and r.target_gid == product.product_id:
            out.append(r)
        elif r.scope == PricingScope.COLLECTION and r.target_gid in product.collection_ids:
            out.append(r)
    return out


def apply_pricing(
    storefront_price: Decimal, rules: list[PricingRule]
) -> tuple[Decimal, list[str], list[str]]:
    """Apply pricing rules in priority order.

    Returns (agent_price, applied_rule_names, excluded_codes).
    Rule interaction semantics:
    - FLOOR: agent_price = max(agent_price, floor)
    - MARKUP_PCT: agent_price += agent_price * pct/100
    - DISCOUNT_PCT: agent_price -= agent_price * pct/100
    - CODE_EXCLUSION: no price effect; code recorded for checkout enforcement.
    """
    price = storefront_price
    applied: list[str] = []
    excluded: list[str] = []
    for r in rules:
        # r.value may be None on transient (not-yet-flushed) rule objects;
        # the DB column default (0) only applies on INSERT.
        value = Decimal(str(r.value if r.value is not None else 0))
        if r.rule_type == PricingRuleType.FLOOR:
            if price < value:
                price = value
                applied.append(r.name)
        elif r.rule_type == PricingRuleType.MARKUP_PCT:
            price = price * (1 + value / 100)
            applied.append(r.name)
        elif r.rule_type == PricingRuleType.DISCOUNT_PCT:
            price = price * (1 - value / 100)
            applied.append(r.name)
        elif r.rule_type == PricingRuleType.CODE_EXCLUSION:
            if r.excluded_code:
                excluded.append(r.excluded_code)
            applied.append(r.name)
    return _money(max(price, Decimal("0"))), applied, excluded


def evaluate_channel(
    products: list[ProductSnapshot], shop: Shop, db: Session
) -> list[ProductChannelView]:
    """Build the "what does an AI agent see" view for a product list."""
    lookup = _visibility_lookup(shop, db)
    views: list[ProductChannelView] = []
    for product in products:
        visible, reason = is_visible(product, shop, lookup)
        storefront = _money(product.price)
        agent_price = storefront
        applied: list[str] = []
        excluded: list[str] = []
        if visible:
            rules = _applicable_pricing_rules(product, shop, db)
            agent_price, applied, excluded = apply_pricing(storefront, rules)
        feed_risk = visible and agent_price < storefront
        margin_amount = None
        margin_pct = None
        if visible and product.unit_cost is not None:
            margin_amount = _money(agent_price - product.unit_cost)
            if agent_price > 0:
                margin_pct = _money((agent_price - product.unit_cost) / agent_price * 100)
        views.append(
            ProductChannelView(
                product=product,
                visible=visible,
                visibility_reason=reason,
                storefront_price=storefront,
                agent_price=agent_price,
                applied_pricing_rules=applied,
                excluded_codes=excluded,
                feed_risk=feed_risk,
                margin_amount=margin_amount,
                margin_pct=margin_pct,
            )
        )
    return views


def summarize(views: list[ProductChannelView]) -> ChannelSummary:
    total = len(views)
    visible_views = [v for v in views if v.visible]
    discounts: list[Decimal] = []
    margin_total = Decimal("0")
    margin_known = False
    for v in visible_views:
        if v.storefront_price > 0:
            discounts.append(
                (v.storefront_price - v.agent_price) / v.storefront_price * 100
            )
        if v.margin_amount is not None:
            margin_total += v.margin_amount
            margin_known = True
    avg_discount = (
        _money(sum(discounts) / len(discounts)) if discounts else Decimal("0")
    )
    return ChannelSummary(
        total_products=total,
        visible_products=len(visible_views),
        hidden_products=total - len(visible_views),
        avg_discount_pct=avg_discount,
        feed_risk_count=sum(1 for v in visible_views if v.feed_risk),
        estimated_margin_total=_money(margin_total) if margin_known else None,
    )


# -- promotions -----------------------------------------------------------

def bundle_unit_price(promo: AgentPromotion) -> Decimal | None:
    """Effective per-bundle price from a BUNDLE promotion config."""
    try:
        return _money(Decimal(str(promo.config.get("bundle_price", "0"))))
    except Exception:
        return None


def free_shipping_threshold(promo: AgentPromotion) -> Decimal | None:
    try:
        return _money(Decimal(str(promo.config.get("threshold", "0"))))
    except Exception:
        return None


def validate_promotion_config(promo_type: PromotionType, config: dict) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    if promo_type == PromotionType.BUNDLE:
        gids = config.get("product_gids") or []
        if not isinstance(gids, list) or len(gids) < 2:
            errors.append("A bundle needs at least 2 products.")
        try:
            price = Decimal(str(config.get("bundle_price", "")))
            if price <= 0:
                errors.append("Bundle price must be greater than zero.")
        except Exception:
            errors.append("Bundle price must be a valid amount.")
    elif promo_type == PromotionType.FREE_SHIPPING:
        try:
            threshold = Decimal(str(config.get("threshold", "")))
            if threshold <= 0:
                errors.append("Free-shipping threshold must be greater than zero.")
        except Exception:
            errors.append("Free-shipping threshold must be a valid amount.")
    else:
        errors.append(f"Unknown promotion type: {promo_type}")
    return errors


def promotion_discount_code(promo: AgentPromotion, shop_domain: str) -> str:
    """Deterministic, unguessable-ish code for an agent-only promotion.

    Codes are derived from the promotion id + shop so they are stable across
    re-syncs, and prefixed so merchants can recognize them in Shopify admin.
    They are never rendered on the storefront by this app.
    """
    return f"AGENT-{promo.id:06d}-{abs(hash((shop_domain, promo.id))) % 10000:04d}"
