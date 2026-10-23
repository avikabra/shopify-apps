/**
 * Ensures all required webhooks are registered with Shopify.
 * Called on app startup to handle cases where webhooks were added
 * after the shop installed (the library only reconciles on OAuth).
 */
import { config } from '../config.js';
import { graphqlAdmin } from './client.js';
import { prisma } from '../db.js';

// Topics must match src/webhooks.ts (Shopify API format)
const REQUIRED_WEBHOOKS = [
  'APP_UNINSTALLED',
  'CUSTOMERS_DATA_REQUEST',
  'CUSTOMERS_REDACT',
  'SHOP_REDACT',
  'INVENTORY_LEVELS_UPDATE',
  'APP_SUBSCRIPTIONS_UPDATE',
];

export async function ensureWebhooksRegistered(): Promise<void> {
  if (config.mockShopify) {
    console.log('[webhooks] Mock mode: skipping registration');
    return;
  }

  try {
    // Get all shops with sessions
    const shops = await prisma.shop.findMany({
      where: { uninstalledAt: null },
      select: { domain: true },
    });

    for (const { domain: shop } of shops) {
      try {
        await ensureWebhooksForShop(shop);
      } catch (err) {
        console.error(`[webhooks] Failed for ${shop}:`, err);
      }
    }
  } catch (err) {
    console.error('[webhooks] Registration check failed:', err);
    // Don't crash startup on webhook registration failure
  }
}

async function ensureWebhooksForShop(shop: string): Promise<void> {
  // Get all offline sessions, try each until one works (old tokens may be invalid)
  const sessions = await prisma.session.findMany({
    where: { shop, isOnline: false },
    orderBy: { id: 'desc' },
  });
  if (sessions.length === 0) {
    console.log(`[webhooks] No offline session for ${shop}, skipping`);
    return;
  }

  // Try each session until one works
  let accessToken: string | null = null;
  let workingSessionId: string | null = null;
  for (const session of sessions) {
    try {
      // Test the token with a simple query
      await graphqlAdmin<{ shop: { name: string } }>(
        shop,
        session.accessToken,
        `query { shop { name } }`
      );
      accessToken = session.accessToken;
      workingSessionId = session.id;
      console.log(`[webhooks] Using working token from session ${session.id} for ${shop}`);
      break;
    } catch (err) {
      console.log(`[webhooks] Session ${session.id} token invalid for ${shop}, trying next`);
      // Delete the invalid session to avoid trying it again
      try {
        await prisma.session.delete({ where: { id: session.id } });
      } catch {}
    }
  }

  if (!accessToken) {
    console.log(`[webhooks] No working token for ${shop}, skipping`);
    return;
  }

  // Get existing subscriptions
  const data = await graphqlAdmin<{
    webhookSubscriptions: { edges: Array<{ node: { topic: string } }> };
  }>(
    shop,
    accessToken,
    `query { webhookSubscriptions(first: 50) { edges { node { topic } } } }`
  );

  const existing = new Set(
    data.webhookSubscriptions.edges.map((e) => e.node.topic)
  );

  const host = new URL(config.host).origin;
  const callbackUrl = `${host}/api/webhooks`;

  for (const topic of REQUIRED_WEBHOOKS) {
    if (existing.has(topic)) {
      continue;
    }

    console.log(`[webhooks] Registering ${topic} for ${shop}...`);
    try {
      await graphqlAdmin(
        shop,
        accessToken,
        `mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
          webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
            webhookSubscription { id }
            userErrors { field message }
          }
        }`,
        {
          topic,
          webhookSubscription: {
            callbackUrl,
            format: 'JSON',
          },
        }
      );
      console.log(`[webhooks] ✓ ${topic} registered for ${shop}`);
    } catch (err) {
      console.error(`[webhooks] ✗ ${topic} failed for ${shop}:`, err);
    }
  }
}
