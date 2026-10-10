import { prisma } from '../db.js';
import { clientForShop } from '../shopify/client.js';
import { evaluateLowStock } from './alerts.js';
import type { Ctx } from './purchaseOrders.js';

export interface DashboardStats {
  lowStockCount: number;
  lowStockItems: { sku: string; available: number; minQty: number; locationName?: string }[];
  openPurchaseOrders: number;
  inTransitTransfers: number;
  /** Sum of available qty x unit cost over threshold-tracked items. */
  trackedInventoryValue: number;
  recentActivity: {
    id: string;
    type: string;
    refNumber?: string | null;
    sku: string;
    qty: number;
    locationName?: string | null;
    createdAt: Date;
  }[];
}

export async function getDashboardStats(ctx: Ctx): Promise<DashboardStats> {
  const [openPOs, inTransit, recentActivity, lowStock] = await Promise.all([
    prisma.purchaseOrder.count({
      where: { shopId: ctx.shopId, status: { in: ['DRAFT', 'SENT', 'PARTIALLY_RECEIVED'] } },
    }),
    prisma.stockTransfer.count({
      where: { shopId: ctx.shopId, status: 'IN_TRANSIT' },
    }),
    prisma.receivingEvent.findMany({
      where: { shopId: ctx.shopId },
      orderBy: { createdAt: 'desc' },
      take: 10,
    }),
    evaluateLowStock(ctx).catch(() => []),
  ]);

  // Inventory value over tracked items only (documented limitation).
  let trackedInventoryValue = 0;
  try {
    const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);
    const thresholds = await prisma.stockThreshold.findMany({
      where: { shopId: ctx.shopId },
    });
    const itemIds = [...new Set(thresholds.map((t) => t.inventoryItemId))];
    if (itemIds.length) {
      const [levels, variants] = await Promise.all([
        adapter.getLevels(itemIds),
        adapter.getVariants(itemIds),
      ]);
      const costById = new Map(
        variants.map((v) => [v.inventoryItemId, v.unitCost ?? 0]),
      );
      const availableById = new Map<string, number>();
      for (const l of levels) {
        availableById.set(
          l.inventoryItemId,
          (availableById.get(l.inventoryItemId) ?? 0) + l.available,
        );
      }
      for (const [id, qty] of availableById) {
        trackedInventoryValue += qty * (costById.get(id) ?? 0);
      }
    }
  } catch {
    // Inventory value is informational; never fail the dashboard over it.
  }

  return {
    lowStockCount: lowStock.length,
    lowStockItems: lowStock.slice(0, 5).map((i) => ({
      sku: i.sku,
      available: i.available,
      minQty: i.minQty,
      locationName: i.locationName,
    })),
    openPurchaseOrders: openPOs,
    inTransitTransfers: inTransit,
    trackedInventoryValue: Math.round(trackedInventoryValue * 100) / 100,
    recentActivity: recentActivity.map((e) => ({
      id: e.id,
      type: e.type,
      refNumber: e.refNumber,
      sku: e.sku,
      qty: e.qty,
      locationName: e.locationName,
      createdAt: e.createdAt,
    })),
  };
}
