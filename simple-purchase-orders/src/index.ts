import 'dotenv/config';
import express from 'express';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { config } from './config.js';
import { devSessionMiddleware } from './middleware/devAuth.js';
import { webhookHandlers } from './webhooks.js';
import { api, apiErrorHandler } from './routes/api.js';
import { startDigestScheduler } from './jobs/digest.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();

const mockMode = config.mockShopify || Boolean(config.devFakeShop);

if (mockMode && !config.devFakeShop) {
  throw new Error('Mock mode requires DEV_FAKE_SHOP to be set (e.g. demo.myshopify.com).');
}

// Real Shopify integration (OAuth, webhooks, session auth). Skipped in mock mode.
let shopify: ReturnType<typeof import('./shopify/app.js').getShopify> | undefined;
if (!mockMode) {
  const { getShopify } = await import('./shopify/app.js');
  shopify = getShopify();

  app.get(shopify.config.auth.path, shopify.auth.begin());
  app.get(
    shopify.config.auth.callbackPath,
    shopify.auth.callback(),
    shopify.redirectToShopifyOrAppRoot(),
  );
  app.post(shopify.config.webhooks.path, shopify.processWebhooks({ webhookHandlers }));
}

app.use(express.json());
app.use(devSessionMiddleware);

// Public: the embedded frontend needs the API key before App Bridge boots.
app.get('/api/config', (_req, res) => {
  res.json({ apiKey: mockMode ? 'mock' : config.shopifyApiKey });
});

// Authenticated API.
if (shopify && !mockMode) {
  app.use('/api', shopify.validateAuthenticatedSession());
}
app.use('/api', api);
app.use('/api', apiErrorHandler);

// Embedded frontend (built by `npm run build` in web/).
const webDist = join(__dirname, '..', 'web', 'dist');
if (existsSync(webDist)) {
  app.use(express.static(webDist));
  const csp = shopify ? shopify.cspHeaders() : undefined;
  app.get(
    /.*/,
    shopify ? shopify.ensureInstalledOnShop() : (_req, _res, next) => next(),
    ...(csp ? [csp] : []),
    (_req, res) => res.sendFile(join(webDist, 'index.html')),
  );
} else {
  app.get('/', (_req, res) => res.json({ ok: true, app: 'simple-purchase-orders' }));
}

startDigestScheduler();

app.listen(config.port, () => {
  console.log(`[app] listening on port ${config.port} (${mockMode ? 'mock' : 'shopify'} mode)`);
});
