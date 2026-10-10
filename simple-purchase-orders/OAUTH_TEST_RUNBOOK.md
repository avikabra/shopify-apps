# OAuth install test runbook — Simple Purchase Orders

Goal: install the app on the free dev store `simple-apps-dev.myshopify.com`
and exercise it end to end. No payment details anywhere; dev stores are free.

## 1. Partner dashboard (browser, logged in as Avi)

1. Partner dashboard → **Apps** → **Create app** → "Create app manually".
   - App name: `Simple Purchase Orders`
2. In the app's **App setup**:
   - **App URL**: the public tunnel URL from step 3 (e.g. `https://abc.trycloudflare.com`)
   - **Allowed redirection URLs**: add `<tunnel>/api/auth/callback`
3. **API credentials** → copy the **Client ID** (= API key) and **Client secret**.
4. **Test stores**: add `simple-apps-dev.myshopify.com` if not already there.

## 2. Public URL for this VM (no signup needed)

```bash
cloudflared tunnel --url http://localhost:3000
# note the https://<random>.trycloudflare.com URL
```

## 3. Database

The VM has no local Postgres. Easiest free options: Supabase, Neon, or Render
Postgres. Copy the connection string.

## 4. Boot the app

```bash
cd ~/workspace/shopify-apps/simple-purchase-orders
export SHOPIFY_API_KEY=<client id> SHOPIFY_API_SECRET=<client secret>
export HOST=https://<tunnel>.trycloudflare.com
export DATABASE_URL=<postgres connection string>
npx prisma migrate deploy
npm run dev   # or: npm run build && npm start
```

## 5. Install on the dev store

In the browser (logged in as Avi), visit:

```
https://<tunnel>.trycloudflare.com/api/auth?shop=simple-apps-dev.myshopify.com
```

Shopify shows the OAuth consent screen → **Install app** → redirects back to
the app inside the store admin.

## 6. What to verify

- [ ] App loads embedded in `admin.shopify.com/store/simple-apps-dev` (dashboard page)
- [ ] Create a supplier; link a variant via SKU search
- [ ] Create a purchase order, **Send** it (EMAIL_MODE=log prints the email; PDF downloads)
- [ ] **Receive** a line → check the variant's inventory actually increased in the
      store admin (Products → Inventory)
- [ ] Create a transfer between two locations; receive it
- [ ] Set a stock threshold; run **Check now**; confirm the low-stock list
- [ ] Webhooks: after OAuth, `app/uninstalled` + `inventory_levels/update` are
      auto-registered (shop-specific). Adjust stock in admin and watch for an
      instant alert (needs `instantAlerts` on + `digestEmail` set in Stock alerts page)

## Notes

- Keep `BILLING_ENFORCE` unset (false) for the test. The 14-day trial in the
  billing code only matters once enforcement is on.
- The tunnel URL changes on every `cloudflared` restart — update the Partner
  dashboard URLs to match.
- If the app is ever uninstalled from the test store, `app/uninstalled` wipes
  that shop's rows (GDPR) — reinstalling starts clean.
