import { badRequest } from "../lib/errors.js";
import { prisma } from '../db.js';
import {
  buildTransferAdjustments,
  checkTransferStock,
  type TransferLineInput,
} from '../lib/transfers.js';
import { formatTransferNumber, nextSequence } from '../lib/sequences.js';
import { clientForShop } from '../shopify/client.js';
import type { Ctx } from './purchaseOrders.js';

/**
 * Transfer lifecycle (v1):
 * - create: validates, books the OUTBOUND decrement at the source immediately
 *   (goods are in transit), status IN_TRANSIT.
 * - receive: books the INBOUND increment at the destination, status RECEIVED.
 * - cancel: reverses the outbound decrement, status CANCELLED.
 */

export async function listTransfers(ctx: Ctx, status?: string) {
  return prisma.stockTransfer.findMany({
    where: { shopId: ctx.shopId, ...(status ? { status: status as never } : {}) },
    include: { _count: { select: { lines: true } } },
    orderBy: { createdAt: 'desc' },
  });
}

export async function getTransfer(ctx: Ctx, id: string) {
  const t = await prisma.stockTransfer.findFirst({
    where: { id, shopId: ctx.shopId },
    include: { lines: true },
  });
  if (!t) throw badRequest('Transfer not found');
  return t;
}

export async function createTransfer(
  ctx: Ctx,
  input: {
    fromLocationId: string;
    toLocationId: string;
    notes?: string;
    lines: TransferLineInput[];
  },
) {
  const changes = buildTransferAdjustments(input); // throws on invalid input
  const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);

  const locations = await adapter.getLocations();
  const from = locations.find((l) => l.id === input.fromLocationId);
  const to = locations.find((l) => l.id === input.toLocationId);
  if (!from || !to) throw badRequest('Source or destination location not found');
  if (!from.isActive || !to.isActive) {
    throw badRequest('Both locations must be active');
  }

  const itemIds = [...new Set(input.lines.map((l) => l.inventoryItemId))];
  const [variants, levels] = await Promise.all([
    adapter.getVariants(itemIds),
    adapter.getLevels(itemIds),
  ]);
  const byId = new Map(variants.map((v) => [v.inventoryItemId, v]));
  for (const id of itemIds) {
    if (!byId.has(id)) throw badRequest(`Unknown inventory item: ${id}`);
  }
  const stockErrors = checkTransferStock(
    levels.map((l) => ({
      inventoryItemId: l.inventoryItemId,
      locationId: l.locationId,
      available: l.available,
    })),
    input.fromLocationId,
    input.lines,
  );
  if (stockErrors.length) {
    throw badRequest(`Insufficient stock: ${stockErrors.join('; ')}`);
  }

  const transferNumber = formatTransferNumber(
    await nextSequence(ctx.shopId, 'transfer'),
  );

  // Book the outbound leg now — the goods are in transit.
  await adapter.adjust(
    changes.filter((c) => c.delta < 0),
    'correction',
    `spo://transfer/${transferNumber}`,
  );

  const created = await prisma.$transaction(async (tx) => {
    const transfer = await tx.stockTransfer.create({
      data: {
        shopId: ctx.shopId,
        transferNumber,
        fromLocationId: from.id,
        fromLocationName: from.name,
        toLocationId: to.id,
        toLocationName: to.name,
        status: 'IN_TRANSIT',
        notes: input.notes,
        lines: {
          create: input.lines.map((l) => {
            const v = byId.get(l.inventoryItemId)!;
            return {
              inventoryItemId: l.inventoryItemId,
              sku: v.sku,
              productTitle: v.productTitle,
              variantTitle: v.variantTitle,
              qty: l.qty,
            };
          }),
        },
      },
      include: { lines: true },
    });
    await tx.receivingEvent.createMany({
      data: input.lines.map((l) => ({
        shopId: ctx.shopId,
        type: 'TRANSFER_RECEIVE' as const,
        refId: transfer.id,
        refNumber: transferNumber,
        inventoryItemId: l.inventoryItemId,
        sku: byId.get(l.inventoryItemId)!.sku,
        locationId: from.id,
        locationName: from.name,
        qty: -l.qty,
        note: `Transfer ${transferNumber} shipped from ${from.name}`,
      })),
    });
    return transfer;
  });
  return created;
}

export async function receiveTransfer(ctx: Ctx, id: string) {
  const transfer = await getTransfer(ctx, id);
  if (transfer.status !== 'IN_TRANSIT') {
    throw badRequest(`Only in-transit transfers can be received (current: ${transfer.status})`);
  }
  const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);
  await adapter.adjust(
    transfer.lines.map((l) => ({
      inventoryItemId: l.inventoryItemId,
      locationId: transfer.toLocationId,
      delta: l.qty,
    })),
    'received',
    `spo://transfer/${transfer.transferNumber}`,
  );

  return prisma.$transaction(async (tx) => {
    await tx.receivingEvent.createMany({
      data: transfer.lines.map((l) => ({
        shopId: ctx.shopId,
        type: 'TRANSFER_RECEIVE' as const,
        refId: transfer.id,
        refNumber: transfer.transferNumber,
        inventoryItemId: l.inventoryItemId,
        sku: l.sku,
        locationId: transfer.toLocationId,
        locationName: transfer.toLocationName,
        qty: l.qty,
        note: `Transfer ${transfer.transferNumber} received at ${transfer.toLocationName}`,
      })),
    });
    return tx.stockTransfer.update({
      where: { id: transfer.id },
      data: { status: 'RECEIVED', receivedAt: new Date() },
      include: { lines: true },
    });
  });
}

export async function cancelTransfer(ctx: Ctx, id: string) {
  const transfer = await getTransfer(ctx, id);
  if (transfer.status !== 'IN_TRANSIT') {
    throw badRequest(`Only in-transit transfers can be cancelled (current: ${transfer.status})`);
  }
  const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);
  // Reverse the outbound leg: put the stock back at the source.
  await adapter.adjust(
    transfer.lines.map((l) => ({
      inventoryItemId: l.inventoryItemId,
      locationId: transfer.fromLocationId,
      delta: l.qty,
    })),
    'correction',
    `spo://transfer/${transfer.transferNumber}/cancel`,
  );

  return prisma.stockTransfer.update({
    where: { id: transfer.id },
    data: { status: 'CANCELLED' },
    include: { lines: true },
  });
}
