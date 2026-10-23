import { config } from '../config.js';
import { graphqlAdmin } from './client.js';
import { prisma } from '../db.js';
import {
  PLANS,
  normalizePlan,
  planFromSubscriptionName,
  type PlanKey,
} from '../lib/plans.js';

// NOTE: the AppSubscription* input shapes below follow the Admin GraphQL API
// as documented for 2025-10. Verify against a real dev store before release
// (see BUILD_NOTES.md).

// Re-export the plan catalog for existing importers.
export { PLANS };
export type { PlanKey };

const ACTIVE_SUBSCRIPTIONS_QUERY = `
  query ActiveSubscriptions {
    app {
      installation {
        activeSubscriptions { id name status trialDays }
      }
    }
  }
`;

export interface ActiveSubscription {
  id: string;
  name: string;
  status: string;
  trialDays: number | null;
}

/** All ACTIVE app subscriptions for the shop (usually 0 or 1). */
export async function getActiveSubscriptions(
  shop: string,
  accessToken: string,
): Promise<ActiveSubscription[]> {
  const data = await graphqlAdmin<{
    app: { installation: { activeSubscriptions: ActiveSubscription[] } };
  }>(shop, accessToken, ACTIVE_SUBSCRIPTIONS_QUERY);
  return data.app.installation.activeSubscriptions.filter((s) => s.status === 'ACTIVE');
}

export async function hasActiveSubscription(
  shop: string,
  accessToken: string,
): Promise<boolean> {
  return (await getActiveSubscriptions(shop, accessToken)).length > 0;
}

const CREATE_SUBSCRIPTION_MUTATION = `
  mutation CreateSubscription(
    $name: String!
    $returnUrl: URL!
    $trialDays: Int!
    $lineItems: [AppSubscriptionLineItemInput!]!
  ) {
    appSubscriptionCreate(
      name: $name
      returnUrl: $returnUrl
      trialDays: $trialDays
      lineItems: $lineItems
    ) {
      confirmationUrl
      userErrors { field message }
    }
  }
`;

/**
 * Create a paid subscription and return the merchant confirmation URL.
 * The Free plan has no Shopify charge — handle it by writing the DB plan
 * directly (see services/billing.js) instead of calling this.
 */
export async function createSubscription(
  shop: string,
  accessToken: string,
  plan: Exclude<PlanKey, 'free'>,
  returnUrl: string,
): Promise<string> {
  const p = PLANS[plan];
  const data = await graphqlAdmin<{
    appSubscriptionCreate: {
      confirmationUrl: string | null;
      userErrors: { field?: string[] | null; message: string }[];
    };
  }>(shop, accessToken, CREATE_SUBSCRIPTION_MUTATION, {
    name: `${config.appName} — ${p.name}`,
    returnUrl,
    trialDays: p.trialDays,
    lineItems: [
      {
        plan: {
          appRecurringPricingDetails: {
            price: { amount: p.price, currencyCode: 'USD' },
            interval: 'EVERY_30_DAYS',
          },
        },
      },
    ],
  });
  const payload = data.appSubscriptionCreate;
  if (payload.userErrors?.length) {
    throw new Error(
      `Billing subscription failed: ${payload.userErrors.map((e) => e.message).join('; ')}`,
    );
  }
  if (!payload.confirmationUrl) {
    throw new Error('Billing subscription failed: no confirmation URL returned');
  }
  return payload.confirmationUrl;
}

const CANCEL_SUBSCRIPTION_MUTATION = `
  mutation CancelSubscription($id: ID!) {
    appSubscriptionCancel(id: $id) {
      userErrors { field message }
    }
  }
`;

/** Cancel a Shopify app subscription (used for downgrades to Free). */
export async function cancelSubscription(
  shop: string,
  accessToken: string,
  subscriptionId: string,
): Promise<void> {
  const data = await graphqlAdmin<{
    appSubscriptionCancel: { userErrors: { field?: string[] | null; message: string }[] };
  }>(shop, accessToken, CANCEL_SUBSCRIPTION_MUTATION, { id: subscriptionId });
  const errors = data.appSubscriptionCancel.userErrors;
  if (errors?.length) {
    throw new Error(
      `Billing cancellation failed: ${errors.map((e) => e.message).join('; ')}`,
    );
  }
}

/**
 * Reconcile the shop's plan from Shopify's active subscriptions.
 * Best-effort: never throws — on API failure the stored DB plan stands.
 * Returns the effective plan key.
 */
export async function syncShopPlan(
  shopId: string,
  shopDomain: string,
  accessToken?: string,
): Promise<PlanKey> {
  const shop = await prisma.shop.findUnique({ where: { id: shopId } });
  const stored = normalizePlan(shop?.plan);
  if (!accessToken || config.mockShopify) return stored;
  try {
    const subs = await getActiveSubscriptions(shopDomain, accessToken);
    const sub = subs[0];
    if (!sub) {
      // No active Shopify subscription — fall back to whatever is stored,
      // but clear a stale subscription id so we don't reference a dead charge.
      if (shop?.subscriptionId) {
        await prisma.shop.update({
          where: { id: shopId },
          data: { subscriptionId: null, billingStatus: 'CANCELLED', planUpdatedAt: new Date() },
        });
      }
      return stored;
    }
    const plan = planFromSubscriptionName(sub.name) ?? stored;
    await prisma.shop.update({
      where: { id: shopId },
      data: {
        plan,
        subscriptionId: sub.id,
        billingStatus: sub.status,
        planUpdatedAt: new Date(),
      },
    });
    return plan;
  } catch (err) {
    console.warn(`[billing] plan sync failed for ${shopDomain}:`, (err as Error).message);
    return stored;
  }
}
