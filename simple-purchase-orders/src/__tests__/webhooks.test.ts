/**
 * Tests for webhook HMAC verification and mandatory compliance webhooks.
 *
 * Note: Actual HMAC verification of incoming webhooks is handled by
 * @shopify/shopify-app-express's processWebhooks() middleware, which
 * verifies the X-Shopify-Hmac-Sha256 header using the API secret before
 * dispatching to handlers. These tests verify:
 * 1. Our HMAC computation matches Shopify's spec (for test tooling)
 * 2. All mandatory compliance webhooks are declared in webhookHandlers
 */
import { describe, it, expect } from 'vitest';
import crypto from 'node:crypto';
import { webhookHandlers } from '../webhooks.js';

// Mirror of Shopify's HMAC verification logic (for testing)
function computeHmac(secret: string, rawBody: string): string {
  return crypto
    .createHmac('sha256', secret)
    .update(rawBody, 'utf8')
    .digest('base64');
}

function verifyHmac(secret: string, rawBody: string, headerValue: string | undefined): boolean {
  if (!headerValue) return false;
  const expected = computeHmac(secret, rawBody);
  // Timing-safe comparison (same as Shopify library)
  const a = Buffer.from(expected, 'utf8');
  const b = Buffer.from(headerValue, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

describe('Webhook HMAC verification', () => {
  const secret = 'test-secret-key';
  const body = JSON.stringify({ shop: { domain: 'test.myshopify.com' } });

  it('computes HMAC-SHA256 matching Shopify spec', () => {
    const hmac = computeHmac(secret, body);
    // Verify it's valid base64-encoded SHA256
    expect(hmac).toMatch(/^[A-Za-z0-9+/]+=*$/);
    expect(Buffer.from(hmac, 'base64')).toHaveLength(32);
  });

  it('accepts valid HMAC signature', () => {
    const validHmac = computeHmac(secret, body);
    expect(verifyHmac(secret, body, validHmac)).toBe(true);
  });

  it('rejects invalid HMAC signature', () => {
    expect(verifyHmac(secret, body, 'invalid-signature')).toBe(false);
  });

  it('rejects missing HMAC header', () => {
    expect(verifyHmac(secret, body, undefined)).toBe(false);
  });

  it('rejects tampered body', () => {
    const validHmac = computeHmac(secret, body);
    const tamperedBody = body + 'tampered';
    expect(verifyHmac(secret, tamperedBody, validHmac)).toBe(false);
  });

  it('rejects wrong secret', () => {
    const validHmac = computeHmac(secret, body);
    expect(verifyHmac('wrong-secret', body, validHmac)).toBe(false);
  });
});

describe('Mandatory compliance webhooks', () => {
  // Shopify requires these GDPR webhooks for App Store approval
  const MANDATORY_TOPICS = [
    'APP_UNINSTALLED',
    'CUSTOMERS_DATA_REQUEST',
    'CUSTOMERS_REDACT',
    'SHOP_REDACT',
  ];

  it.each(MANDATORY_TOPICS)('declares %s webhook handler', (topic) => {
    expect(webhookHandlers).toHaveProperty(topic);
    const handlers = (webhookHandlers as any)[topic];
    expect(Array.isArray(handlers)).toBe(true);
    expect(handlers.length).toBeGreaterThan(0);
  });

  it('all handlers use HTTP delivery to /api/webhooks', () => {
    for (const topic of MANDATORY_TOPICS) {
      const handlers = (webhookHandlers as any)[topic];
      for (const h of handlers) {
        expect(h.callbackUrl).toBe('/api/webhooks');
        expect(typeof h.callback).toBe('function');
      }
    }
  });

  it('compliance webhooks acknowledge without error', async () => {
    // The compliance handlers are no-ops (we store no customer data)
    // They must return 200 (resolve without throwing)
    for (const topic of ['CUSTOMERS_DATA_REQUEST', 'CUSTOMERS_REDACT', 'SHOP_REDACT']) {
      const handlers = (webhookHandlers as any)[topic];
      for (const h of handlers) {
        await expect(h.callback(topic, 'test.myshopify.com', '{}')).resolves.not.toThrow();
      }
    }
  });
});

describe('Webhook handler registration', () => {
  it('exports webhookHandlers for shopify-app-express', () => {
    expect(webhookHandlers).toBeDefined();
    expect(typeof webhookHandlers).toBe('object');
  });
});
