import { config } from '../config.js';
import { graphqlAdmin } from './client.js';

// NOTE: the AppSubscription* input shapes below follow the Admin GraphQL API
// as documented for 2025-10. Verify against a real dev store before release
// (see BUILD_NOTES.md).

export const PLANS = {
  starter: { key: 'starter', name: 'Starter', price: 9, trialDays: 14 },
  growth: { key: 'growth', name: 'Growth', price: 19, trialDays: 14 },
} as const;

export type PlanKey = keyof typeof PLANS;

const ACTIVE_SUBSCRIPTIONS_QUERY = `
  query ActiveSubscriptions {
    app {
      installation {
        activeSubscriptions { id name status }
      }
    }
  }
`;

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

export async function hasActiveSubscription(
  shop: string,
  accessToken: string,
): Promise<boolean> {
  const data = await graphqlAdmin<{
    app: { installation: { activeSubscriptions: { status: string }[] } };
  }>(shop, accessToken, ACTIVE_SUBSCRIPTIONS_QUERY);
  return data.app.installation.activeSubscriptions.some(
    (s) => s.status === 'ACTIVE',
  );
}

export async function createSubscription(
  shop: string,
  accessToken: string,
  plan: PlanKey,
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
