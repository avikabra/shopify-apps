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
from ..models import Plan, Shop

router = APIRouter()

_WEBHOOK_TOPICS = ["app/uninstalled", "shop/update"]


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

    refresh_token = None
    expires_in = None
    granted_scopes = ""
    if settings.use_mock_shopify:
        token = "mock-access-token"
    else:
        async with httpx.AsyncClient(timeout=20) as http:
            try:
                resp = await http.post(
                    f"https://{shop_domain}/admin/oauth/access_token",
                    json={
                        "client_id": settings.shopify_api_key,
                        "client_secret": settings.shopify_api_secret,
                        "code": code,
                        # Shopify rejects non-expiring offline tokens for
                        # the Admin API; request an expiring token plus a
                        # refresh token.
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
        db.commit()
        db.refresh(shop)
    else:
        shop.access_token_enc = security.encrypt_token(token, settings)
        shop.refresh_token_enc = refresh_token_enc
        shop.access_token_expires_at = expires_at
        if granted_scopes:
            shop.scopes = granted_scopes
        shop.uninstalled_at = None
        db.commit()

    if not settings.use_mock_shopify:
        await _register_webhooks(shop_domain, token, settings)

    host = params.get("host", "")
    dest = f"/app?shop={shop_domain}" + (f"&host={host}" if host else "")
    resp = RedirectResponse(dest, status_code=302)
    # Plant the session cookie NOW so the post-callback /app load is
    # authenticated immediately. Without it, /app can only restart OAuth,
    # which loops (callback -> /app -> /auth -> callback) because nothing
    # in the round trip ever establishes a session.
    resp.set_cookie(
        "acm_session",
        security.sign_session_cookie(shop_domain, settings),
        max_age=86400, httponly=True, secure=True, samesite="none",
    )
    return resp


async def _register_webhooks(shop_domain: str, token: str, settings: Settings) -> None:
    mutation = """
    mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
        webhookSubscription { id }
        userErrors { field message }
      }
    }
    """
    async with httpx.AsyncClient(timeout=20) as http:
        for topic in _WEBHOOK_TOPICS:
            try:
                await http.post(
                    f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/graphql.json",
                    headers={
                        "X-Shopify-Access-Token": token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": mutation,
                        "variables": {
                            "topic": topic.upper(),
                            "webhookSubscription": {
                                "callbackUrl": f"{settings.app_url}/webhooks/{topic.replace('/', '_')}",
                                "format": "JSON",
                            },
                        },
                    },
                )
            except httpx.HTTPError:
                continue
