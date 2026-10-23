import { Router, type Request, type Response, type NextFunction } from 'express';
import { prisma } from '../db.js';
import { config } from '../config.js';
import { sessionFromReq } from '../middleware/devAuth.js';
import { graphqlAdmin, clientForShop } from '../shopify/client.js';
import { badRequest, statusOf } from '../lib/errors.js';
import {
  hasActiveSubscription,
  createSubscription,
  cancelSubscription,
  getActiveSubscriptions,
} from '../shopify/billing.js';
import {
  PLANS,
  FEATURE_MIN_PLAN,
  planAtLeast,
  skuLimitLabel,
  type PlanKey,
} from '../lib/plans.js';
import {
  countTrackedSkus,
  currentPlan,
  isSkuTracked,
  requireFeature,
  requireSkuCapacity,
  setPlan,
  PlanLimitError,
} from '../services/billing.js';
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

/**
 * Freemium gate: every shop gets at least the free plan, so this never blocks.
 * It best-effort syncs the plan from Shopify so downgrades/cancellations are
 * picked up promptly. Per-feature limits are enforced by planGate() below.
 */
async function requireBilling(req: Request, res: Response, next: NextFunction) {
  try {
    if (!config.billingEnforce) return next();
    const ctx = reqCtx(req);
    await currentPlan(ctx);
    next();
  } catch (err) {
    next(err);
  }
}

/** Wrap a plan check so 402s carry the billingUrl the frontend redirects to. */
function planGate(check: (req: Request, ctx: Ctx) => Promise<void>) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      if (!config.billingEnforce) return next();
      await check(req, reqCtx(req));
      next();
    } catch (err) {
      if (err instanceof PlanLimitError) {
        res.status(402).json({ error: err.message, billingUrl: err.billingUrl });
        return;
      }
      next(err);
    }
  };
}

const requireFeaturePlan = (feature: keyof typeof FEATURE_MIN_PLAN) =>
  planGate((_req, ctx) => requireFeature(ctx, feature, true));

const requireSkuForBody =
  (field: string) =>
  planGate(async (req, ctx) => {
    const itemId = req.body?.[field] ? String(req.body[field]) : undefined;
    const addingNew = itemId ? !(await isSkuTracked(ctx.shopId, itemId)) : true;
    await requireSkuCapacity(ctx, true, addingNew);
  });

/** Full billing state for the Billing page. */
api.get('/billing', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const plan = await currentPlan(ctx);
    const skusUsed = await countTrackedSkus(ctx.shopId);
    let subscription: { id: string; name: string; status: string } | null = null;
    if (ctx.accessToken && !config.mockShopify) {
      try {
        const subs = await getActiveSubscriptions(ctx.shopDomain, ctx.accessToken);
        if (subs[0]) {
          subscription = { id: subs[0].id, name: subs[0].name, status: subs[0].status };
        }
      } catch {
        // Best-effort; DB plan is the source of truth for the UI.
      }
    }
    res.json({
      plan,
      plans: Object.values(PLANS).map((p) => ({
        key: p.key,
        name: p.name,
        price: p.price,
        trialDays: p.trialDays,
        skuLimit: p.skuLimit,
        skuLimitLabel: skuLimitLabel(p.key),
        blurb: p.blurb,
        current: p.key === plan,
      })),
      skusUsed,
      skuLimit: PLANS[plan].skuLimit,
      skuLimitLabel: skuLimitLabel(plan),
      features: Object.fromEntries(
        Object.keys(FEATURE_MIN_PLAN).map((f) => [
          f,
          {
            minPlan: FEATURE_MIN_PLAN[f],
            minPlanName: PLANS[FEATURE_MIN_PLAN[f]].name,
            allowed: planAtLeast(plan, FEATURE_MIN_PLAN[f]),
          },
        ]),
      ),
      subscription,
    });
  } catch (err) {
    next(err);
  }
});

api.get('/billing/status', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const plan = await currentPlan(ctx);
    const active =
      !config.billingEnforce ||
      !ctx.accessToken ||
      config.mockShopify ||
      plan !== 'free' ||
      (await hasActiveSubscription(ctx.shopDomain, ctx.accessToken));
    res.json({ active, enforce: config.billingEnforce, plan });
  } catch (err) {
    next(err);
  }
});

api.get('/billing/start', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    const raw = String(req.query.plan ?? 'starter');
    const plan: PlanKey = raw === 'growth' ? 'growth' : raw === 'free' ? 'free' : 'starter';
    if (!ctx.accessToken || config.mockShopify) {
      throw badRequest('Billing is not available in mock mode.');
    }
    if (plan === 'free') {
      // Downgrade path: cancel any active subscription, then record free.
      try {
        const subs = await getActiveSubscriptions(ctx.shopDomain, ctx.accessToken);
        for (const s of subs) {
          await cancelSubscription(ctx.shopDomain, ctx.accessToken, s.id);
        }
      } catch (err) {
        console.warn('[billing] downgrade cancel failed:', (err as Error).message);
      }
      await setPlan(ctx.shopId, 'free');
      res.redirect(`/?shop=${ctx.shopDomain}`);
      return;
    }
    const confirmationUrl = await createSubscription(
      ctx.shopDomain,
      ctx.accessToken,
      plan,
      `${config.host}/api/billing/callback?shop=${ctx.shopDomain}&plan=${plan}`,
    );
    res.redirect(302, confirmationUrl);
  } catch (err) {
    next(err);
  }
});

/** Explicit downgrade to Free (cancels the Shopify subscription). */
api.post('/billing/downgrade', async (req, res, next) => {
  try {
    const ctx = reqCtx(req);
    if (!ctx.accessToken || config.mockShopify) {
      await setPlan(ctx.shopId, 'free');
      res.json({ ok: true, plan: 'free' });
      return;
    }
    try {
      const subs = await getActiveSubscriptions(ctx.shopDomain, ctx.accessToken);
      for (const s of subs) {
        await cancelSubscription(ctx.shopDomain, ctx.accessToken, s.id);
      }
    } catch (err) {
      console.warn('[billing] downgrade cancel failed:', (err as Error).message);
    }
    await setPlan(ctx.shopId, 'free');
    res.json({ ok: true, plan: 'free' });
  } catch (err) {
    next(err);
  }
});

api.get('/billing/callback', async (req, res) => {
  // Shopify redirects here (top-level) after the merchant approves/declines.
  // Plan reconciliation happens on the next authenticated API call via
  // requireBilling -> currentPlan, so this just bounces back into the app.
  // (Never trust the ?plan= param here — a declined checkout must not grant
  // paid features.)
  const shop = String(req.query.shop ?? '');
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

api.post('/suppliers/:id/skus', requireSkuForBody('inventoryItemId'), async (req, res, next) => {
  try {
    const { inventoryItemId, sku, variantTitle, unitCost } = req.body ?? {};
    if (!inventoryItemId || !sku) throw badRequest('inventoryItemId and sku are required.');
    res.status(201).json(
      await addSupplierSku(reqCtx(req), String(req.params.id), {
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

api.post(
  '/thresholds',
  requireFeaturePlan('alerts'),
  requireSkuForBody('inventoryItemId'),
  async (req, res, next) => {
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
  },
);

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

api.get('/low-stock', requireFeaturePlan('alerts'), async (req, res, next) => {
  try {
    res.json(await evaluateLowStock(reqCtx(req)));
  } catch (err) {
    next(err);
  }
});

api.post('/alerts/check-now', requireFeaturePlan('alerts'), async (req, res, next) => {
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

api.post('/purchase-orders/:id/send', requireFeaturePlan('pdf'), async (req, res, next) => {
  try {
    res.json(await sendPurchaseOrder(reqCtx(req), String(req.params.id)));
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

api.get('/purchase-orders/:id/pdf', requireFeaturePlan('pdf'), async (req, res, next) => {
  try {
    const { filename, buffer } = await purchaseOrderPdf(reqCtx(req), String(req.params.id));
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

api.post('/transfers', requireFeaturePlan('transfers'), async (req, res, next) => {
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
