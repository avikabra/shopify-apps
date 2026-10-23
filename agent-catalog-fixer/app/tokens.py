"""Access-token lifecycle for Shopify Admin API calls.

Shopify no longer accepts non-expiring offline access tokens. Installs
request expiring tokens (see routes/auth.py): a ~1h access token plus a
90-day refresh token that ROTATES on every use. `fresh_access_token` is
the single choke point every API caller goes through: given the shop row
from the caller's own session, it returns the stored token, refreshing
(and persisting the rotated pair) when the stored one is about to lapse.
Shops without a refresh token (mock installs, legacy rows) simply return
their stored token, exactly as before.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from . import security
from .config import Settings
from .db import SessionLocal
from .models import Shop


class TokenError(Exception):
    """Raised when a token refresh fails (distinct from mock fallback)."""


def _refresh(shop: Shop, db, settings: Settings) -> None:
    refresh_token = security.decrypt_token(shop.refresh_token_enc, settings)
    with httpx.Client(timeout=20) as http:
        resp = http.post(
            f"https://{shop.shop_domain}/admin/oauth/access_token",
            json={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    try:
        resp.raise_for_status()
        payload = resp.json()
        new_token = payload["access_token"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise TokenError(f"Could not refresh access token: {exc}") from exc
    shop.access_token_enc = security.encrypt_token(new_token, settings)
    if payload.get("refresh_token"):
        shop.refresh_token_enc = security.encrypt_token(
            payload["refresh_token"], settings
        )
    if payload.get("expires_in"):
        shop.access_token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload["expires_in"])
        )
    db.commit()


def fresh_access_token(shop: Shop, settings: Settings) -> str:
    """Return a usable access token for the shop, refreshing if needed.

    The refresh (when one is due) is persisted through a dedicated
    session so the rotated refresh token is never lost; the token from
    that fresh row is what gets returned.
    """
    if shop.refresh_token_enc and shop.access_token_expires_at is not None:
        expires_at = shop.access_token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at - datetime.now(timezone.utc) < timedelta(minutes=5):
            db = SessionLocal()
            try:
                row = (
                    db.query(Shop)
                    .filter_by(shop_domain=shop.shop_domain)
                    .one_or_none()
                )
                if row is not None:
                    _refresh(row, db, settings)
                    return security.decrypt_token(row.access_token_enc, settings)
            finally:
                db.close()
    return security.decrypt_token(shop.access_token_enc, settings)
