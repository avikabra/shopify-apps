# Build notes — Simple Purchase Orders & Stock Alerts

Built 2026-10-01 on a Linux VM (Node v24.20.0, npm 10.9.4).

## Verified working

- `npm install` (root + `web/`) — clean.
- `npx prisma generate` — works, but required a workaround on this VM (see below).
- `npx tsc --noEmit` — clean for backend and frontend.
- `npm test` — **29/29 pass** (`src/lib/__tests__/`: poMachine 14, thresholds 8,
  transfers 7). These cover the PO state machine, receive math, transfer
  adjustment building + stock preflight, and threshold evaluation.
- `npm --prefix web run build` — Vite production build succeeds.
- Mock-mode boot smoke test (`SHOPIFY_MOCK=true DEV_FAKE_SHOP=demo.myshopify.com`):
  server starts, `/api/config` returns `{"apiKey":"mock"}`, `/` serves the built
  SPA (200), DB-backed endpoints return a clean 500 JSON (`{"error":"Internal
  server error"}`) with no DB present and the process stays up.

## NOT yet verified (needs a dev store + real API keys)

- OAuth install flow (`/api/auth`, `/api/auth/callback`) against a real shop.
  **Status 2026-10-01 ~03:25 ET:** a free dev store is live
  (`simple-apps-dev.myshopify.com`, confirmed reachable — storefront returns
  302 to the password page as expected). Real OAuth testing was **not feasible
  from this subagent**: the app's API key/secret live in the Partner dashboard,
  which this agent cannot open (no live browser), and the install grant needs
  an interactive browser session as the store owner. The server correctly
  refuses to boot in real mode without credentials (clear error, verified).
  Full step-by-step runbook for the parent agent: `OAUTH_TEST_RUNBOOK.md`.
- Real Admin GraphQL calls: product/variant search, inventory levels,
  `inventoryAdjustQuantities`, location listing.
- `appSubscriptionCreate` billing mutation shapes (written from docs; the exact
  `userErrors` handling should be exercised once).
- Webhook delivery (`app/uninstalled`, `inventory_levels/update`, privacy
  webhooks) end-to-end.
- Email sending over real SMTP (`EMAIL_MODE=smtp`); `log` mode is the default.
- `prisma migrate dev` / `migrate deploy` against a live Postgres.
- The digest cron across timezones (logic is a simple UTC-hour match).

## Known edge cases / judgment calls

- **PO receive ordering:** the Shopify inventory adjustment is applied *first*,
  then the DB transaction records it. If the process dies between the two, a
  retry could double-adjust. Mitigation recorded for later: store an
  idempotency key (`referenceDocumentUri` already carries `po:<id>:line:<id>`)
  and check `ReceivingEvent` rows before re-applying.
- **Transfers** are paired +/- adjustments, not atomic. Same crash-window caveat
  as above; acceptable for an MVP, surfaced in code comments.
- **`unitCost` on variants** is read from the mock fixture; on real Shopify it
  comes from `inventoryItem.unitCost` — confirm field availability on the dev
  store (it requires the `read_inventory` scope, which we request).
- **Express 5:** no `/api/*` wildcard routes (path-to-regexp v8 changed
  semantics); routers are mounted explicitly and the SPA fallback uses a regex.
- **Session model** matches `@shopify/shopify-app-session-storage-prisma` v11's
  documented columns exactly (incl. `refreshToken`/`refreshTokenExpires`).

## Environment quirks found on this VM

- `@shopify/shopify-app-express@^3.2.0` does not exist on npm; the current
  major is **v8** (needs Express 5 as a peer). Pinned: express `^5.1.0`,
  `@shopify/shopify-api` `^15.0.0`, `@shopify/shopify-app-session-storage-prisma`
  `^11.0.0`.
- The Prisma CLI's own HTTP client cannot complete TLS through this VM's egress
  proxy (`binaries.prisma.sh`); `curl` works fine. Workaround used:
  downloaded `schema-engine.gz` + `libquery_engine.so.node.gz` (+ `.sha256`
  files) with curl into `~/.cache/prisma/master/<commit>/debian-openssl-3.0.x/`,
  then ran generate with `PRISMA_SCHEMA_ENGINE_BINARY` /
  `PRISMA_QUERY_ENGINE_LIBRARY` pointing at the extracted files, and copied the
  `.so` to `node_modules/.prisma/client/libquery_engine-debian-openssl-3.0.x.so.node`.
  On any normal network `npx prisma generate` just works — no code changes needed.
- No local Postgres on this VM (the `apt-get install postgresql` attempt was
  stopped), so DB-backed integration testing is pending a hosted database.
- Polaris 12 notes: `Text` requires the `as` prop; `Modal` sizing is
  `size="large"` (no `large` boolean).
