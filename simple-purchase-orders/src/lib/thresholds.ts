// Low-stock threshold evaluation. Pure functions, no I/O.

/** Sentinel meaning "aggregate across every location" (avoids NULL-uniqueness issues). */
export const ALL_LOCATIONS = 'ALL_LOCATIONS';

export interface StockLevel {
  inventoryItemId: string;
  locationId: string;
  locationName?: string;
  available: number;
}

export interface ThresholdRule {
  inventoryItemId: string;
  sku: string;
  productTitle?: string;
  variantTitle?: string;
  /** A location GID, or ALL_LOCATIONS for the summed-across-locations rule. */
  locationId: string;
  locationName?: string;
  minQty: number;
}

export interface LowStockItem {
  inventoryItemId: string;
  sku: string;
  productTitle?: string;
  variantTitle?: string;
  locationId: string;
  locationName?: string;
  available: number;
  minQty: number;
  deficit: number;
}

/**
 * Evaluate threshold rules against current stock levels.
 * A rule fires when available <= minQty (at-or-below semantics, so a
 * threshold of 10 with exactly 10 on hand still alerts).
 * Sorted lowest-availability-first so the most urgent items come first.
 */
export function findLowStock(
  levels: StockLevel[],
  rules: ThresholdRule[],
): LowStockItem[] {
  const out: LowStockItem[] = [];

  for (const rule of rules) {
    if (rule.minQty < 0) continue;

    if (rule.locationId === ALL_LOCATIONS) {
      const total = levels
        .filter((l) => l.inventoryItemId === rule.inventoryItemId)
        .reduce((sum, l) => sum + l.available, 0);
      if (total <= rule.minQty) {
        out.push({
          inventoryItemId: rule.inventoryItemId,
          sku: rule.sku,
          productTitle: rule.productTitle,
          variantTitle: rule.variantTitle,
          locationId: ALL_LOCATIONS,
          locationName: 'All locations',
          available: total,
          minQty: rule.minQty,
          deficit: rule.minQty - total,
        });
      }
    } else {
      const level = levels.find(
        (l) =>
          l.inventoryItemId === rule.inventoryItemId &&
          l.locationId === rule.locationId,
      );
      const available = level?.available ?? 0;
      if (available <= rule.minQty) {
        out.push({
          inventoryItemId: rule.inventoryItemId,
          sku: rule.sku,
          productTitle: rule.productTitle,
          variantTitle: rule.variantTitle,
          locationId: rule.locationId,
          locationName: rule.locationName ?? level?.locationName,
          available,
          minQty: rule.minQty,
          deficit: rule.minQty - available,
        });
      }
    }
  }

  return out.sort((a, b) => a.available - b.available);
}
