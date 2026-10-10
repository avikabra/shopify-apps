# Simple Returns & Exchanges

A self-serve Shopify app for returns, exchanges, and store credit — no sales
calls, no per-return fees, 5-minute setup. Flat pricing: **$12/mo Starter**
(50 returns/mo), **$24/mo Growth** (unlimited).

## What it does

- **Customer portal** (theme app extension): shoppers enter order number +
  email, pick items / quantities / reasons, choose refund, store credit, or
  exchange (with variant picker), and get a return shipping label by email.
- **Rules engine**: merchants define auto-approval rules (return window,
  reasons, excluded products/tags, max quantity). Matching requests are
  approved instantly; the rest land in a review queue.
- **Merchant dashboard** (embedded admin): review queue, one-click
  approve/reject, "mark received & settle" (refund via Shopify, gift card for
  store credit, draft order + invoice email for exchanges), return labels via
  EasyPost (cost passed through), plan usage & settings.
- **Webhooks**: return lifecycle events, `app/uninstalled`, and the mandatory
  GDPR topics (`customers/data_request`, `customers/redact`, `shop/redact`).

## Architecture

```
Shopify Admin API (GraphQL) ──▶ app/shopify/   protocol.py (seam)
                                                  client.py (real, httpx)
                                                  mock.py   (fixtures)
EasyPost (REST) ──────────────▶ app/easypost/  protocol.py / client.py / mock.py
Email ────────────────────────▶ app/email/     protocol.py / smtp.py / console.py
                                                   ▲
app/services.py  — ReturnService: the state machine (owns DB + orchestration)
app/rules.py     — pure rules engine (no I/O, fully unit-tested)
app/routes/      — auth (OAuth), webhooks, admin (dashboard), portal (customer API)
app/templates/   — server-rendered admin UI (Jinja2 + App Bridge via CDN)
extensions/return-portal/ — theme app extension (vanilla JS, no build step)
```

**Key design decision:** our database owns the pre-approval queue. The Shopify
`Return` object is created only when a request is approved
(`returnRequest` → `returnApproveRequest`). Money movement uses `refundCreate`
(with the mandatory `@idempotent` key), `giftCardCreate` (store credit), and
`draftOrderCreate` + `draftOrderInvoiceSend` (exchanges).

**Adapter seams:** every external service (Shopify, EasyPost, email) has a
`protocol.py` + real client + mock/fixture implementation. Swapping to real
credentials is a config change (`SHOPIFY_CLIENT_MODE=real`, etc.) — see
`.env.example`. No code changes needed.

## Run locally (no credentials needed)

```bash
cd simple-returns
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults already run in mock mode
# Generate an encryption key and put it in .env:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
uvicorn app.main:app --reload
```

Then open http://localhost:8000/dev/login — this creates a demo shop
(`demo.myshopify.com`) and drops you into the dashboard. The customer portal
flow works against fixture orders:

- Order **#1001** / `customer@example.com` — 2 fulfilled items (hoodie + cap,
  cap is tagged `final-sale` so it won't auto-approve)
- Order **#1002** / `customer@example.com` — 1 unfulfilled item (not returnable)

Try the portal API directly:

```bash
curl -X POST localhost:8000/portal/lookup \
  -H 'Content-Type: application/json' \
  -d '{"shop":"demo.myshopify.com","order_name":"1001","email":"customer@example.com"}'
```

## Run tests

```bash
pytest -q
```

61 tests: rules engine, service lifecycle (all three resolutions), webhook
handlers + HMAC, OAuth/security helpers, portal + admin routes — all against
mock adapters.

## Deploy

```bash
docker build -t simple-returns .
docker run -p 8000:8000 --env-file .env simple-returns
```

Or use `fly.toml` (`fly launch` / `fly deploy`) or `render.yaml`. Set
`DATABASE_URL` to Postgres, `SKIP_AUTO_CREATE_TABLES=1` (the container runs
`alembic upgrade head` on boot), and fill in the Shopify/EasyPost/SMTP secrets.
`APP_URL` must be the public HTTPS URL.

## Theme app extension

`extensions/return-portal/` is a Shopify theme app extension. Deploy with the
Shopify CLI from a partner app that shares this backend:

```bash
cd extensions/return-portal
shopify app deploy
```

Then add the "Return portal" block to a page (e.g. a dedicated Returns page)
in the theme editor, and set the **Simple Returns API URL** block setting to
your deployed `APP_URL`.

## What still needs a Shopify dev store + partner app

See `BUILD_NOTES.md` — it lists everything verified against live docs, what
was tested here, what could **not** be tested without real credentials (OAuth
handshake, real webhook delivery, GraphQL mutations end-to-end, app review),
and the known v1 cuts (notably: Shopify Billing API for real subscriptions).
