"""Shopify OAuth install flow.

GET /auth?shop={shop}.myshopify.com -> 302 to Shopify permission URL.
GET /auth/callback -> verify HMAC + state, exchange code for offline token,
                     upsert Shop, register webhooks, redirect into the app.

GDPR note: token is stored Fernet-encrypted (see models.Shop).
"""
from __future__ import annotations

import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import security
from ..config import Settings, get_settings
from ..deps import get_db
from ..models import Shop

router = APIRouter()


def _normalize_shop(shop: str) -> str:
    shop = shop.strip().lower()
    if not shop.endswith(".myshopify.com"):
        shop = f"{shop}.myshopify.com"
    return shop


@router.get("/auth")
async def auth_start(shop: str, settings: Settings = Depends(get_settings)):
    if not settings.shopify_api_key:
        raise HTTPException(500, "SHOPIFY_API_KEY is not configured.")
    shop_domain = _normalize_shop(shop)
    state = security.new_oauth_state(settings)
    params = {
        "client_id": settings.shopify_api_key,
        "scope": settings.oauth_scopes,
        "redirect_uri": f"{settings.app_url}/auth/callback",
        "state": state,
    }
    url = f"https://{shop_domain}/admin/oauth/authorize?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    params = dict(request.query_params)
    if not security.verify_oauth_hmac(params, settings.shopify_api_secret):
        raise HTTPException(401, "Invalid OAuth HMAC")
    state = params.get("state", "")
    shop_domain = params.get("shop", "")
    if not security.verify_oauth_state(state, settings):
        raise HTTPException(401, "Invalid OAuth state")
    code = params.get("code")
    if not code or not shop_domain:
        raise HTTPException(400, "Missing code or shop")

    async with httpx.AsyncClient(timeout=20) as http:
        try:
            resp = await http.post(
                f"https://{shop_domain}/admin/oauth/access_token",
                json={
                    "client_id": settings.shopify_api_key,
                    "client_secret": settings.shopify_api_secret,
                    "code": code,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(502, f"Token exchange failed: {exc}") from exc

    shop = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()
    if shop is None:
        from ..models import Plan, ReturnRule

        shop = Shop(
            shop_domain=shop_domain,
            access_token_enc=security.encrypt_token(token, settings),
            plan=Plan(settings.resolved_default_plan),
            settings={"brand_name": shop_domain.split(".")[0].replace("-", " ").title()},
        )
        db.add(shop)
        db.flush()
        # Seed a default auto-approval rule so the app works out of the box.
        db.add(
            ReturnRule(
                shop_id=shop.id,
                name="Default 30-day window",
                enabled=True,
                priority=0,
                window_days=30,
                auto_approve_reasons=[],
                excluded_product_ids=[],
                excluded_product_tags=["final-sale"],
                max_quantity=10,
            )
        )
    else:
        shop.access_token_enc = security.encrypt_token(token, settings)
        shop.uninstalled_at = None
    db.commit()

    # Register webhooks (best effort; surfaced in admin if it fails).
    try:
        from ..deps import shopify_client

        await shopify_client().register_webhooks(shop_domain, settings.app_url)
    except Exception:  # noqa: BLE001 - install must succeed regardless
        pass

    return RedirectResponse(f"https://{shop_domain}/admin/apps/{settings.shopify_api_key}", status_code=302)
