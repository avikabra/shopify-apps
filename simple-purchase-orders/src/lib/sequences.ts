import { prisma } from '../db.js';

/**
 * Allocate the next number from a per-shop sequence with optimistic locking.
 * Returns the allocated value (1-based).
 */
export async function nextSequence(
  shopId: string,
  name: 'po' | 'transfer',
): Promise<number> {
  for (let attempt = 0; attempt < 5; attempt++) {
    const seq = await prisma.sequence.upsert({
      where: { shopId_name: { shopId, name } },
      create: { shopId, name, value: 1 },
      update: {},
    });
    // Only one concurrent caller can win this compare-and-swap.
    const updated = await prisma.sequence.updateMany({
      where: { shopId, name, value: seq.value },
      data: { value: seq.value + 1 },
    });
    if (updated.count === 1) {
      return seq.value + 1;
    }
    // Lost the race — re-read and retry.
  }
  throw new Error('Could not allocate a sequence number (concurrent update conflict)');
}

export function formatPoNumber(n: number): string {
  return `PO-${String(n).padStart(4, '0')}`;
}

export function formatTransferNumber(n: number): string {
  return `TR-${String(n).padStart(4, '0')}`;
}
