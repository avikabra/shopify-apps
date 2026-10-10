"""Unit tests for the pure rules engine (app/rules.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.rules import RequestItemInput, RuleEvaluation, RuleInput, evaluate_rules

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def make_rule(**overrides):
    base = dict(
        id=1,
        enabled=True,
        priority=0,
        window_days=30,
        auto_approve_reasons=[],
        excluded_product_ids=[],
        excluded_product_tags=["final-sale"],
        max_quantity=10,
    )
    base.update(overrides)
    return RuleInput(**base)


def make_item(**overrides):
    base = dict(
        product_id="gid://shopify/Product/21",
        product_tags=["apparel"],
        quantity=1,
        reason="DEFECTIVE",
    )
    base.update(overrides)
    return RequestItemInput(**base)


def evaluate(rules, order_age_days=10, items=None):
    return evaluate_rules(
        rules,
        order_created_at=NOW - timedelta(days=order_age_days),
        now=NOW,
        items=items if items is not None else [make_item()],
    )


def test_auto_approves_within_window():
    result = evaluate([make_rule()])
    assert result.approved is True
    assert result.rule_id == 1


def test_rejects_outside_window():
    result = evaluate([make_rule()], order_age_days=45)
    assert result.approved is False
    assert "30-day" in result.reasons[0]


def test_boundary_day_is_inside_window():
    result = evaluate([make_rule()], order_age_days=30)
    assert result.approved is True


def test_excluded_tag_blocks():
    result = evaluate([make_rule()], items=[make_item(product_tags=["Final-Sale"])])
    assert result.approved is False
    assert "excluded tag" in result.reasons[0]


def test_excluded_product_id_blocks():
    result = evaluate(
        [make_rule(excluded_product_ids=["gid://shopify/Product/21"])],
    )
    assert result.approved is False


def test_quantity_over_max_blocks():
    result = evaluate(
        [make_rule(max_quantity=2)], items=[make_item(quantity=2), make_item(quantity=1)]
    )
    assert result.approved is False
    assert "quantity" in result.reasons[0].lower()


def test_reason_allowlist():
    rule = make_rule(auto_approve_reasons=["DEFECTIVE", "WRONG_ITEM"])
    assert evaluate([rule]).approved is True
    assert evaluate([rule], items=[make_item(reason="UNWANTED")]).approved is False


def test_reason_matching_is_case_insensitive():
    rule = make_rule(auto_approve_reasons=["defective"])
    assert evaluate([rule], items=[make_item(reason="DEFECTIVE")]).approved is True


def test_disabled_rules_are_skipped():
    result = evaluate([make_rule(enabled=False)])
    assert result.approved is False
    assert "No auto-approval rules are enabled" in result.reasons[0]


def test_no_rules_means_manual_review():
    result = evaluate([])
    assert result.approved is False


def test_priority_order_first_match_wins():
    strict = make_rule(id=1, priority=5, window_days=7)     # would reject (10d old)
    loose = make_rule(id=2, priority=1, window_days=60)     # matches
    result = evaluate([strict, loose])
    assert result.approved is True
    assert result.rule_id == 2


def test_empty_items_never_approve():
    result = evaluate([make_rule()], items=[])
    assert result.approved is False


def test_naive_datetimes_treated_as_utc():
    naive_now = datetime(2026, 10, 1, 12, 0)
    result = evaluate_rules(
        [make_rule()],
        order_created_at=naive_now - timedelta(days=10),
        now=naive_now,
        items=[make_item()],
    )
    assert result.approved is True


def test_evaluation_result_shape():
    result = evaluate([make_rule()])
    assert isinstance(result, RuleEvaluation)
    assert isinstance(result.reasons, list) and result.reasons
