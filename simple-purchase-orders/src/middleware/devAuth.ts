import type { NextFunction, Request, Response } from 'express';
import { config } from '../config.js';

export interface RequestSession {
  shop: string;
  accessToken?: string;
}

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      spoSession?: RequestSession;
    }
  }
}

/**
 * Local-dev escape hatch: when DEV_FAKE_SHOP is set (and not production),
 * every request gets a fake session for that shop and the mock Shopify
 * adapter is used. This makes the whole app demoable with zero credentials.
 */
export function devSessionMiddleware(
  req: Request,
  _res: Response,
  next: NextFunction,
): void {
  if (config.devFakeShop && process.env.NODE_ENV !== 'production') {
    req.spoSession = { shop: config.devFakeShop };
  }
  next();
}

/**
 * Resolve the caller's shop + access token, preferring a real Shopify
 * session (set by validateAuthenticatedSession on res.locals) and falling
 * back to the dev fake session.
 */
export function sessionFromReq(req: Request, res: Response): RequestSession {
  const real = (res.locals as Record<string, unknown> | undefined)?.shopify as
    | { session?: { shop: string; accessToken?: string } }
    | undefined;
  if (real?.session?.shop) {
    return { shop: real.session.shop, accessToken: real.session.accessToken };
  }
  if (req.spoSession) return req.spoSession;
  throw new Error('No Shopify session found for this request');
}
