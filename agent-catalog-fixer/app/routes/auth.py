"""OAuth install flow."""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import security
from ..config import Settings, get_settings
from ..deps import get_db, get_settings_dep
from ..models import Plan, Shop

router = APIRouter()


def _shop_url(shop_domain: str) -> str:
    return f"https://{shop_domain}"


@router.get("/auth")
def auth_start(
    request: Request,
    shop: str,
    settings: Settings = Depends(get_settings_dep),
):
    shop_domain = shop.strip().lower()
    if not shop_domain.endswith(".myshopify.com"):
        raise HTTPException(400, "Invalid shop domain.")
    # Mock/dev: skip Shopify entirely.
    if settings.use_mock_shopify:
        return RedirectResponse(f"/auth/callback?shop={shop_domain}&mock=1", status_code=302)
    state = security.new_oauth_state(settings)
    params = {
        "client_id": settings.shopify_api_key,
        "scope": ",".join(settings.oauth_scopes),
        "redirect_uri": f"{settings.app_url}/auth/callback",
        "state": state,
    }
    url = f"{_shop_url(shop_domain)}/admin/oauth/authorize?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.get("/auth/callback")
def auth_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    params = dict(request.query_params)
    shop_domain = (params.get("shop") or "").strip().lower()

    refresh_token = None
    expires_in = None
    if settings.use_mock_shopify and params.get("mock") == "1":
        access_token = "mock-access-token"
    else:
        if not security.verify_oauth_hmac(params, settings.shopify_api_secret):
            raise HTTPException(401, "Invalid OAuth HMAC")
        if not security.verify_oauth_state(params.get("state", ""), settings):
            raise HTTPException(401, "Invalid OAuth state")
        code = params.get("code")
        if not code or not shop_domain:
            raise HTTPException(400, "Missing OAuth code or shop.")
        # Token exchange (legacy install flow fallback).
        resp = httpx.post(
            f"{_shop_url(shop_domain)}/admin/oauth/access_token",
            data={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret,
                "code": code,
                # Shopify rejects non-expiring offline tokens for the
                # Admin API; request an expiring token + refresh token.
                "expiring": "1",
            },
            timeout=30,
        )
        resp.raise_for_status()
        token_payload = resp.json()
        access_token = token_payload["access_token"]
        refresh_token = token_payload.get("refresh_token")
        expires_in = token_payload.get("expires_in")

    try:
        token_enc = security.encrypt_token(access_token, settings)
    except RuntimeError:
        # Dev without ENCRYPTION_KEY: store plaintext marker (mock mode only).
        token_enc = b"mock"

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in
        else None
    )
    refresh_token_enc = None
    if refresh_token:
        try:
            refresh_token_enc = security.encrypt_token(refresh_token, settings)
        except RuntimeError:
            refresh_token_enc = None

    shop = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()
    if shop is None:
        shop = Shop(
            shop_domain=shop_domain,
            access_token_enc=token_enc,
            refresh_token_enc=refresh_token_enc,
            access_token_expires_at=expires_at,
            plan=Plan(settings.resolved_default_plan),
            settings={},
        )
        db.add(shop)
    else:
        shop.access_token_enc = token_enc
        shop.refresh_token_enc = refresh_token_enc
        shop.access_token_expires_at = expires_at
        shop.uninstalled_at = None
    db.commit()

    # Register webhooks (best-effort; skipped in mock mode).
    if not settings.use_mock_shopify:
        _register_webhooks(shop_domain, access_token, settings)

    resp = RedirectResponse(f"/app?shop={shop_domain}", status_code=302)
    resp.set_cookie(
        "acf_session",
        security.sign_session_cookie(shop_domain, settings),
        max_age=86400, httponly=True, secure=True, samesite="none",
    )
    return resp


def _register_webhooks(shop_domain: str, access_token: str, settings: Settings) -> None:
    query = """
    mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
        userErrors { field message }
      }
    }
    """
    url = f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
    for topic in settings.webhook_topics:
        try:
            httpx.post(url, headers=headers, json={
                "query": query,
                "variables": {
                    "topic": topic,
                    "webhookSubscription": {
                        "callbackUrl": f"{settings.app_url}/webhooks",
                        "format": "JSON",
                    },
                },
            }, timeout=15)
        except Exception:
            continue
