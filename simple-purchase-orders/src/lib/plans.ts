/**
 * Freemium plan catalog for Simple Purchase Orders & Stock Alerts.
 *
 * Pure logic only (no DB, no network) so it is trivially unit-testable.
 * The Shopify-facing subscription calls live in ../shopify/billing.js.
 */

export type PlanKey = 'free' | 'starter' | 'growth';

export interface PlanDef {
  key: PlanKey;
  name: string;
  /** Monthly USD price. 0 = free tier, no Shopify charge created. */
  price: number;
  /** Free trial days for paid plans. */
  trialDays: number;
  /** Max distinct SKUs the shop may track. Infinity = unlimited. */
  skuLimit: number;
  /** Short marketing blurb for the billing page. */
  blurb: string;
}

export const PLANS: Record<PlanKey, PlanDef> = {
  free: {
    key: 'free',
    name: 'Free',
    price: 0,
    trialDays: 0,
    skuLimit: 50,
    blurb: 'Basic purchase orders and supplier list for tiny catalogs.',
  },
  starter: {
    key: 'starter',
    name: 'Starter',
    price: 9,
    trialDays: 14,
    skuLimit: 200,
    blurb: 'Low-stock alerts, daily digest, and PDF emailing.',
  },
  growth: {
    key: 'growth',
    name: 'Growth',
    price: 19,
    trialDays: 14,
    skuLimit: Infinity,
    blurb: 'Unlimited SKUs, stock transfers, and priority support.',
  },
};

/** Ascending plan order — used for "at least" comparisons. */
export const PLAN_ORDER: PlanKey[] = ['free', 'starter', 'growth'];

export function isPlanKey(value: unknown): value is PlanKey {
  return typeof value === 'string' && (PLAN_ORDER as string[]).includes(value);
}

/** Normalize any stored value to a valid plan (unknown -> 'free'). */
export function normalizePlan(value: unknown): PlanKey {
  return isPlanKey(value) ? value : 'free';
}

/** True when `plan` is at least as capable as `minimum`. */
export function planAtLeast(plan: PlanKey, minimum: PlanKey): boolean {
  return PLAN_ORDER.indexOf(plan) >= PLAN_ORDER.indexOf(minimum);
}

/** Feature gates: minimum plan required for each gated capability. */
export const FEATURE_MIN_PLAN: Record<string, PlanKey> = {
  /** Low-stock thresholds, alerts, and the daily digest. */
  alerts: 'starter',
  /** PDF download and emailing a PO to the supplier. */
  pdf: 'starter',
  /** Location-to-location stock transfers. */
  transfers: 'growth',
};

export function canUseFeature(plan: PlanKey, feature: keyof typeof FEATURE_MIN_PLAN): boolean {
  return planAtLeast(plan, FEATURE_MIN_PLAN[feature]);
}

/**
 * Map a Shopify app subscription name back to a plan key.
 * Names are created as `${appName} — ${PlanName}` by createSubscription().
 * Returns null when the name does not match a known paid plan.
 */
export function planFromSubscriptionName(name: string | null | undefined): PlanKey | null {
  if (!name) return null;
  const lower = name.toLowerCase();
  if (lower.includes('growth')) return 'growth';
  if (lower.includes('starter')) return 'starter';
  return null;
}

/** Human-readable SKU limit for UI copy ("Unlimited" instead of Infinity). */
export function skuLimitLabel(plan: PlanKey): string {
  const limit = PLANS[plan].skuLimit;
  return Number.isFinite(limit) ? String(limit) : 'Unlimited';
}
