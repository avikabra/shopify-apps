import { badRequest } from "./errors.js";
// Pure purchase-order state machine. No I/O, no framework imports — fully unit-testable.

export type PoStatus =
  | 'DRAFT'
  | 'SENT'
  | 'PARTIALLY_RECEIVED'
  | 'RECEIVED'
  | 'CANCELLED';

const TRANSITIONS: Record<PoStatus, PoStatus[]> = {
  DRAFT: ['SENT', 'CANCELLED'],
  SENT: ['PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED'],
  PARTIALLY_RECEIVED: ['RECEIVED', 'CANCELLED'],
  RECEIVED: [],
  CANCELLED: [],
};

export function canTransition(from: PoStatus, to: PoStatus): boolean {
  return TRANSITIONS[from].includes(to);
}

export interface PoLineState {
  id: string;
  qtyOrdered: number;
  qtyReceived: number;
}

export interface ReceiveResult {
  lines: PoLineState[];
  receivedNow: { lineId: string; qty: number };
  newStatus: 'PARTIALLY_RECEIVED' | 'RECEIVED';
}

/**
 * Apply a receiving event to a PO's lines. Throws on any invalid input so
 * callers can turn it into a 400 response. Never mutates the input lines.
 */
export function applyReceive(
  currentStatus: PoStatus,
  lines: PoLineState[],
  lineId: string,
  qty: number,
): ReceiveResult {
  if (currentStatus !== 'SENT' && currentStatus !== 'PARTIALLY_RECEIVED') {
    throw badRequest(
      `Cannot receive items on a purchase order with status ${currentStatus}`,
    );
  }
  if (!Number.isInteger(qty) || qty <= 0) {
    throw badRequest('Received quantity must be a positive integer');
  }
  const line = lines.find((l) => l.id === lineId);
  if (!line) {
    throw badRequest('Purchase order line not found');
  }
  const remaining = line.qtyOrdered - line.qtyReceived;
  if (qty > remaining) {
    throw badRequest(
      `Cannot receive ${qty} units: only ${remaining} remaining on this line`,
    );
  }
  const nextLines = lines.map((l) =>
    l.id === lineId ? { ...l, qtyReceived: l.qtyReceived + qty } : l,
  );
  const allReceived = nextLines.every((l) => l.qtyReceived >= l.qtyOrdered);
  return {
    lines: nextLines,
    receivedNow: { lineId, qty },
    newStatus: allReceived ? 'RECEIVED' : 'PARTIALLY_RECEIVED',
  };
}

export function poTotals(lines: PoLineState[]): {
  ordered: number;
  received: number;
  remaining: number;
} {
  const ordered = lines.reduce((sum, l) => sum + l.qtyOrdered, 0);
  const received = lines.reduce((sum, l) => sum + l.qtyReceived, 0);
  return { ordered, received, remaining: ordered - received };
}
