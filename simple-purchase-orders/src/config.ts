import 'dotenv/config';

function optional(name: string, fallback = ''): string {
  return process.env[name] ?? fallback;
}

export const config = {
  port: Number(process.env.PORT ?? 3000),
  /** Public base URL of the app, no trailing slash. Used for OAuth callbacks, webhooks and billing return URLs. */
  host: (process.env.HOST ?? 'http://localhost:3000').replace(/\/$/, ''),

  shopifyApiKey: optional('SHOPIFY_API_KEY'),
  shopifyApiSecret: optional('SHOPIFY_API_SECRET'),
  scopes: (process.env.SCOPES ?? 'read_products,read_inventory,write_inventory,read_locations')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
  apiVersion: process.env.SHOPIFY_API_VERSION ?? '2025-10',

  databaseUrl: optional('DATABASE_URL'),

  /** When true, all Shopify API calls hit the local fixture adapter instead of Shopify. */
  mockShopify: process.env.SHOPIFY_MOCK === 'true',
  /** Path to the mock shop fixture file (read + written by the mock adapter). */
  mockDataPath: optional('MOCK_DATA_PATH', 'data/mock-shop.json'),
  /**
   * Local-dev escape hatch: when set (e.g. DEV_FAKE_SHOP=demo.myshopify.com),
   * API routes accept requests without a Shopify session and use the mock
   * adapter. NEVER set in production.
   */
  devFakeShop: optional('DEV_FAKE_SHOP'),

  emailMode: process.env.EMAIL_MODE ?? 'log', // 'log' | 'smtp'
  smtpHost: optional('SMTP_HOST'),
  smtpPort: Number(process.env.SMTP_PORT ?? 587),
  smtpUser: optional('SMTP_USER'),
  smtpPass: optional('SMTP_PASS'),
  emailFrom: optional('EMAIL_FROM', 'Simple Purchase Orders <noreply@example.com>'),

  billingEnforce: process.env.BILLING_ENFORCE === 'true',
  appName: 'Simple Purchase Orders',
};

export function requireShopifyCredentials(): void {
  if (!config.shopifyApiKey || !config.shopifyApiSecret) {
    throw new Error(
      'SHOPIFY_API_KEY and SHOPIFY_API_SECRET must be set to run OAuth / real Shopify API calls. ' +
        'For local development, set SHOPIFY_MOCK=true and DEV_FAKE_SHOP=demo.myshopify.com instead.',
    );
  }
}
