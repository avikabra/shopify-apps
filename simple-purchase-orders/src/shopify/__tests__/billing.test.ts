import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getActiveSubscriptions,
  hasActiveSubscription,
  createSubscription,
  cancelSubscription,
} from '../billing.js';

function mockFetchOnce(json: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok,
      text: async () => JSON.stringify(json),
      json: async () => json,
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getActiveSubscriptions', () => {
  it('returns only ACTIVE subscriptions', async () => {
    mockFetchOnce({
      data: {
        app: {
          installation: {
            activeSubscriptions: [
              { id: 'gid://shopify/AppSubscription/1', name: 'App — Starter', status: 'ACTIVE', trialDays: 14 },
              { id: 'gid://shopify/AppSubscription/2', name: 'App — Growth', status: 'CANCELLED', trialDays: 0 },
            ],
          },
        },
      },
    });
    const subs = await getActiveSubscriptions('shop.myshopify.com', 'token');
    expect(subs).toHaveLength(1);
    expect(subs[0].id).toBe('gid://shopify/AppSubscription/1');
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [, init] = fetchMock.mock.calls[0] as [string, { body: string }];
    expect(JSON.parse(init.body).query).toContain('activeSubscriptions');
  });

  it('throws on GraphQL errors', async () => {
    mockFetchOnce({ errors: [{ message: 'boom' }] });
    await expect(getActiveSubscriptions('shop.myshopify.com', 'token')).rejects.toThrow(
      'Shopify GraphQL errors: boom',
    );
  });
});

describe('hasActiveSubscription', () => {
  it('is true when an ACTIVE subscription exists', async () => {
    mockFetchOnce({
      data: { app: { installation: { activeSubscriptions: [{ status: 'ACTIVE' }] } } },
    });
    expect(await hasActiveSubscription('shop.myshopify.com', 'token')).toBe(true);
  });

  it('is false when none are active', async () => {
    mockFetchOnce({
      data: { app: { installation: { activeSubscriptions: [{ status: 'CANCELLED' }] } } },
    });
    expect(await hasActiveSubscription('shop.myshopify.com', 'token')).toBe(false);
  });
});

describe('createSubscription', () => {
  it('sends the starter mutation and returns the confirmation URL', async () => {
    mockFetchOnce({
      data: {
        appSubscriptionCreate: {
          confirmationUrl: 'https://shopify.com/confirm/123',
          userErrors: [],
        },
      },
    });
    const url = await createSubscription(
      'shop.myshopify.com',
      'token',
      'starter',
      'https://app.example.com/api/billing/callback?shop=shop.myshopify.com',
    );
    expect(url).toBe('https://shopify.com/confirm/123');

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [calledUrl, init] = fetchMock.mock.calls[0] as [string, { body: string; headers: Record<string, string> }];
    expect(calledUrl).toContain('shop.myshopify.com/admin/api/');
    expect(init.headers['X-Shopify-Access-Token']).toBe('token');
    const payload = JSON.parse(init.body);
    expect(payload.variables.name).toContain('Starter');
    expect(payload.variables.trialDays).toBe(14);
    expect(payload.variables.returnUrl).toContain('/api/billing/callback');
    const pricing = payload.variables.lineItems[0].plan.appRecurringPricingDetails;
    expect(pricing.price).toEqual({ amount: 9, currencyCode: 'USD' });
    expect(pricing.interval).toBe('EVERY_30_DAYS');
  });

  it('prices growth at $19', async () => {
    mockFetchOnce({
      data: { appSubscriptionCreate: { confirmationUrl: 'https://x', userErrors: [] } },
    });
    await createSubscription('shop.myshopify.com', 'token', 'growth', 'https://app.example.com/cb');
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [, init] = fetchMock.mock.calls[0] as [string, { body: string }];
    const payload = JSON.parse(init.body);
    expect(payload.variables.lineItems[0].plan.appRecurringPricingDetails.price.amount).toBe(19);
  });

  it('throws when Shopify reports userErrors', async () => {
    mockFetchOnce({
      data: {
        appSubscriptionCreate: {
          confirmationUrl: null,
          userErrors: [{ field: ['name'], message: 'Name is invalid' }],
        },
      },
    });
    await expect(
      createSubscription('shop.myshopify.com', 'token', 'starter', 'https://app.example.com/cb'),
    ).rejects.toThrow('Billing subscription failed: Name is invalid');
  });

  it('throws when no confirmation URL is returned', async () => {
    mockFetchOnce({
      data: { appSubscriptionCreate: { confirmationUrl: null, userErrors: [] } },
    });
    await expect(
      createSubscription('shop.myshopify.com', 'token', 'starter', 'https://app.example.com/cb'),
    ).rejects.toThrow('no confirmation URL');
  });
});

describe('cancelSubscription', () => {
  it('sends the cancel mutation with the subscription id', async () => {
    mockFetchOnce({ data: { appSubscriptionCancel: { userErrors: [] } } });
    await cancelSubscription('shop.myshopify.com', 'token', 'gid://shopify/AppSubscription/9');
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [, init] = fetchMock.mock.calls[0] as [string, { body: string }];
    const payload = JSON.parse(init.body);
    expect(payload.query).toContain('appSubscriptionCancel');
    expect(payload.variables).toEqual({ id: 'gid://shopify/AppSubscription/9' });
  });

  it('throws on userErrors', async () => {
    mockFetchOnce({
      data: { appSubscriptionCancel: { userErrors: [{ message: 'Cannot cancel' }] } },
    });
    await expect(
      cancelSubscription('shop.myshopify.com', 'token', 'gid://shopify/AppSubscription/9'),
    ).rejects.toThrow('Billing cancellation failed: Cannot cancel');
  });
});
