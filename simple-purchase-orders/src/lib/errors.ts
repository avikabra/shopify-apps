/**
 * Throw for invalid user input. The API error middleware maps these to 400;
 * anything else becomes a 500.
 */
export function badRequest(message: string): Error {
  return Object.assign(new Error(message), { status: 400 });
}

export function statusOf(err: unknown): number {
  return (err as { status?: number } | null)?.status ?? 500;
}
