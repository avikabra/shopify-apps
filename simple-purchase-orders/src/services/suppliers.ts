import { prisma } from '../db.js';
import { badRequest } from '../lib/errors.js';
import type { Ctx } from './purchaseOrders.js';

async function ownSupplier(ctx: Ctx, supplierId: string) {
  const supplier = await prisma.supplier.findFirst({
    where: { id: supplierId, shopId: ctx.shopId },
  });
  if (!supplier) throw badRequest('Supplier not found');
  return supplier;
}

export async function listSuppliers(ctx: Ctx) {
  return prisma.supplier.findMany({
    where: { shopId: ctx.shopId },
    include: { skus: true },
    orderBy: { name: 'asc' },
  });
}

export async function createSupplier(
  ctx: Ctx,
  input: { name: string; email?: string; contactName?: string; phone?: string; notes?: string },
) {
  if (!input.name.trim()) throw badRequest('Supplier name is required');
  return prisma.supplier.create({
    data: {
      shopId: ctx.shopId,
      name: input.name.trim(),
      email: input.email?.trim() || null,
      contactName: input.contactName?.trim() || null,
      phone: input.phone?.trim() || null,
      notes: input.notes?.trim() || null,
    },
    include: { skus: true },
  });
}

export async function updateSupplier(
  ctx: Ctx,
  supplierId: string,
  input: { name?: string; email?: string; contactName?: string; phone?: string; notes?: string },
) {
  await ownSupplier(ctx, supplierId);
  const data: Record<string, string | null> = {};
  if (input.name !== undefined) {
    if (!input.name.trim()) throw badRequest('Supplier name is required');
    data.name = input.name.trim();
  }
  for (const k of ['email', 'contactName', 'phone', 'notes'] as const) {
    if (input[k] !== undefined) data[k] = input[k]?.trim() || null;
  }
  return prisma.supplier.update({
    where: { id: supplierId },
    data,
    include: { skus: true },
  });
}

export async function deleteSupplier(ctx: Ctx, supplierId: string) {
  await ownSupplier(ctx, supplierId);
  const used = await prisma.purchaseOrder.count({
    where: { supplierId, status: { not: 'CANCELLED' } },
  });
  if (used > 0) throw badRequest('This supplier has purchase orders; cancel them first');
  await prisma.supplier.delete({ where: { id: supplierId } });
}

export async function addSupplierSku(
  ctx: Ctx,
  supplierId: string,
  input: { inventoryItemId: string; sku: string; variantTitle?: string; unitCost?: number },
) {
  await ownSupplier(ctx, supplierId);
  if (!input.inventoryItemId || !input.sku.trim()) {
    throw badRequest('inventoryItemId and sku are required');
  }
  return prisma.supplierSku.upsert({
    where: { supplierId_inventoryItemId: { supplierId, inventoryItemId: input.inventoryItemId } },
    create: {
      supplierId,
      inventoryItemId: input.inventoryItemId,
      sku: input.sku.trim(),
      variantTitle: input.variantTitle?.trim() || null,
      unitCost: input.unitCost != null ? String(input.unitCost) : null,
    },
    update: {
      sku: input.sku.trim(),
      variantTitle: input.variantTitle?.trim() || null,
      unitCost: input.unitCost != null ? String(input.unitCost) : null,
    },
  });
}

export async function removeSupplierSku(ctx: Ctx, supplierId: string, skuId: string) {
  await ownSupplier(ctx, supplierId);
  const sku = await prisma.supplierSku.findFirst({
    where: { id: skuId, supplierId },
  });
  if (!sku) throw badRequest('Supplier SKU not found');
  await prisma.supplierSku.delete({ where: { id: sku.id } });
}
