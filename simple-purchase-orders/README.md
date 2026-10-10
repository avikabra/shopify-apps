# Simple Purchase Orders & Stock Alerts

A dead-simple Stocky replacement for Shopify merchants: create purchase orders,
email them to suppliers as PDFs, receive stock into Shopify inventory, move
stock between locations, and get low-stock alerts (daily digest + instant
webhooks).

Shopify removed Stocky from the App Store in February 2026 and shut it down on
August 31, 2026. This app covers the core Stocky workflow — POs, receiving,
transfers, and stock alerts — with a flat monthly price and no per-order fees.

## How it works

- **Suppliers** — name, contact info, and a mapping of your Shopify variants to
  the supplier's SKUs (with unit costs).
- **Purchase orders** — draft → send (emails a PDF to the supplier) → receive
  line-by-line. Receiving writes the stock into Shopify via
  `inventoryAdjustQuantities` (reason `received`), then records it locally.
- **Transfers** — move stock between locations. Implemented as paired
  +/- inventory adjustments (Shopify has no native transfer mutation).
- **Stock alerts** — per-variant/per-location minimum thresholds. A daily
  digest email lists everything at or below threshold; optional instant alerts
  fire on `inventory_levels/update` webhooks.

## Tech

- Backend: Node 24, Express 5, TypeScript, Prisma (PostgreSQL),
  `@shopify/shopify-app-express` v8, `@shopify/shopify-api` v15.
- Frontend: React 18 + Polaris 12 + App Bridge 3, built with Vite, served by
  Express as an embedded app.
- Billing: Shopify managed subscriptions — Starter $9/mo, Growth $19/mo,
  14-day free trial. Toggle with `BILLING_ENFORCE`.
- Email: nodemailer SMTP seam (`EMAIL_MODE=log` prints to stdout in dev).

All Shopify access goes through the `ShopifyInventoryClient` adapter
(`src/shopify/client.ts`), so the whole app runs against a local JSON fixture
with `SHOPIFY_MOCK=true` — no credentials needed for development.

## Local development (no Shopify credentials)

```bash
npm install
npm --prefix web install
# database (needs PostgreSQL)
npx prisma migrate dev
# run everything against the mock shop fixture
SHOPIFY_MOCK=true DEV_FAKE_SHOP=demo.myshopify.com npm run dev
# in another terminal, with the backend running:
npm --prefix web run dev   # or serve the built bundle
```

Open http://localhost:3000 — the dev session middleware treats every request
as `demo.myshopify.com` and all inventory calls hit `data/mock-shop.json`.

## Production deploy (Fly.io)

```bash
fly launch
fly secrets set SHOPIFY_API_KEY=... SHOPIFY_API_SECRET=... \
  DATABASE_URL=... HOST=https://<app>.fly.dev BILLING_ENFORCE=true \
  EMAIL_MODE=smtp SMTP_HOST=... SMTP_USER=... SMTP_PASS=...
fly deploy
```

The Dockerfile runs `prisma migrate deploy` on boot. Register the app in the
Partner dashboard with app URL = `HOST` and callback
`HOST/api/auth/callback`. Webhooks are shop-specific and reconciled on OAuth;
required scopes: `read_products,read_inventory,write_inventory,read_locations`.

## Testing

```bash
npm test        # 29 unit tests: PO state machine, transfer math, threshold evaluation
npx tsc --noEmit
```

See `BUILD_NOTES.md` for what was verified and what still needs a dev store.
