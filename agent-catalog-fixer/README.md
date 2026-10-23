# Agent-Ready Catalog Fixer

Make every product buyable by an AI shopping agent (ChatGPT, Perplexity, Google AI Mode).

AI agents are becoming a real sales channel — but they skip products they can't verify. In one widely-cited test, **28.8% of AI agent purchases were wrong** because catalog data lacked the evidence to verify constraints (missing GTINs, thin descriptions, no structured data). This app audits your catalog 0–100 on agent-readiness and bulk auto-fixes the gaps.

## What it does

1. **Audit engine** — scores every product 0–100 across six factors:
   - Identifiers (GTIN/barcode presence + check-digit validity) — 20 pts
   - Description length & richness (flags < 80 words) — 15 pts
   - Structured data (FAQ / schema.org signals) — 15 pts
   - Image alt text coverage — 15 pts
   - Variant completeness (price, title) — 15 pts
   - Shopify Catalog eligibility (brand, product type, published, tags) — 20 pts
2. **Dashboard** — product list with scores and grades (A–F), filterable by issue.
3. **Auto-fixes** (one click each; bulk on Pro):
   - Assign valid GTIN-12 barcodes where missing (labeled as placeholders — replace with GS1-issued GTINs)
   - Expand thin descriptions with attribute-rich copy built from your existing data
   - Draft FAQs and store Product + FAQPage JSON-LD in product metafields
   - Fill missing image alt text from product titles
4. **Continuous monitoring** — `products/update` webhooks re-score changed products automatically.

## Tech

- FastAPI + SQLAlchemy (SQLite for dev, Postgres via `DATABASE_URL`)
- Shopify Admin API (GraphQL) for product reads/writes
- Mock Shopify client for dev/tests (no credentials needed)
- Deterministic, template-based fixes — no paid APIs, no LLM calls

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults run in MOCK mode
uvicorn app.main:app --reload
```

Then open http://localhost:8000/dev/login — this creates a demo shop with a
fixture catalog (one excellent, one terrible, one middling product) and drops
you into the dashboard. Run a full audit and try the auto-fixes.

## Real Shopify mode

1. Create an app in the Shopify Dev Dashboard; set the App URL and
   `https://<your-host>/auth/callback` as the redirect URL.
2. Fill in `.env`: `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `APP_URL`,
   `SHOPIFY_CLIENT_MODE=real`, `DATABASE_URL` (Postgres), `ENCRYPTION_KEY`.
3. Required OAuth scopes: `read_products`, `write_products`.
4. Webhooks registered on install: `app/uninstalled`, `products/update`,
   `products/create`, plus GDPR topics.

## Deployment (Railway)

```bash
railway init            # or link an existing project
railway add --database postgres
railway variables --set "SHOPIFY_API_KEY=..." --set "SHOPIFY_API_SECRET=..." \
  --set "APP_URL=https://<service>.up.railway.app" \
  --set "SHOPIFY_CLIENT_MODE=real" \
  --set "ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --set "SKIP_AUTO_CREATE_TABLES=1"
railway up
```

## Plans (freemium)

- **Free** — audit up to 25 products, one-by-one auto-fixes.
- **Pro ($19/mo)** — unlimited products, bulk auto-fix, continuous webhook monitoring.

## Tests

```bash
pytest   # 87 tests
```
