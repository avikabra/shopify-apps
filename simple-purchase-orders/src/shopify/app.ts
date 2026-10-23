import '@shopify/shopify-api/adapters/node';
import { ApiVersion, shopifyApp } from '@shopify/shopify-app-express';
import { PrismaSessionStorage } from '@shopify/shopify-app-session-storage-prisma';
import { prisma } from '../db.js';
import { config, requireShopifyCredentials } from '../config.js';

type ShopifyApp = ReturnType<typeof shopifyApp>;

let cached: ShopifyApp | undefined;

/**
 * Lazily build the Shopify app instance. Throws unless real credentials are
 * configured — mock/dev mode never calls this.
 */
export function getShopify(): ShopifyApp {
  if (cached) return cached;
  requireShopifyCredentials();

  const hostUrl = new URL(config.host);
  const apiVersion = (Object.values(ApiVersion) as string[]).includes(config.apiVersion)
    ? (config.apiVersion as ApiVersion)
    : ApiVersion.October25;

  cached = shopifyApp({
    api: {
      apiKey: config.shopifyApiKey,
      apiSecretKey: config.shopifyApiSecret,
      scopes: config.scopes,
      hostName: hostUrl.host,
      hostScheme: hostUrl.protocol.replace(':', '') as 'http' | 'https',
      apiVersion,
    },
    auth: {
      path: '/api/auth',
      callbackPath: '/api/auth/callback',
    },
    webhooks: {
      path: '/api/webhooks',
    },
    sessionStorage: new PrismaSessionStorage(prisma),
    future: {
      // The app uses Shopify's managed install flow (use_legacy_install_flow
      // = false), so there is no OAuth callback. Session tokens from App
      // Bridge are exchanged for access tokens on demand.
      tokenExchange: true,
      // Shopify no longer accepts non-expiring offline tokens for the
      // Admin API on this app; exchange for expiring tokens instead and
      // let the library refresh them automatically.
      expiringOfflineAccessTokens: true,
    },
  });
  return cached;
}
