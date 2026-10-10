"""Rules engine: decides whether a return request is auto-approved.

Pure functions — no DB, no network — so the whole thing is unit-testable.
A request is auto-approved iff at least one ENABLED rule matches it fully:

- the order is within the rule's return window (days since order creation)
- every requested reason is in the rule's auto_approve_reasons
  (empty list = all reasons allowed)
- no requested item is in excluded_product_ids / excluded_product_tags
- total quantity is within the rule's max_quantity

Rules are evaluated in priority order (lowest first); the first fully-matching
rule wins. If none match, the request goes to the merchant review queue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RuleInput:
    id: int
    enabled: bool
    priority: int
    window_days: int
    auto_approve_reasons: list[str] = field(default_factory=list)
    excluded_product_ids: list[str] = field(default_factory=list)
    excluded_product_tags: list[str] = field(default_factory=list)
    max_quantity: int = 10


@dataclass
class RequestItemInput:
    product_id: str | None
    product_tags: list[str]
    quantity: int
    reason: str  # Shopify ReturnReason value


@dataclass
class RuleEvaluation:
    approved: bool
    rule_id: int | None
    reasons: list[str]  # human-readable explanation of the outcome


def evaluate_rules(
    rules: list[RuleInput],
    *,
    order_created_at: datetime,
    now: datetime,
    items: list[RequestItemInput],
) -> RuleEvaluation:
    """Evaluate enabled rules in priority order; first full match wins."""
    enabled = sorted(
        (r for r in rules if r.enabled), key=lambda r: (r.priority, r.id)
    )
    if not enabled:
        return RuleEvaluation(False, None, ["No auto-approval rules are enabled."])

    for rule in enabled:
        failure = _rule_failure_reason(rule, order_created_at, now, items)
        if failure is None:
            return RuleEvaluation(
                True,
                rule.id,
                [f"Auto-approved by rule #{rule.id} (priority {rule.priority})."],
            )
    # None matched: explain using the highest-priority rule's failure, which is
    # the most actionable for the merchant.
    failure = _rule_failure_reason(enabled[0], order_created_at, now, items)
    return RuleEvaluation(False, None, [failure or "No rule matched."])


def _rule_failure_reason(
    rule: RuleInput,
    order_created_at: datetime,
    now: datetime,
    items: list[RequestItemInput],
) -> str | None:
    """Return None if the rule fully matches, else a human-readable reason."""
    if not items:
        return "The request contains no items."

    created = _as_aware(order_created_at)
    current = _as_aware(now)
    age_days = (current - created).total_seconds() / 86400
    if age_days > rule.window_days:
        return (
            f"Order is {age_days:.0f} days old, outside the "
            f"{rule.window_days}-day return window."
        )

    if rule.auto_approve_reasons:
        allowed = {r.upper() for r in rule.auto_approve_reasons}
        bad = [i.reason for i in items if i.reason.upper() not in allowed]
        if bad:
            return (
                f"Reason(s) {sorted(set(bad))} are not in this rule's "
                "auto-approve list."
            )

    excluded_ids = set(rule.excluded_product_ids)
    excluded_tags = {t.lower() for t in rule.excluded_product_tags}
    for item in items:
        if item.product_id and item.product_id in excluded_ids:
            return "One or more items are excluded from auto-approval (final sale)."
        if excluded_tags and {t.lower() for t in item.product_tags} & excluded_tags:
            return "One or more items carry an excluded tag (final sale)."

    total_qty = sum(i.quantity for i in items)
    if total_qty > rule.max_quantity:
        return (
            f"Requested quantity ({total_qty}) exceeds this rule's "
            f"limit of {rule.max_quantity}."
        )

    return None


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
