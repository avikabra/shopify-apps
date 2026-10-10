// Tiny money helpers. Amounts are stored as Decimal(12,2) in Postgres and
// handled as integer minor units (cents) in pure logic to avoid float drift.

export function toCents(amount: number | string | null | undefined): number {
  if (amount === null || amount === undefined || amount === '') return 0;
  return Math.round(Number(amount) * 100);
}

export function fromCents(cents: number): number {
  return cents / 100;
}

export function formatMoney(cents: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
  }).format(cents / 100);
}
