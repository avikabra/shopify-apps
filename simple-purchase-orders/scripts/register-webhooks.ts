/**
 * Manually registers all required webhooks with Shopify.
 * Run this after deploying webhook handler changes to ensure Shopify
 * has the subscriptions (the library only reconciles on OAuth).
 *
 * Usage: npx tsx scripts/register-webhooks.ts
 */
import 'dotenv/config';
import { PrismaClient } from '@prisma/client';
import crypto from 'node:crypto';

const prisma = new PrismaClient();

const SHOP = 'simple-apps-dev.myshopify.com';
const API_KEY = process.env.SHOPIFY_API_KEY!;
const API_SECRET = process.env.SHOPIFY_API_SECRET!;
const HOST = process.env.HOST!; // e.g. https://simple-purchase-orders-production.up.railway.app

// All webhooks the app needs (must match src/webhooks.ts topics)
const WEBHOOKS = [
  { topic: 'app/uninstalled', address: `${HOST}/api/webhooks` },
  { topic: 'customers/data_request', address: `${HOST}/api/webhooks` },
  { topic: 'customers/redact', address: `${HOST}/api/webhooks` },
  { topic: 'shop/redact', address: `${HOST}/api/webhooks` },
  { topic: 'inventory_levels/update', address: `${HOST}/api/webhooks` },
  { topic: 'app_subscriptions/update', address: `${HOST}/api/webhooks` },
];

async function getAccessToken(): Promise<string> {
  // Get the offline session for the shop (contains access token)
  const session = await prisma.session.findFirst({
    where: { shop: SHOP, isOnline: false },
    orderBy: { expires: 'desc' },
  });
  if (!session?.accessToken) {
    // Fallback: any session for the shop
    const anySession = await prisma.session.findFirst({
      where: { shop: SHOP },
      orderBy: { expires: 'desc' },
    });
    if (!anySession?.accessToken) {
      throw new Error(`No session/access token found for ${SHOP}`);
    }
    return anySession.accessToken;
  }
  return session.accessToken;
}

async function shopifyFetch(accessToken: string, query: string, variables: any = {}) {
  const res = await fetch(`https://${SHOP}/admin/api/2025-10/graphql.json`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Shopify-Access-Token': accessToken,
    },
    body: JSON.stringify({ query, variables }),
  });
  const data = await res.json();
  if (data.errors) {
    throw new Error(`GraphQL errors: ${JSON.stringify(data.errors)}`);
  }
  return data.data;
}

async function main() {
  console.log(`[register-webhooks] Shop: ${SHOP}`);
  console.log(`[register-webhooks] Host: ${HOST}`);

  const accessToken = await getAccessToken();
  console.log('[register-webhooks] Got access token');

  // Get existing webhook subscriptions
  const existing = await shopifyFetch(
    accessToken,
    `query { webhookSubscriptions(first: 50) { edges { node { id topic endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } } } } } }`
  );
  const existingTopics = new Map(
    existing.webhookSubscriptions.edges.map((e: any) => [
      e.node.topic,
      e.node.endpoint?.callbackUrl,
    ])
  );
  console.log('[register-webhooks] Existing:', [...existingTopics.keys()]);

  // Register missing webhooks
  for (const wh of WEBHOOKS) {
    if (existingTopics.has(wh.topic.toUpperCase().replace('/', '_'))) {
      console.log(`[register-webhooks] ✓ ${wh.topic} already registered`);
      continue;
    }

    console.log(`[register-webhooks] Registering ${wh.topic}...`);
    const result = await shopifyFetch(
      accessToken,
      `mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
        webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
          webhookSubscription { id topic }
          userErrors { field message }
        }
      }`,
      {
        topic: wh.topic.toUpperCase().replace('/', '_'),
        webhookSubscription: {
          callbackUrl: wh.address,
          format: 'JSON',
        },
      }
    );

    const errors = result.webhookSubscriptionCreate.userErrors;
    if (errors?.length) {
      console.error(`[register-webhooks] ✗ ${wh.topic} failed:`, errors);
    } else {
      console.log(`[register-webhooks] ✓ ${wh.topic} registered`);
    }
  }

  console.log('[register-webhooks] Done');
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error('[register-webhooks] Fatal:', err);
  process.exit(1);
});
