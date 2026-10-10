import { describe, expect, it } from 'vitest';
import {
  ALL_LOCATIONS,
  findLowStock,
  type StockLevel,
  type ThresholdRule,
} from '../thresholds.js';

const LOC_A = 'gid://shopify/Location/1';
const LOC_B = 'gid://shopify/Location/2';

function rule(overrides: Partial<ThresholdRule> = {}): ThresholdRule {
  return {
    inventoryItemId: 'gid://shopify/InventoryItem/100',
    sku: 'SKU-100',
    productTitle: 'Widget',
    variantTitle: 'Blue',
    locationId: LOC_A,
    locationName: 'Warehouse A',
    minQty: 10,
    ...overrides,
  };
}

describe('findLowStock', () => {
  it('flags an item at exactly the threshold (at-or-below semantics)', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 10 },
    ];
    const out = findLowStock(levels, [rule()]);
    expect(out).toHaveLength(1);
    expect(out[0].deficit).toBe(0);
  });

  it('does not flag items above the threshold', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 11 },
    ];
    expect(findLowStock(levels, [rule()])).toHaveLength(0);
  });

  it('treats a missing level row as zero stock', () => {
    expect(findLowStock([], [rule()])).toHaveLength(1);
  });

  it('aggregates across locations for ALL_LOCATIONS rules', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 6 },
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_B, available: 6 },
    ];
    const all = rule({ locationId: ALL_LOCATIONS, minQty: 15 });
    const out = findLowStock(levels, [all]);
    expect(out).toHaveLength(1);
    expect(out[0].available).toBe(12);
    expect(out[0].locationName).toBe('All locations');
  });

  it('does not flag an ALL_LOCATIONS rule when the total is healthy', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 9 },
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_B, available: 9 },
    ];
    expect(
      findLowStock(levels, [rule({ locationId: ALL_LOCATIONS, minQty: 15 })]),
    ).toHaveLength(0);
  });

  it('evaluates each location independently for per-location rules', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 3 },
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_B, available: 50 },
    ];
    const out = findLowStock(levels, [
      rule({ locationId: LOC_A, minQty: 10 }),
      rule({ locationId: LOC_B, minQty: 10 }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].locationId).toBe(LOC_A);
  });

  it('sorts lowest availability first', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 8 },
      { inventoryItemId: 'gid://shopify/InventoryItem/200', locationId: LOC_A, available: 2 },
    ];
    const out = findLowStock(levels, [
      rule({ inventoryItemId: 'gid://shopify/InventoryItem/100', sku: 'SKU-100', minQty: 10 }),
      rule({ inventoryItemId: 'gid://shopify/InventoryItem/200', sku: 'SKU-200', minQty: 10 }),
    ]);
    expect(out.map((i) => i.sku)).toEqual(['SKU-200', 'SKU-100']);
  });

  it('ignores rules for unrelated inventory items', () => {
    const levels: StockLevel[] = [
      { inventoryItemId: 'gid://shopify/InventoryItem/999', locationId: LOC_A, available: 0 },
    ];
    expect(findLowStock(levels, [rule()])).toHaveLength(1); // missing row => 0 stock
    expect(
      findLowStock(
        [{ inventoryItemId: 'gid://shopify/InventoryItem/100', locationId: LOC_A, available: 50 }],
        [rule()],
      ),
    ).toHaveLength(0);
  });
});
