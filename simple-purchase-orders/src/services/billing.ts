import { prisma } from '../db.js';
import {
  PLANS,
  normalizePlan,
  planAtLeast,
  canUseFeature,
  FEATURE_MIN_PLAN,
  type PlanKey,
} from '../lib/plans.js';
import { syncShopPlan } from '../shopify/billing.js';
import type { Ctx } from './purchaseOrders.js';

/** Current plan for a shop, reconciled best-effort against Shopify. */
export async function currentPlan(ctx: Ctx): Promise<PlanKey> {
  return syncShopPlan(ctx.shopId, ctx.shopDomain, ctx.accessToken);
}

/** Stored plan without a Shopify round-trip (jobs, webhooks). */
export async function storedPlan(shopId: string): Promise<PlanKey> {
  const shop = await prisma.shop.findUnique({ where: { id: shopId }, select: { plan: true } });
  return normalizePlan(shop?.plan);
}

/**
 * Distinct SKUs tracked by a shop (supplier SKU links + low-stock thresholds).
 * This is the metered quantity the plan SKU limits apply to.
 */
export async function countTrackedSkus(shopId: string): Promise<number> {
  const [supplierSkus, thresholds] = await Promise.all([
    prisma.supplierSku.findMany({
      where: { supplier: { shopId } },
      select: { inventoryItemId: true },
    }),
    prisma.stockThreshold.findMany({
      where: { shopId },
      select: { inventoryItemId: true },
    }),
  ]);
  const ids = new Set<string>();
  for (const r of supplierSkus) ids.add(r.inventoryItemId);
  for (const r of thresholds) ids.add(r.inventoryItemId);
  return ids.size;
}

export class PlanLimitError extends Error {
  status = 402;
  billingUrl: string;
  constructor(message: string, billingUrl = '/api/billing/start?plan=starter') {
    super(message);
    this.name = 'PlanLimitError';
    this.billingUrl = billingUrl;
  }
}

/**
 * Throw a 402 PlanLimitError when the shop cannot use a gated feature.
 * Skipped entirely unless billing is enforced (dev/mock mode stays open).
 */
export async function requireFeature(
  ctx: Ctx,
  feature: keyof typeof FEATURE_MIN_PLAN,
  enforce: boolean,
): Promise<void> {
  if (!enforce) return;
  const plan = await currentPlan(ctx);
  if (!canUseFeature(plan, feature)) {
    const min = FEATURE_MIN_PLAN[feature];
    throw new PlanLimitError(
      `This feature requires the ${PLANS[min].name} plan or higher. You're on ${PLANS[plan].name}.`,
      `/api/billing/start?plan=${min}`,
    );
  }
}

/**
 * Throw a 402 when adding another SKU would exceed the plan's SKU limit.
 * `addingNew` should be true only when the SKU is not already tracked.
 */
export async function requireSkuCapacity(
  ctx: Ctx,
  enforce: boolean,
  addingNew: boolean,
): Promise<void> {
  if (!enforce || !addingNew) return;
  const plan = await currentPlan(ctx);
  const limit = PLANS[plan].skuLimit;
  if (!Number.isFinite(limit)) return;
  const used = await countTrackedSkus(ctx.shopId);
  if (used >= limit) {
    const next = plan === 'free' ? 'starter' : 'growth';
    throw new PlanLimitError(
      `Your ${PLANS[plan].name} plan covers ${limit} SKUs and you're tracking ${used}. ` +
        `Upgrade to add more.`,
      `/api/billing/start?plan=${next}`,
    );
  }
}

/** Is this inventory item already tracked by the shop (exempt from SKU cap)? */
export async function isSkuTracked(shopId: string, inventoryItemId: string): Promise<boolean> {
  const [sku, threshold] = await Promise.all([
    prisma.supplierSku.findFirst({
      where: { inventoryItemId, supplier: { shopId } },
      select: { id: true },
    }),
    prisma.stockThreshold.findFirst({
      where: { shopId, inventoryItemId },
      select: { id: true },
    }),
  ]);
  return Boolean(sku || threshold);
}

/** Set the shop's plan directly (free tier, webhook sync, tests). */
export async function setPlan(
  shopId: string,
  plan: PlanKey,
  extra: { subscriptionId?: string | null; billingStatus?: string | null; trialEndsAt?: Date | null } = {},
): Promise<void> {
  await prisma.shop.update({
    where: { id: shopId },
    data: {
      plan,
      subscriptionId: extra.subscriptionId ?? null,
      billingStatus: extra.billingStatus ?? null,
      trialEndsAt: extra.trialEndsAt ?? null,
      planUpdatedAt: new Date(),
    },
  });
}

export { planAtLeast, PLANS };
export type { PlanKey };
