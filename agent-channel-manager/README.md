# Agent Channel Manager

Control what AI shopping agents (ChatGPT, Copilot, Google AI Mode) can discover
from your catalog — and at what price. Protect margin as agentic commerce grows.

## What it does

- **Product visibility controls** — blocklist or allowlist mode. Hide low-margin
  or MAP-sensitive SKUs from AI agents, per product or per collection, or flip
  to allowlist so agents only see explicitly approved products.
- **Agent-specific pricing rules** — price floors, percentage markups/discounts
  vs. storefront price, and discount-code exclusions, scoped to all products,
  one product, or one collection.
- **Agent-only promotions** — bundles and free-shipping thresholds that sync an
  *unlisted* discount code to Shopify. Wins agent comparisons on value without
  touching your advertised price.
- **Margin guard simulator** — "how your store looks to an AI agent": per-product
  agent price, margin estimate, and feed-risk flags in one table.

### Google Shopping price-match guardrail

Discount rules that push the agent price below your advertised storefront price
require explicit merchant acknowledgement, because checkout prices below the
product-feed price risk Google Shopping feed disapproval. The simulator flags
every feed-risk item. Prefer agent-only promotions to compete on value.

## Run locally

```bash
cd agent-channel-manager
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # mock mode works with no credentials
uvicorn app.main:app --reload
```

Open http://localhost:8000/dev/login — this creates a demo shop (mock catalog)
and drops you into the embedded admin UI.

## Run tests

```bash
pytest
```

## Deploy (Railway)

```bash
railway init            # or link an existing project
railway add --database postgres
railway variables --set "DATABASE_URL=${{Postgres.DATABASE_URL}}" \
  --set "SHOPIFY_API_KEY=..." \
  --set "SHOPIFY_API_SECRET=..." \
  --set "SHOPIFY_CLIENT_MODE=real" \
  --set "APP_URL=https://<your-app>.up.railway.app" \
  --set "ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --set "SKIP_AUTO_CREATE_TABLES=0"
railway up
```

Required Shopify OAuth scopes: `read_products`, `read_product_listings`,
`write_discounts`, `read_discounts`.

## Project layout

```
app/
  main.py          FastAPI app, lifespan, /health, /dev/login
  config.py        pydantic-settings
  db.py            SQLAlchemy engine/session
  deps.py          auth chain (session token -> id_token -> cookie -> HMAC -> mock)
  models.py        Shop, VisibilityRule, PricingRule, AgentPromotion, WebhookEvent
  security.py      HMAC verify, Fernet tokens, OAuth state, session cookies
  services.py      channel evaluation, pricing engine, margin simulator
  webhooks.py      app/uninstalled, shop/update, GDPR handlers
  shopify/         protocol.py (seam), client.py (GraphQL), mock.py (fixtures)
  routes/          auth.py (OAuth), admin.py (UI), webhooks.py (receiver)
  templates/       Jinja2 admin UI
tests/             pytest suite (40+ tests)
```

## Plans (freemium)

- **Free ($0)**: 10 visibility rules, 2 pricing rules, 1 promotion.
- **Pro ($19/mo)**: unlimited everything.
