import { prisma } from '../db.js';
import { config } from '../config.js';
import { findLowStock, type LowStockItem } from '../lib/thresholds.js';
import {
  GraphqlShopifyClient,
  MockShopifyClient,
  type ShopifyInventoryClient,
} from '../shopify/client.js';
import { sendEmail } from './email.js';
import type { Ctx } from './purchaseOrders.js';

function backgroundClient(shopDomain: string, accessToken?: string): ShopifyInventoryClient {
  if (config.mockShopify) return new MockShopifyClient();
  if (!accessToken) {
    throw new Error(`No offline session for ${shopDomain}; skipping`);
  }
  return new GraphqlShopifyClient(shopDomain, accessToken);
}

async function offlineAccessToken(shopDomain: string): Promise<string | undefined> {
  const session = await prisma.session.findFirst({
    where: { shop: shopDomain, isOnline: false },
  });
  return session?.accessToken ?? undefined;
}

/** Evaluate all threshold rules for a shop against live Shopify levels. */
export async function evaluateLowStock(ctx: Ctx): Promise<LowStockItem[]> {
  const shop = await prisma.shop.findUnique({
    where: { id: ctx.shopId },
    include: { thresholds: true },
  });
  if (!shop || shop.thresholds.length === 0) return [];

  const adapter: ShopifyInventoryClient = ctx.accessToken
    ? backgroundClient(ctx.shopDomain, ctx.accessToken)
    : backgroundClient(ctx.shopDomain, await offlineAccessToken(ctx.shopDomain));

  const itemIds = [...new Set(shop.thresholds.map((t) => t.inventoryItemId))];
  const levels = await adapter.getLevels(itemIds);

  return findLowStock(
    levels.map((l) => ({
      inventoryItemId: l.inventoryItemId,
      locationId: l.locationId,
      locationName: l.locationName,
      available: l.available,
    })),
    shop.thresholds.map((t) => ({
      inventoryItemId: t.inventoryItemId,
      sku: t.sku,
      productTitle: t.productTitle ?? undefined,
      variantTitle: t.variantTitle ?? undefined,
      locationId: t.locationId,
      locationName: t.locationName ?? undefined,
      minQty: t.minQty,
    })),
  );
}

function digestHtml(shopDomain: string, items: LowStockItem[]): string {
  const rows = items
    .map(
      (i) => `<tr>
        <td style="padding:6px 10px;border-bottom:1px solid #eee"><code>${i.sku}</code></td>
        <td style="padding:6px 10px;border-bottom:1px solid #eee">${i.productTitle ?? ''}${i.variantTitle ? ` — ${i.variantTitle}` : ''}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #eee">${i.locationName ?? ''}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right"><strong>${i.available}</strong></td>
        <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right">${i.minQty}</td>
      </tr>`,
    )
    .join('');
  return `<p>These items are at or below their minimum stock levels on <strong>${shopDomain}</strong>:</p>
    <table style="border-collapse:collapse;width:100%;font-size:14px">
      <thead><tr>
        <th style="text-align:left;padding:6px 10px">SKU</th>
        <th style="text-align:left;padding:6px 10px">Item</th>
        <th style="text-align:left;padding:6px 10px">Location</th>
        <th style="text-align:right;padding:6px 10px">On hand</th>
        <th style="text-align:right;padding:6px 10px">Min</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p>Open Simple Purchase Orders to create a purchase order from these items.</p>`;
}

/** Send the daily low-stock digest for one shop. No email when nothing is low. */
export async function sendLowStockDigest(
  shopId: string,
): Promise<{ sent: boolean; reason: string; count?: number }> {
  const shop = await prisma.shop.findUnique({ where: { id: shopId } });
  if (!shop || shop.uninstalledAt) {
    return { sent: false, reason: 'shop not found or uninstalled' };
  }
  if (!shop.digestEnabled) return { sent: false, reason: 'digest disabled' };
  const to = shop.digestEmail || shop.email;
  if (!to) return { sent: false, reason: 'no digest email configured' };

  let items: LowStockItem[];
  try {
    items = await evaluateLowStock({
      shopId: shop.id,
      shopDomain: shop.domain,
      accessToken: await offlineAccessToken(shop.domain),
    });
  } catch (err) {
    return { sent: false, reason: `evaluation failed: ${(err as Error).message}` };
  }
  if (items.length === 0) return { sent: false, reason: 'nothing below threshold' };

  const result = await sendEmail({
    to,
    subject: `Low stock: ${items.length} item${items.length === 1 ? '' : 's'} need reordering`,
    html: digestHtml(shop.domain, items),
    text:
      `Low stock on ${shop.domain}:\n` +
      items.map((i) => `- ${i.sku} (${i.locationName}): ${i.available} on hand, min ${i.minQty}`).join('\n'),
  });
  return { sent: result.sent, reason: result.sent ? 'emailed' : 'logged (email log mode)', count: items.length };
}

/**
 * Webhook helper for inventory_levels/update: if the shop enabled instant
 * alerts, re-evaluate thresholds for the affected item and email when low.
 */
export async function checkInstantAlert(
  shopDomain: string,
  inventoryItemId: string,
): Promise<void> {
  const shop = await prisma.shop.findUnique({
    where: { domain: shopDomain },
    include: { thresholds: true },
  });
  if (!shop || shop.uninstalledAt || !shop.instantAlerts) return;
  const rules = shop.thresholds.filter((t) => t.inventoryItemId === inventoryItemId);
  if (rules.length === 0) return;

  const to = shop.digestEmail || shop.email;
  if (!to) return;

  let items: LowStockItem[];
  try {
    const adapter = backgroundClient(shopDomain, await offlineAccessToken(shopDomain));
    const levels = await adapter.getLevels([inventoryItemId]);
    items = findLowStock(
      levels.map((l) => ({
        inventoryItemId: l.inventoryItemId,
        locationId: l.locationId,
        locationName: l.locationName,
        available: l.available,
      })),
      rules.map((t) => ({
        inventoryItemId: t.inventoryItemId,
        sku: t.sku,
        productTitle: t.productTitle ?? undefined,
        variantTitle: t.variantTitle ?? undefined,
        locationId: t.locationId,
        locationName: t.locationName ?? undefined,
        minQty: t.minQty,
      })),
    );
  } catch {
    return; // never fail a webhook on alert evaluation
  }
  if (items.length === 0) return;

  await sendEmail({
    to,
    subject: `Low stock alert: ${items[0].sku} is at ${items[0].available}`,
    html: digestHtml(shopDomain, items),
  });
}
