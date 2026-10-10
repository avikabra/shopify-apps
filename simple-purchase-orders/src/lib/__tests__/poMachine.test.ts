import { describe, expect, it } from 'vitest';
import {
  applyReceive,
  canTransition,
  poTotals,
  type PoStatus,
} from '../poMachine.js';

describe('canTransition', () => {
  it('allows the happy path DRAFT -> SENT -> RECEIVED', () => {
    expect(canTransition('DRAFT', 'SENT')).toBe(true);
    expect(canTransition('SENT', 'RECEIVED')).toBe(true);
  });

  it('allows partial receiving then completion', () => {
    expect(canTransition('SENT', 'PARTIALLY_RECEIVED')).toBe(true);
    expect(canTransition('PARTIALLY_RECEIVED', 'RECEIVED')).toBe(true);
  });

  it('allows cancelling from any open state', () => {
    for (const from of ['DRAFT', 'SENT', 'PARTIALLY_RECEIVED'] as PoStatus[]) {
      expect(canTransition(from, 'CANCELLED')).toBe(true);
    }
  });

  it('blocks transitions out of terminal states', () => {
    expect(canTransition('RECEIVED', 'SENT')).toBe(false);
    expect(canTransition('CANCELLED', 'DRAFT')).toBe(false);
    expect(canTransition('DRAFT', 'RECEIVED')).toBe(false);
    expect(canTransition('DRAFT', 'PARTIALLY_RECEIVED')).toBe(false);
  });
});

describe('applyReceive', () => {
  const lines = [
    { id: 'l1', qtyOrdered: 10, qtyReceived: 0 },
    { id: 'l2', qtyOrdered: 5, qtyReceived: 0 },
  ];

  it('receives a partial quantity and moves to PARTIALLY_RECEIVED', () => {
    const res = applyReceive('SENT', lines, 'l1', 4);
    expect(res.newStatus).toBe('PARTIALLY_RECEIVED');
    expect(res.lines.find((l) => l.id === 'l1')!.qtyReceived).toBe(4);
    expect(res.receivedNow).toEqual({ lineId: 'l1', qty: 4 });
  });

  it('moves to RECEIVED when every line is fully received', () => {
    const step1 = applyReceive('SENT', lines, 'l1', 10);
    expect(step1.newStatus).toBe('PARTIALLY_RECEIVED');
    const step2 = applyReceive(step1.newStatus, step1.lines, 'l2', 5);
    expect(step2.newStatus).toBe('RECEIVED');
  });

  it('accepts the exact remaining quantity on a line', () => {
    const res = applyReceive('PARTIALLY_RECEIVED', [{ id: 'l1', qtyOrdered: 10, qtyReceived: 7 }], 'l1', 3);
    expect(res.newStatus).toBe('RECEIVED');
  });

  it('rejects receiving on a DRAFT purchase order', () => {
    expect(() => applyReceive('DRAFT', lines, 'l1', 1)).toThrow(/DRAFT/);
  });

  it('rejects receiving on a RECEIVED purchase order', () => {
    expect(() => applyReceive('RECEIVED', lines, 'l1', 1)).toThrow(/RECEIVED/);
  });

  it('rejects quantities larger than the remaining amount', () => {
    expect(() =>
      applyReceive('SENT', [{ id: 'l1', qtyOrdered: 10, qtyReceived: 8 }], 'l1', 3),
    ).toThrow(/only 2 remaining/);
  });

  it('rejects zero and negative quantities', () => {
    expect(() => applyReceive('SENT', lines, 'l1', 0)).toThrow(/positive integer/);
    expect(() => applyReceive('SENT', lines, 'l1', -2)).toThrow(/positive integer/);
  });

  it('rejects unknown line ids', () => {
    expect(() => applyReceive('SENT', lines, 'nope', 1)).toThrow(/not found/);
  });

  it('does not mutate the input lines', () => {
    const input = [{ id: 'l1', qtyOrdered: 10, qtyReceived: 0 }];
    applyReceive('SENT', input, 'l1', 5);
    expect(input[0].qtyReceived).toBe(0);
  });
});

describe('poTotals', () => {
  it('sums ordered, received and remaining', () => {
    expect(
      poTotals([
        { id: 'l1', qtyOrdered: 10, qtyReceived: 4 },
        { id: 'l2', qtyOrdered: 5, qtyReceived: 5 },
      ]),
    ).toEqual({ ordered: 15, received: 9, remaining: 6 });
  });
});
