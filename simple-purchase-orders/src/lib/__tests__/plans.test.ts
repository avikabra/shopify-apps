import { describe, expect, it } from 'vitest';
import {
  PLANS,
  PLAN_ORDER,
  isPlanKey,
  normalizePlan,
  planAtLeast,
  canUseFeature,
  planFromSubscriptionName,
  skuLimitLabel,
  FEATURE_MIN_PLAN,
  type PlanKey,
} from '../plans.js';

describe('plan catalog', () => {
  it('defines free, starter, and growth in ascending order', () => {
    expect(PLAN_ORDER).toEqual(['free', 'starter', 'growth']);
    expect(PLANS.free.price).toBe(0);
    expect(PLANS.starter.price).toBe(9);
    expect(PLANS.growth.price).toBe(19);
  });

  it('gives paid plans a 14-day trial and free none', () => {
    expect(PLANS.free.trialDays).toBe(0);
    expect(PLANS.starter.trialDays).toBe(14);
    expect(PLANS.growth.trialDays).toBe(14);
  });

  it('sets SKU limits 50 / 200 / unlimited', () => {
    expect(PLANS.free.skuLimit).toBe(50);
    expect(PLANS.starter.skuLimit).toBe(200);
    expect(PLANS.growth.skuLimit).toBe(Infinity);
  });
});

describe('isPlanKey / normalizePlan', () => {
  it('accepts the three known keys', () => {
    for (const k of ['free', 'starter', 'growth']) expect(isPlanKey(k)).toBe(true);
  });

  it('rejects unknown, null, and non-string values', () => {
    expect(isPlanKey('enterprise')).toBe(false);
    expect(isPlanKey(null)).toBe(false);
    expect(isPlanKey(undefined)).toBe(false);
    expect(isPlanKey(42)).toBe(false);
  });

  it('normalizes unknown values to free', () => {
    expect(normalizePlan('starter')).toBe('starter');
    expect(normalizePlan('bogus')).toBe('free');
    expect(normalizePlan(null)).toBe('free');
    expect(normalizePlan(undefined)).toBe('free');
  });
});

describe('planAtLeast', () => {
  it('is reflexive', () => {
    for (const p of PLAN_ORDER) expect(planAtLeast(p, p)).toBe(true);
  });

  it('orders free < starter < growth', () => {
    expect(planAtLeast('starter', 'free')).toBe(true);
    expect(planAtLeast('growth', 'starter')).toBe(true);
    expect(planAtLeast('growth', 'free')).toBe(true);
    expect(planAtLeast('free', 'starter')).toBe(false);
    expect(planAtLeast('starter', 'growth')).toBe(false);
  });
});

describe('feature gates', () => {
  it('requires starter for alerts and pdf, growth for transfers', () => {
    expect(FEATURE_MIN_PLAN.alerts).toBe('starter');
    expect(FEATURE_MIN_PLAN.pdf).toBe('starter');
    expect(FEATURE_MIN_PLAN.transfers).toBe('growth');
  });

  it('free can use nothing gated', () => {
    expect(canUseFeature('free', 'alerts')).toBe(false);
    expect(canUseFeature('free', 'pdf')).toBe(false);
    expect(canUseFeature('free', 'transfers')).toBe(false);
  });

  it('starter unlocks alerts and pdf but not transfers', () => {
    expect(canUseFeature('starter', 'alerts')).toBe(true);
    expect(canUseFeature('starter', 'pdf')).toBe(true);
    expect(canUseFeature('starter', 'transfers')).toBe(false);
  });

  it('growth unlocks everything', () => {
    const g: PlanKey = 'growth';
    expect(canUseFeature(g, 'alerts')).toBe(true);
    expect(canUseFeature(g, 'pdf')).toBe(true);
    expect(canUseFeature(g, 'transfers')).toBe(true);
  });
});

describe('planFromSubscriptionName', () => {
  it('maps the names created by createSubscription', () => {
    expect(planFromSubscriptionName('Simple Purchase Orders — Starter')).toBe('starter');
    expect(planFromSubscriptionName('Simple Purchase Orders — Growth')).toBe('growth');
  });

  it('is case-insensitive', () => {
    expect(planFromSubscriptionName('my app - STARTER')).toBe('starter');
  });

  it('returns null for unknown or empty names', () => {
    expect(planFromSubscriptionName('Something Else')).toBe(null);
    expect(planFromSubscriptionName('')).toBe(null);
    expect(planFromSubscriptionName(null)).toBe(null);
    expect(planFromSubscriptionName(undefined)).toBe(null);
  });
});

describe('skuLimitLabel', () => {
  it('renders numbers and Unlimited', () => {
    expect(skuLimitLabel('free')).toBe('50');
    expect(skuLimitLabel('starter')).toBe('200');
    expect(skuLimitLabel('growth')).toBe('Unlimited');
  });
});
