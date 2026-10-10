import { badRequest } from "./errors.js";
// Stock-transfer helpers. Transfers are implemented as paired inventory
// adjustments (decrement source, increment destination). Pure logic here;
// the Shopify adapter executes the resulting changes.

export interface TransferLineInput {
  inventoryItemId: string;
  sku: string;
  qty: number;
}

export interface InventoryChangeInput {
  inventoryItemId: string;
  locationId: string;
  delta: number;
}

export interface StockLevelInput {
  inventoryItemId: string;
  locationId: string;
  available: number;
}

/**
 * Build the paired +/- adjustments for a transfer. Throws on invalid input.
 */
export function buildTransferAdjustments(args: {
  fromLocationId: string;
  toLocationId: string;
  lines: TransferLineInput[];
}): InventoryChangeInput[] {
  const { fromLocationId, toLocationId, lines } = args;

  if (!fromLocationId || !toLocationId) {
    throw badRequest('Source and destination locations are required');
  }
  if (fromLocationId === toLocationId) {
    throw badRequest('Source and destination locations must be different');
  }
  if (lines.length === 0) {
    throw badRequest('A transfer must include at least one line');
  }

  const changes: InventoryChangeInput[] = [];
  for (const line of lines) {
    if (!line.inventoryItemId) {
      throw badRequest(`Line for SKU ${line.sku} is missing an inventory item`);
    }
    if (!Number.isInteger(line.qty) || line.qty <= 0) {
      throw badRequest(
        `Invalid quantity for SKU ${line.sku}: must be a positive integer`,
      );
    }
    changes.push({
      inventoryItemId: line.inventoryItemId,
      locationId: fromLocationId,
      delta: -line.qty,
    });
    changes.push({
      inventoryItemId: line.inventoryItemId,
      locationId: toLocationId,
      delta: line.qty,
    });
  }
  return changes;
}

/**
 * Pre-flight check: returns a list of human-readable errors for lines where
 * the source location does not hold enough stock. Empty list = good to go.
 */
export function checkTransferStock(
  levels: StockLevelInput[],
  fromLocationId: string,
  lines: TransferLineInput[],
): string[] {
  const errors: string[] = [];
  for (const line of lines) {
    const level = levels.find(
      (l) =>
        l.inventoryItemId === line.inventoryItemId &&
        l.locationId === fromLocationId,
    );
    const available = level?.available ?? 0;
    if (available < line.qty) {
      errors.push(
        `SKU ${line.sku}: only ${available} available at the source location, requested ${line.qty}`,
      );
    }
  }
  return errors;
}
