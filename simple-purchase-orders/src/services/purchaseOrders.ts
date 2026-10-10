import { badRequest } from "../lib/errors.js";
import { prisma } from '../db.js';
import {
  applyReceive,
  canTransition,
  type PoStatus,
} from '../lib/poMachine.js';
import { formatPoNumber, nextSequence } from '../lib/sequences.js';
import { clientForShop } from '../shopify/client.js';
import { sendEmail } from './email.js';
import { renderPurchaseOrderPdf, type PoPdfData } from './poPdf.js';

export interface Ctx {
  shopId: string;
  shopDomain: string;
  accessToken?: string;
}

export interface CreatePoLineInput {
  inventoryItemId: string;
  qtyOrdered: number;
  unitCost?: number;
}

export async function listPurchaseOrders(ctx: Ctx, status?: string) {
  return prisma.purchaseOrder.findMany({
    where: {
      shopId: ctx.shopId,
      ...(status ? { status: status as PoStatus } : {}),
    },
    include: {
      supplier: { select: { id: true, name: true } },
      _count: { select: { lines: true } },
    },
    orderBy: { createdAt: 'desc' },
  });
}

export async function getPurchaseOrder(ctx: Ctx, id: string) {
  const po = await prisma.purchaseOrder.findFirst({
    where: { id, shopId: ctx.shopId },
    include: { supplier: true, lines: true },
  });
  if (!po) throw badRequest('Purchase order not found');
  return po;
}

export async function createPurchaseOrder(
  ctx: Ctx,
  input: {
    supplierId: string;
    locationId?: string;
    notes?: string;
    expectedAt?: string;
    lines: CreatePoLineInput[];
  },
) {
  if (!input.lines?.length) throw badRequest('A purchase order needs at least one line');
  for (const l of input.lines) {
    if (!Number.isInteger(l.qtyOrdered) || l.qtyOrdered <= 0) {
      throw badRequest('Each line needs a positive integer quantity');
    }
  }

  const supplier = await prisma.supplier.findFirst({
    where: { id: input.supplierId, shopId: ctx.shopId },
  });
  if (!supplier) throw badRequest('Supplier not found');

  const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);
  const itemIds = [...new Set(input.lines.map((l) => l.inventoryItemId))];
  const variants = await adapter.getVariants(itemIds);
  const byId = new Map(variants.map((v) => [v.inventoryItemId, v]));
  for (const id of itemIds) {
    if (!byId.has(id)) throw badRequest(`Unknown inventory item: ${id}`);
  }

  let locationId = input.locationId;
  let locationName: string | undefined;
  const locations = await adapter.getLocations();
  if (locationId) {
    const loc = locations.find((l) => l.id === locationId);
    if (!loc) throw badRequest('Receiving location not found');
    locationName = loc.name;
  } else {
    const firstActive = locations.find((l) => l.isActive) ?? locations[0];
    if (!firstActive) throw badRequest('No locations available on this shop');
    locationId = firstActive.id;
    locationName = firstActive.name;
  }

  const poNumber = formatPoNumber(await nextSequence(ctx.shopId, 'po'));

  return prisma.purchaseOrder.create({
    data: {
      shopId: ctx.shopId,
      supplierId: supplier.id,
      poNumber,
      status: 'DRAFT',
      notes: input.notes,
      expectedAt: input.expectedAt ? new Date(input.expectedAt) : undefined,
      locationId,
      locationName,
      lines: {
        create: input.lines.map((l) => {
          const v = byId.get(l.inventoryItemId)!;
          return {
            inventoryItemId: l.inventoryItemId,
            sku: v.sku,
            productTitle: v.productTitle,
            variantTitle: v.variantTitle,
            unitCost: l.unitCost ?? v.unitCost ?? undefined,
            qtyOrdered: l.qtyOrdered,
          };
        }),
      },
    },
    include: { lines: true, supplier: true },
  });
}

/** DRAFT -> SENT, emailing the PO (with PDF) to the supplier. */
export async function sendPurchaseOrder(ctx: Ctx, id: string) {
  const po = await getPurchaseOrder(ctx, id);
  if (po.status !== 'DRAFT') {
    throw badRequest(`Only draft purchase orders can be sent (current: ${po.status})`);
  }
  if (!po.supplier.email) {
    throw badRequest('This supplier has no email address — add one before sending');
  }

  const pdf = await renderPurchaseOrderPdf(toPdfData(po));
  await sendEmail({
    to: po.supplier.email,
    subject: `Purchase order ${po.poNumber}`,
    text:
      `Hi${po.supplier.contactName ? ` ${po.supplier.contactName}` : ''},\n\n` +
      `Please find purchase order ${po.poNumber} attached.\n\n` +
      (po.notes ? `Notes: ${po.notes}\n\n` : ''),
    html:
      `<p>Hi${po.supplier.contactName ? ` ${po.supplier.contactName}` : ''},</p>` +
      `<p>Please find purchase order <strong>${po.poNumber}</strong> attached.</p>` +
      (po.notes ? `<p>Notes: ${po.notes}</p>` : ''),
    attachments: [
      { filename: `${po.poNumber}.pdf`, content: pdf, contentType: 'application/pdf' },
    ],
  });

  return prisma.purchaseOrder.update({
    where: { id: po.id },
    data: { status: 'SENT', sentAt: new Date() },
    include: { lines: true, supplier: true },
  });
}

/**
 * Record receiving `qty` units against one PO line and book the stock increase
 * in Shopify. The Shopify adjustment happens first; the DB write follows, so
 * a Shopify failure never records phantom stock. (See BUILD_NOTES for the
 * residual double-adjust retry edge.)
 */
export async function receivePurchaseOrderLine(
  ctx: Ctx,
  id: string,
  lineId: string,
  qty: number,
) {
  const po = await getPurchaseOrder(ctx, id);
  const current = po.lines.map((l) => ({
    id: l.id,
    qtyOrdered: l.qtyOrdered,
    qtyReceived: l.qtyReceived,
  }));
  const { lines: nextLines, newStatus } = applyReceive(
    po.status as PoStatus,
    current,
    lineId,
    qty,
  );
  const receivedLine = nextLines.find((l) => l.id === lineId)!;
  const dbLine = po.lines.find((l) => l.id === lineId)!;

  const adapter = clientForShop(ctx.shopDomain, ctx.accessToken);
  await adapter.adjust(
    [{ inventoryItemId: dbLine.inventoryItemId, locationId: po.locationId, delta: qty }],
    'received',
    `spo://po/${po.poNumber}`,
  );

  const updated = await prisma.$transaction(async (tx) => {
    await tx.purchaseOrderLine.update({
      where: { id: lineId },
      data: { qtyReceived: receivedLine.qtyReceived },
    });
    await tx.receivingEvent.create({
      data: {
        shopId: ctx.shopId,
        type: 'PO_RECEIVE',
        refId: po.id,
        refNumber: po.poNumber,
        inventoryItemId: dbLine.inventoryItemId,
        sku: dbLine.sku,
        locationId: po.locationId,
        locationName: po.locationName,
        qty,
        note: `Received against ${po.poNumber}`,
      },
    });
    return tx.purchaseOrder.update({
      where: { id: po.id },
      data: { status: newStatus },
      include: { lines: true, supplier: true },
    });
  });
  return updated;
}

export async function cancelPurchaseOrder(ctx: Ctx, id: string) {
  const po = await getPurchaseOrder(ctx, id);
  if (!canTransition(po.status as PoStatus, 'CANCELLED')) {
    throw badRequest(`Cannot cancel a purchase order with status ${po.status}`);
  }
  return prisma.purchaseOrder.update({
    where: { id: po.id },
    data: { status: 'CANCELLED' },
    include: { lines: true, supplier: true },
  });
}

export async function purchaseOrderPdf(ctx: Ctx, id: string): Promise<{ filename: string; buffer: Buffer }> {
  const po = await getPurchaseOrder(ctx, id);
  const buffer = await renderPurchaseOrderPdf(toPdfData(po));
  return { filename: `${po.poNumber}.pdf`, buffer };
}

function toPdfData(po: Awaited<ReturnType<typeof getPurchaseOrder>>): PoPdfData {
  return {
    poNumber: po.poNumber,
    status: po.status,
    createdAt: po.createdAt,
    expectedAt: po.expectedAt,
    notes: po.notes,
    supplier: {
      name: po.supplier.name,
      contactName: po.supplier.contactName,
      email: po.supplier.email,
      phone: po.supplier.phone,
    },
    locationName: po.locationName,
    lines: po.lines.map((l) => ({
      sku: l.sku,
      productTitle: l.productTitle,
      variantTitle: l.variantTitle,
      qtyOrdered: l.qtyOrdered,
      qtyReceived: l.qtyReceived,
      unitCost: l.unitCost === null ? null : Number(l.unitCost),
    })),
  };
}
