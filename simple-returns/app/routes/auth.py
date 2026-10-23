"""Shopify OAuth install flow.

GET /auth?shop={shop}.myshopify.com -> 302 to Shopify permission URL.
GET /auth/callback -> verify HMAC + state, exchange code for offline token,
                     upsert Shop, register webhooks, redirect into the app.

GDPR note: token is stored Fernet-encrypted (see models.Shop).
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

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
        "scope": ",".join(settings.oauth_scopes),
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
                    # Shopify rejects non-expiring offline tokens for the
                    # Admin API; request an expiring token + refresh token.
                    "expiring": 1,
                },
            )
            resp.raise_for_status()
            token_payload = resp.json()
            token = token_payload["access_token"]
            refresh_token = token_payload.get("refresh_token")
            expires_in = token_payload.get("expires_in")
            granted_scopes = token_payload.get("scope", "")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(502, f"Token exchange failed: {exc}") from exc

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in
        else None
    )
    refresh_token_enc = (
        security.encrypt_token(refresh_token, settings) if refresh_token else None
    )

    shop = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()
    if shop is None:
        from ..models import Plan, ReturnRule

        shop = Shop(
            shop_domain=shop_domain,
            access_token_enc=security.encrypt_token(token, settings),
            refresh_token_enc=refresh_token_enc,
            access_token_expires_at=expires_at,
            scopes=granted_scopes,
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
        shop.refresh_token_enc = refresh_token_enc
        shop.access_token_expires_at = expires_at
        if granted_scopes:
            shop.scopes = granted_scopes
        shop.uninstalled_at = None
    db.commit()

    # Register webhooks (best effort; surfaced in admin if it fails).
    try:
        from ..deps import shopify_client

        await shopify_client().register_webhooks(shop_domain, settings.app_url)
    except Exception:  # noqa: BLE001 - install must succeed regardless
        pass

    resp = RedirectResponse(
        f"https://{shop_domain}/admin/apps/{settings.shopify_api_key}",
        status_code=302,
    )
    # Plant the session cookie NOW (this response is served from the app
    # domain, so the cookie sticks). Otherwise the first embedded load
    # depends entirely on signed launch params and can bounce into an
    # OAuth chain inside the iframe, which Shopify refuses to frame.
    resp.set_cookie(
        "sr_session",
        security.sign_session_cookie(shop_domain, settings),
        max_age=86400, httponly=True, secure=True, samesite="none",
    )
    return resp
