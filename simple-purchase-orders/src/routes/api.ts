import { Router, type Request, type Response, type NextFunction } from 'express';
import { prisma } from '../db.js';
import { config } from '../config.js';
import { sessionFromReq } from '../middleware/devAuth.js';
import { graphqlAdmin, clientForShop } from '../shopify/client.js';
import { badRequest, statusOf } from '../lib/errors.js';
import { hasActiveSubscription, createSubscription } from '../shopify/billing.js';
import type { Ctx } from '../services/purchaseOrders.js';
import {
  createPurchaseOrder,
  listPurchaseOrders,
  getPurchaseOrder,
  sendPurchaseOrder,
  receivePurchaseOrderLine,
  cancelPurchaseOrder,
  purchaseOrderPdf,
} from '../services/purchaseOrders.js';
import {
  listSuppliers,
  createSupplier,
  updateSupplier,
  deleteSupplier,
  addSupplierSku,
  removeSupplierSku,
} from '../services/suppliers.js';
import {
  createTransfer,
  listTransfers,
  getTransfer,
  receiveTransfer,
  cancelTransfer,
} from '../services/transfers.js';
import { evaluateLowStock, sendLowStockDigest } from '../services/alerts.js';
import { getDashboardStats } from '../services/dashboard.js';

export const api = Router();

function reqCtx(req: Request): Ctx {
  return (req as unknown as { spoCtx: Ctx }).spoCtx;
}

/** Attach the shop row + Shopify session context to every request. */
async function resolveShop(req: Request, res: Response, next: NextFunction) {
  try {
    const session = sessionFromReq(req, res);
    let shop = await prisma.shop.findUnique({ where: { domain: session.shop } });
    if (!shop) {
      shop = await prisma.shop.create({ data: { domain: session.shop } });
      // Best-effort: backfill name/email for digests.
      if (session.accessToken && !config.mockShopify) {
        try {
          const data = await graphqlAdmin<{ shop: { name: string; email: string } }>(
            session.shop,
            session.accessToken,
            `query { shop { name email } }`,
          );
          shop = await prisma.shop.update({
            where: { id: shop.id },
            data: { name: data.shop.name, email: data.shop.email },
          });
        } catch {
          // Non-fatal; the settings page lets the merchant set an email.
        }
      }
    }
    (req as unknown as { spoCtx: Ctx }).spoCtx = {
      shopId: shop.id,
      shopDomain: shop.domain,
      accessToken: session.accessToken,
    };
    next();
  } catch (err) {
    next(err);
  }
}

api.use(resolveShop);

/* ---------- billing ---------- */

async function requireBilling(req: Request, res: Response, next: NextFunction) {
  try {
    if (!config.billingEnforce) return next();
    const ctx = reqCtx(req);
    if (!ctx.accessToken || config.mockShopify) return next(); // can't verify in dev/mock
    const active = await hasActiveSubscription(ctx.shopDomain, ctx.accessToken);
    if (active) return next();
    res.status(402).json({
      error: 'A subscription is required to use this app.',
      billingUrl: '/api/billing/start?plan=starter',
    });
  } catch (err) {
    next(err);
  }
}

api.get('/billing/status', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const active =
      !config.billingEnforce ||
      !ctx.accessToken ||
      config.mockShopify ||
      (await hasActiveSubscription(ctx.shopDomain, ctx.accessToken));
    res.json({ active, enforce: config.billingEnforce });
  } catch (err) {
    next(err);
  }
});

api.get('/billing/start', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const plan = req.query.plan === 'growth' ? 'growth' : 'starter';
    if (!ctx.accessToken || config.mockShopify) {
      throw badRequest('Billing is not available in mock mode.');
    }
    const confirmationUrl = await createSubscription(
      ctx.shopDomain,
      ctx.accessToken,
      plan,
      `${config.host}/api/billing/callback?shop=${ctx.shopDomain}`,
    );
    res.redirect(302, confirmationUrl);
  } catch (err) {
    next(err);
  }
});

api.get('/billing/callback', async (req, res) => {
  // Shopify redirects here after the merchant approves/declines.
  const shop = req.query.shop ?? '';
  res.redirect(`/?shop=${shop}`);
});

api.use(requireBilling);

/* ---------- shop / dashboard ---------- */

api.get('/shop', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const shop = await prisma.shop.findUnique({ where: { id: ctx.shopId } });
    const locations = await clientForShop(ctx.shopDomain, ctx.accessToken).getLocations();
    res.json({ shop: { domain: ctx.shopDomain, name: shop?.name ?? null }, locations });
  } catch (err) {
    next(err);
  }
});

api.get('/dashboard', async (req, res, next) => {
  try {
    res.json(await getDashboardStats(reqCtx(req)));
  } catch (err) {
    next(err);
  }
});

/* ---------- variants ---------- */

api.get('/variants/search', async (req, res, next) => {
  try {
    const q = String(req.query.q ?? '');
    if (!q.trim()) throw badRequest('Search query is required.');
    const ctx = reqCtx(req);
    res.json(await clientForShop(ctx.shopDomain, ctx.accessToken).searchVariants(q));
  } catch (err) {
    next(err);
  }
});

/* ---------- suppliers ---------- */

api.get('/suppliers', async (req, res, next) => {
  try {
    res.json(await listSuppliers(reqCtx(req)));
  } catch (err) {
    next(err);
  }
});

api.post('/suppliers', async (req, res, next) => {
  try {
    const { name, email, contactName, phone, notes } = req.body ?? {};
    if (!String(name ?? '').trim()) throw badRequest('Supplier name is required.');
    res.status(201).json(
      await createSupplier(reqCtx(req), {
        name: String(name),
        email: email ? String(email) : undefined,
        contactName: contactName ? String(contactName) : undefined,
        phone: phone ? String(phone) : undefined,
        notes: notes ? String(notes) : undefined,
      }),
    );
  } catch (err) {
    next(err);
  }
});

api.put('/suppliers/:id', async (req, res, next) => {
  try {
    res.json(await updateSupplier(reqCtx(req), req.params.id, req.body ?? {}));
  } catch (err) {
    next(err);
  }
});

api.delete('/suppliers/:id', async (req, res, next) => {
  try {
    await deleteSupplier(reqCtx(req), req.params.id);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

api.post('/suppliers/:id/skus', async (req, res, next) => {
  try {
    const { inventoryItemId, sku, variantTitle, unitCost } = req.body ?? {};
    if (!inventoryItemId || !sku) throw badRequest('inventoryItemId and sku are required.');
    res.status(201).json(
      await addSupplierSku(reqCtx(req), req.params.id, {
        inventoryItemId: String(inventoryItemId),
        sku: String(sku),
        variantTitle: variantTitle ? String(variantTitle) : undefined,
        unitCost: unitCost != null ? Number(unitCost) : undefined,
      }),
    );
  } catch (err) {
    next(err);
  }
});

api.delete('/suppliers/:id/skus/:skuId', async (req, res, next) => {
  try {
    await removeSupplierSku(reqCtx(req), req.params.id, req.params.skuId);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

/* ---------- thresholds / alerts ---------- */

api.get('/thresholds', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const thresholds = await prisma.stockThreshold.findMany({
      where: { shopId: ctx.shopId },
      orderBy: [{ productTitle: 'asc' }, { variantTitle: 'asc' }],
    });
    const itemIds = [...new Set(thresholds.map((t) => t.inventoryItemId))];
    const available = new Map<string, number>();
    if (itemIds.length) {
      const levels = await clientForShop(ctx.shopDomain, ctx.accessToken).getLevels(itemIds);
      for (const l of levels) {
        available.set(`${l.inventoryItemId}:${l.locationId}`, l.available);
        available.set(l.inventoryItemId, (available.get(l.inventoryItemId) ?? 0) + l.available);
      }
    }
    res.json(
      thresholds.map((t) => ({
        ...t,
        available: t.locationId
          ? available.get(`${t.inventoryItemId}:${t.locationId}`) ?? null
          : available.get(t.inventoryItemId) ?? null,
      })),
    );
  } catch (err) {
    next(err);
  }
});

api.post('/thresholds', async (req, res, next) => {
  try {
    const { inventoryItemId, productTitle, variantTitle, sku, locationId, locationName, minQty } =
      req.body ?? {};
    if (!inventoryItemId || !productTitle) throw badRequest('inventoryItemId and productTitle are required.');
    if (!Number.isInteger(minQty) || minQty < 0) throw badRequest('minQty must be a non-negative integer.');
    const ctx = reqCtx(req);
    const locId = locationId ? String(locationId) : 'ALL_LOCATIONS';
    res.status(201).json(
      await prisma.stockThreshold.upsert({
        where: {
          shopId_inventoryItemId_locationId: {
            shopId: ctx.shopId,
            inventoryItemId: String(inventoryItemId),
            locationId: locId,
          },
        },
        create: {
          shopId: ctx.shopId,
          inventoryItemId: String(inventoryItemId),
          sku: sku ? String(sku) : '',
          productTitle: String(productTitle),
          variantTitle: variantTitle ? String(variantTitle) : null,
          locationId: locId,
          locationName: locationName ? String(locationName) : null,
          minQty,
        },
        update: { minQty },
      }),
    );
  } catch (err) {
    next(err);
  }
});

api.put('/thresholds/:id', async (req, res, next) => {
  try {
    const { minQty } = req.body ?? {};
    if (!Number.isInteger(minQty) || minQty < 0) throw badRequest('minQty must be a non-negative integer.');
    const ctx = reqCtx(req);
    const existing = await prisma.stockThreshold.findFirst({
      where: { id: req.params.id, shopId: ctx.shopId },
    });
    if (!existing) throw badRequest('Threshold not found.');
    res.json(await prisma.stockThreshold.update({ where: { id: existing.id }, data: { minQty } }));
  } catch (err) {
    next(err);
  }
});

api.delete('/thresholds/:id', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const existing = await prisma.stockThreshold.findFirst({
      where: { id: req.params.id, shopId: ctx.shopId },
    });
    if (!existing) throw badRequest('Threshold not found.');
    await prisma.stockThreshold.delete({ where: { id: existing.id } });
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

api.get('/low-stock', async (req, res, next) => {
  try {
    res.json(await evaluateLowStock(reqCtx(req)));
  } catch (err) {
    next(err);
  }
});

api.post('/alerts/check-now', async (req, res, next) => {
  try {
    const low = await evaluateLowStock(reqCtx(req));
    res.json({ count: low.length, items: low });
  } catch (err) {
    next(err);
  }
});

/* ---------- purchase orders ---------- */

api.get('/purchase-orders', async (req, res, next) => {
  try {
    const status = req.query.status ? String(req.query.status) : undefined;
    res.json(await listPurchaseOrders(reqCtx(req), status));
  } catch (err) {
    next(err);
  }
});

api.post('/purchase-orders', async (req, res, next) => {
  try {
    const { supplierId, locationId, expectedAt, notes, lines } = req.body ?? {};
    if (!supplierId) throw badRequest('supplierId is required.');
    res.status(201).json(
      await createPurchaseOrder(reqCtx(req), {
        supplierId: String(supplierId),
        locationId: locationId ? String(locationId) : undefined,
        expectedAt: expectedAt ? String(expectedAt) : undefined,
        notes: notes ? String(notes) : undefined,
        lines: Array.isArray(lines) ? lines : [],
      }),
    );
  } catch (err) {
    next(err);
  }
});

api.get('/purchase-orders/:id', async (req, res, next) => {
  try {
    const po = await getPurchaseOrder(reqCtx(req), req.params.id);
    if (!po) throw badRequest('Purchase order not found.');
    res.json(po);
  } catch (err) {
    next(err);
  }
});

api.post('/purchase-orders/:id/send', async (req, res, next) => {
  try {
    res.json(await sendPurchaseOrder(reqCtx(req), req.params.id));
  } catch (err) {
    next(err);
  }
});

api.post('/purchase-orders/:id/receive', async (req, res, next) => {
  try {
    const { lineId, quantity } = req.body ?? {};
    if (!lineId) throw badRequest('lineId is required.');
    if (!Number.isInteger(quantity) || quantity <= 0) throw badRequest('quantity must be a positive integer.');
    res.json(await receivePurchaseOrderLine(reqCtx(req), req.params.id, String(lineId), quantity));
  } catch (err) {
    next(err);
  }
});

api.post('/purchase-orders/:id/cancel', async (req, res, next) => {
  try {
    res.json(await cancelPurchaseOrder(reqCtx(req), req.params.id));
  } catch (err) {
    next(err);
  }
});

api.get('/purchase-orders/:id/pdf', async (req, res, next) => {
  try {
    const { filename, buffer } = await purchaseOrderPdf(reqCtx(req), req.params.id);
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.send(buffer);
  } catch (err) {
    next(err);
  }
});

/* ---------- transfers ---------- */

api.get('/transfers', async (req, res, next) => {
  try {
    const status = req.query.status ? String(req.query.status) : undefined;
    res.json(await listTransfers(reqCtx(req), status));
  } catch (err) {
    next(err);
  }
});

api.post('/transfers', async (req, res, next) => {
  try {
    const { fromLocationId, toLocationId, notes, lines } = req.body ?? {};
    if (!fromLocationId || !toLocationId) throw badRequest('fromLocationId and toLocationId are required.');
    res.status(201).json(
      await createTransfer(reqCtx(req), {
        fromLocationId: String(fromLocationId),
        toLocationId: String(toLocationId),
        notes: notes ? String(notes) : undefined,
        lines: Array.isArray(lines) ? lines : [],
      }),
    );
  } catch (err) {
    next(err);
  }
});

api.get('/transfers/:id', async (req, res, next) => {
  try {
    const t = await getTransfer(reqCtx(req), req.params.id);
    if (!t) throw badRequest('Transfer not found.');
    res.json(t);
  } catch (err) {
    next(err);
  }
});

api.post('/transfers/:id/receive', async (req, res, next) => {
  try {
    res.json(await receiveTransfer(reqCtx(req), req.params.id));
  } catch (err) {
    next(err);
  }
});

api.post('/transfers/:id/cancel', async (req, res, next) => {
  try {
    res.json(await cancelTransfer(reqCtx(req), req.params.id));
  } catch (err) {
    next(err);
  }
});

/* ---------- settings ---------- */

api.get('/settings', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const shop = await prisma.shop.findUnique({ where: { id: ctx.shopId } });
    res.json({
      digestEmail: shop?.digestEmail ?? shop?.email ?? '',
      digestEnabled: shop?.digestEnabled ?? true,
      digestHourUtc: shop?.digestHourUtc ?? 13,
      instantAlerts: shop?.instantAlerts ?? false,
    });
  } catch (err) {
    next(err);
  }
});

api.put('/settings', async (req, res, next) => {
  try {
    const { digestEmail, digestEnabled, digestHourUtc, instantAlerts } = req.body ?? {};
    if (digestHourUtc != null && (!Number.isInteger(digestHourUtc) || digestHourUtc < 0 || digestHourUtc > 23)) {
      throw badRequest('digestHourUtc must be an integer 0-23.');
    }
    const ctx = reqCtx(req);
    const shop = await prisma.shop.update({
      where: { id: ctx.shopId },
      data: {
        ...(digestEmail !== undefined ? { digestEmail: String(digestEmail) || null } : {}),
        ...(digestEnabled !== undefined ? { digestEnabled: Boolean(digestEnabled) } : {}),
        ...(digestHourUtc !== undefined ? { digestHourUtc } : {}),
        ...(instantAlerts !== undefined ? { instantAlerts: Boolean(instantAlerts) } : {}),
      },
    });
    res.json({
      digestEmail: shop.digestEmail ?? shop.email ?? '',
      digestEnabled: shop.digestEnabled,
      digestHourUtc: shop.digestHourUtc,
      instantAlerts: shop.instantAlerts,
    });
  } catch (err) {
    next(err);
  }
});

/** JSON error responses for the whole API surface. */
export function apiErrorHandler(err: unknown, _req: Request, res: Response, _next: NextFunction) {
  const status = statusOf(err);
  if (status >= 500) console.error('[api]', err);
  const message =
    status >= 500 ? 'Internal server error' : err instanceof Error ? err.message : 'Request failed';
  res.status(status).json({ error: message });
}
