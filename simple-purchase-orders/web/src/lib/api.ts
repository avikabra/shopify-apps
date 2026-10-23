import { useCallback, useMemo } from 'react';
import { authenticatedFetch } from '@shopify/app-bridge-utils';
import { useAppBridge } from '@shopify/app-bridge-react';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * App Bridge is absent in local mock-dev mode (apiKey === 'mock'); this hook
 * returns null there instead of throwing. Hook order is stable because the
 * mode never changes during the app's lifetime.
 */
function useAppBridgeOptional() {
  try {
    return useAppBridge();
  } catch {
    return null;
  }
}

/**
 * Authenticated API hook for use inside the App Bridge provider.
 * In mock-dev mode (no provider) it falls back to a plain fetch — the dev
 * session middleware on the server supplies the shop context.
 */
export function useApi() {
  const bridge = useAppBridgeOptional();
  const authFetch = useMemo(() => (bridge ? authenticatedFetch(bridge) : null), [bridge]);

  return useCallback(
    async <T = unknown>(path: string, options: RequestInit = {}): Promise<T> => {
      type Fetcher = (input: RequestInfo, init?: RequestInit) => Promise<Response>;
      const doFetch = async (fetcher: Fetcher): Promise<T> => {
        const res = await fetcher(path, {
          ...options,
          headers: {
            'Content-Type': 'application/json',
            ...(options.headers ?? {}),
          },
        });

        if (res.status === 402) {
          // Billing required — bounce the top frame to the plan picker.
          const data = (await res.json().catch(() => ({}))) as {
            billingUrl?: string;
          };
          if (data.billingUrl) {
            window.top!.location.href = data.billingUrl;
            throw new ApiError(402, 'Billing required — redirecting to plan selection');
          }
        }

        if (res.status === 204) return undefined as T;

        const text = await res.text();
        const data = text ? (JSON.parse(text) as T & { error?: string }) : ({} as T);
        if (!res.ok) {
          throw new ApiError(
            res.status,
            (data as { error?: string }).error || `Request failed (${res.status})`,
          );
        }
        return data as T;
      };

      try {
        return await doFetch(authFetch ?? fetch);
      } catch (e) {
        // First paint can race App Bridge session-token readiness and come back
        // 403; retry once with an authenticated fetch before surfacing the error.
        if (e instanceof ApiError && e.status === 403 && authFetch) {
          await new Promise((r) => setTimeout(r, 750));
          return doFetch(authFetch);
        }
        throw e;
      }
    },
    [authFetch],
  );
}

/** Unauthenticated fetch for /api/config (used before App Bridge boots). */
export async function fetchConfig(): Promise<{ apiKey: string }> {
  const res = await fetch('/api/config');
  if (!res.ok) throw new Error('Could not load app config');
  return res.json();
}
