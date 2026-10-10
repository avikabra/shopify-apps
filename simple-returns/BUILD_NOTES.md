# BUILD_NOTES — Simple Returns & Exchanges

Built 2026-10-01 (overnight) on a local VM. No Shopify partner account, dev
store, or third-party credentials were available, so everything below is
explicit about what was verified vs. what must be confirmed on a dev store.

## What was tested (this VM)

- **61 pytest tests, all passing** (`pytest -q`):
  - `test_rules.py` (13): pure rules engine — window boundaries, excluded
    tags/IDs, quantity caps, reason allowlists (case-insensitive), priority
    ordering, disabled rules, naive datetimes.
  - `test_services.py` (15): full lifecycle against mock adapters — submit →
    auto-approve → label → settle for refund / store credit / exchange;
    manual review queue path; reject; idempotent re-approve and re-settle;
    plan-limit enforcement; invalid quantity/reason/unfulfilled-item rejections;
    label failure doesn't block approval (stays `approved`, retry from
    dashboard, customer gets "label on its way" email).
  - `test_webhooks.py` (10): HMAC rejection at the route boundary,
    `app/uninstalled`, `returns/approve` + `returns/decline` sync,
    idempotent unknown-return events, audit logging of every delivery, GDPR
    `customers/redact` anonymization, `customers/data_request` payload,
    `shop/redact` wipe.
  - `test_security.py` (9): webhook/OAuth HMAC roundtrips and tamper
    rejection, Fernet encrypt/decrypt, OAuth state freshness + secret binding.
  - `test_routes.py` (14): portal lookup/submit/variants/reasons, origin-gate
    unit test (mock vs. production settings), admin dashboard/detail/settings/
    billing pages, approve→settle flow through HTTP.
- **Live smoke test** (uvicorn + curl, mock mode): `/dev/login` → dashboard →
  portal lookup → portal submit (auto-approved) → admin settle → refunded.
  Verified the approve-without-warehouse-address path stays `approved` and the
  customer email copy doesn't promise a missing label.
- **Alembic migration**: `alembic upgrade head` verified against a scratch
  SQLite DB — all 6 tables created. (The Docker CMD runs migrations on boot.)

## Verified against live Shopify docs (2026-10-01, shopify.dev)

- Mutations used: `returnRequest`, `returnApproveRequest`,
  `returnDeclineRequest`, `refundCreate`, `giftCardCreate`, `draftOrderCreate`,
  `draftOrderInvoiceSend`, `webhookSubscriptionCreate`.
- `refundCreate` requires an idempotency key via the `@idempotent(key: ...)`
  directive as of API 2026-04 — implemented (server-generated `sr-<uuid>` key
  stored per request, reused on retry).
- Webhook topics registered: `app/uninstalled`, `returns/*` (request, approve,
  decline, cancel, close, reopen, process, update), `refunds/create`, plus
  mandatory GDPR `customers/data_request`, `customers/redact`, `shop/redact`.
- `ReturnReason` enum values used for the portal dropdown (COLOR, DEFECTIVE,
  NOT_AS_DESCRIBED, OTHER, SIZE_TOO_LARGE, SIZE_TOO_SMALL, STYLE, UNKNOWN,
  UNWANTED, WRONG_ITEM).
- API version pinned to `2026-07` (`SHOPIFY_API_VERSION`).
- OAuth scopes requested: `read_orders write_orders read_returns write_returns
  read_customers read_products read_draft_orders write_draft_orders
  read_gift_cards write_gift_cards`.

## Could NOT be tested without a dev store + partner app

1. **OAuth handshake** (`/auth`, `/auth/callback`): HMAC/state logic is unit
   tested, but the real redirect + token exchange needs a partner app.
2. **Real webhook delivery**: HMAC verification and handlers are tested with
   fixtures, but actual delivery, retry behavior, and payload shapes from
   Shopify need a dev store.
3. **GraphQL mutations end-to-end**: `returnRequest`/`returnApproveRequest`/
   `refundCreate`/`giftCardCreate`/`draftOrderCreate` were verified in docs but
   never executed against a real store. Highest-risk items:
   - `refundCreate` `@idempotent(key: ...)` exact directive syntax.
   - `giftCardCreate` exact input shape (assumed `initialValue`/`customerId`).
   - `draftOrderInvoiceSend` input shape (assumed `{id}`).
   - OAuth scope strings: docs now render some scopes as `orders`/`returns`,
     but the OAuth request strings are believed to remain `read_*`/`write_*`
     (community skill docs updated ~Sept 2026 still show the old form).
     Confirm on the dev store; if Shopify rejects, adjust `oauth_scopes`.
4. **Theme app extension install**: the extension files are written but never
   deployed via `shopify app deploy` or added to a real theme.
5. **App Bridge session-token auth**: implemented (stdlib JWT HS256 verify)
   but untested against real tokens — the `/dev/login` + `?shop=` path is what
   mock mode uses.
6. **EasyPost label purchase**: REST flow implemented per docs, fixture-tested
   only. Real buy flow + cheapest-rate selection needs an EasyPost key.
7. **SMTP delivery**: `smtp.py` implemented, never sent a real email.

## Known limitations / v1 cuts

- **Billing is a placeholder.** The Billing page records plan changes directly
  in the DB. Before app-store submission, wire Shopify Billing API recurring
  subscriptions (`appSubscriptionCreate`) and gate plan changes on them.
- **One draft order per exchange request** (first item only). Multi-item
  exchanges need one draft order with all line items — small follow-up.
- **No background job queue.** Shopify/EasyPost calls run inline in the
  request. Fine for MVP volumes; move portal submit + label generation to a
  worker (e.g. arq/dramatiq) before scaling.
- **Rate limiting is in-memory per process** (60 req/min/IP on portal routes).
  Use Redis-backed limiting with multiple workers.
- **No merchant notification for label retries** beyond the approval email;
  no SMS; no multi-language portal (English only).
- **Return label parcel dims are fixed** (12×9×6 in, 16 oz). Per-product
  weights/dims are a natural v2.
- **Refund restock** uses `RETURN`/`NO_RESTOCK` per the merchant's choice on
  settle; no per-item granularity.
- **Admin dashboard has no search**; queue is capped at 100 rows.
- **Session tokens**: no token caching/rotation handling; App Bridge snippet
  in `base.html` is minimal.
- **SQLite default**: fine for dev; production needs Postgres
  (`postgresql+psycopg://...`) — the Docker CMD and `psycopg` dep assume this.
- **CORS**: portal allows the shop's `*.myshopify.com` origin plus
  merchant-configured custom domains (Settings page). Storefronts on
  unlisted domains will be blocked — merchant must add them.

## Security notes

- Shop access tokens are Fernet-encrypted at rest (`ENCRYPTION_KEY`).
- Webhook HMAC verified with constant-time compare before any processing.
- Portal auth = order number + email match; identical 404 for "no order" vs
  "wrong email" (no enumeration); destructive admin routes need a valid
  session token (or mock-mode `?shop=`).
- `shop/redact` wipes requests + token; `customers/redact` anonymizes PII.
- Never log tokens, HMAC secrets, or gift-card codes.
