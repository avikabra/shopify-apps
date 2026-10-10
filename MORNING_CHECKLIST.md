# Morning checklist — Shopify apps (2026-10-01)

## Done overnight
- **Simple Returns & Exchanges** (`simple-returns/`): built, 61/61 tests pass, mock-mode smoke test OK.
- **Simple Purchase Orders & Stock Alerts** (`simple-purchase-orders/`): built, 29/29 tests pass, mock-mode smoke test OK.
- Shopify Partner org created (Avi Kabra, av3kabra@gmail.com), email verified, no payment used.
- Dev store live: https://simple-apps-dev.myshopify.com
- Both apps created in the Partner dashboard ("Simple Returns", "Simple Purchase Orders").
  Client IDs are in each app's `.env`; client secrets are in the dashboard (Dev Dashboard > Apps > App settings > Credentials).
- Shared account password stored at `~/.jarvis-secrets/accounts-password` — give to Avi to write down, then delete.

## Needs Avi (morning)
1. **GitHub**: create empty public repo `shopify-apps` (my integration can't create repos), then I push both apps.
2. **Third app**: ship two strong apps, or build a speculative third (branded invoices / age verification were the least-bad options, neither validated).
3. **Hosting**: pick together — Render was vetoed; Fly.io/Railway need a card for free tier. Nothing deployed yet.
4. **Client secrets**: paste the two secrets from the dashboard into each `.env` (30 seconds).
5. **Postgres** for the PO app's real-mode test (no local Postgres — the install prompt was declined overnight, correctly left alone).

## Then I do
- Boot both apps against a public URL, set App URLs + redirect URLs in the dashboard, install on the dev store, run the OAuth runbooks (`OAUTH_TEST_RUNBOOK.md` in the PO app; returns app: `/auth?shop=simple-apps-dev.myshopify.com`).
- Draft app-store listing copy for approval (submission needs Avi's explicit OK; review takes 4–10 business days).
