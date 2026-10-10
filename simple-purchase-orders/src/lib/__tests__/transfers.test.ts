import { describe, expect, it } from 'vitest';
import {
  buildTransferAdjustments,
  checkTransferStock,
} from '../transfers.js';

const FROM = 'gid://shopify/Location/1';
const TO = 'gid://shopify/Location/2';
const ITEM = 'gid://shopify/InventoryItem/100';

describe('buildTransferAdjustments', () => {
  it('builds paired -/+ adjustments per line', () => {
    const changes = buildTransferAdjustments({
      fromLocationId: FROM,
      toLocationId: TO,
      lines: [{ inventoryItemId: ITEM, sku: 'SKU-100', qty: 5 }],
    });
    expect(changes).toEqual([
      { inventoryItemId: ITEM, locationId: FROM, delta: -5 },
      { inventoryItemId: ITEM, locationId: TO, delta: 5 },
    ]);
  });

  it('nets to zero across each pair (nothing created or destroyed)', () => {
    const changes = buildTransferAdjustments({
      fromLocationId: FROM,
      toLocationId: TO,
      lines: [
        { inventoryItemId: ITEM, sku: 'SKU-100', qty: 5 },
        { inventoryItemId: 'gid://shopify/InventoryItem/200', sku: 'SKU-200', qty: 3 },
      ],
    });
    const total = changes.reduce((sum, c) => sum + c.delta, 0);
    expect(total).toBe(0);
    expect(changes).toHaveLength(4);
  });

  it('rejects same source and destination', () => {
    expect(() =>
      buildTransferAdjustments({
        fromLocationId: FROM,
        toLocationId: FROM,
        lines: [{ inventoryItemId: ITEM, sku: 'SKU-100', qty: 1 }],
      }),
    ).toThrow(/different/);
  });

  it('rejects empty lines and non-positive quantities', () => {
    expect(() =>
      buildTransferAdjustments({ fromLocationId: FROM, toLocationId: TO, lines: [] }),
    ).toThrow(/at least one line/);
    expect(() =>
      buildTransferAdjustments({
        fromLocationId: FROM,
        toLocationId: TO,
        lines: [{ inventoryItemId: ITEM, sku: 'SKU-100', qty: 0 }],
      }),
    ).toThrow(/positive integer/);
  });
});

describe('checkTransferStock', () => {
  const levels = [{ inventoryItemId: ITEM, locationId: FROM, available: 4 }];

  it('returns no errors when stock is sufficient', () => {
    expect(
      checkTransferStock(levels, FROM, [{ inventoryItemId: ITEM, sku: 'SKU-100', qty: 4 }]),
    ).toEqual([]);
  });

  it('reports insufficient stock with the numbers', () => {
    const errors = checkTransferStock(levels, FROM, [
      { inventoryItemId: ITEM, sku: 'SKU-100', qty: 10 },
    ]);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatch(/only 4 available/);
  });

  it('treats missing level rows as zero stock', () => {
    const errors = checkTransferStock([], FROM, [
      { inventoryItemId: ITEM, sku: 'SKU-100', qty: 1 },
    ]);
    expect(errors).toHaveLength(1);
  });
});
