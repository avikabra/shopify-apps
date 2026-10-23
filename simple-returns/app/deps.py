"""Shared FastAPI dependencies: DB sessions, adapter singletons, auth."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from . import security
from .config import Settings, get_settings
from .db import SessionLocal
from .easypost.client import EasyPostClient
from .easypost.mock import MockShippingProvider
from .email.console import ConsoleEmailProvider
from .email.smtp import SMTPEmailProvider
from .models import Shop


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> Settings:
    return get_settings()


@lru_cache
def _shopify_client():
    from .shopify.client import ShopifyGraphQLClient
    from .shopify.mock import MockShopifyClient

    if get_settings().use_mock_shopify:
        return MockShopifyClient()
    return ShopifyGraphQLClient()


@lru_cache
def _shipping_provider():
    settings = get_settings()
    if settings.use_mock_shipping:
        return MockShippingProvider()
    return EasyPostClient()


@lru_cache
def _email_provider():
    settings = get_settings()
    if settings.use_mock_email:
        return ConsoleEmailProvider()
    return SMTPEmailProvider()


def shopify_client():
    return _shopify_client()


def shipping_provider():
    return _shipping_provider()


def email_provider():
    return _email_provider()


# -- auth -----------------------------------------------------------------

def verify_session_token(token: str, settings: Settings) -> dict:
    """Verify a Shopify App Bridge session token (JWT HS256, secret=API secret).

    Implemented with stdlib only (no PyJWT dependency).
    """
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise HTTPException(401, "Malformed session token") from exc
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(settings.shopify_api_secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        actual = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception as exc:
        raise HTTPException(401, "Malformed session token") from exc
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(401, "Invalid session token signature")
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    except Exception as exc:
        raise HTTPException(401, "Malformed session token payload") from exc
    now = time.time()
    if payload.get("exp", 0) < now - 30:
        raise HTTPException(401, "Session token expired")
    if payload.get("aud") != settings.shopify_api_key:
        raise HTTPException(401, "Session token audience mismatch")
    if not payload.get("dest"):
        raise HTTPException(401, "Session token missing dest")
    return payload




def _bounce_or_401(request: Request, shop_domain: str) -> AuthBounce | HTTPException:
    """API calls get a plain 401; page loads get the exit-iframe bounce."""
    if request.url.path.startswith("/api/"):
        return HTTPException(401, "Missing or invalid session")
    return AuthBounce(shop_domain, request.query_params.get("host"))

class AuthBounce(Exception):
    """No verifiable session on an embedded page load.

    Raised instead of a blind 302 to /auth: a server-side redirect chain
    that starts inside the Shopify admin iframe ends on Shopify's own
    OAuth page (which refuses to be framed) or loops through the OAuth
    callback. main.py turns this into an exit-iframe page that restarts
    OAuth at the top level.
    """

    def __init__(self, shop_domain: str, host: str | None = None):
        self.shop_domain = shop_domain
        self.host = host
        super().__init__(f"Auth bounce for {shop_domain}")


def get_shop_domain_from_request(
    request: Request,
    response: Response,
    settings: Settings,
) -> str:
    """Resolve the shop for embedded admin routes.

    Production auth chain:
    1. App Bridge session token (Authorization: Bearer ...) — used by
       fetch/XHR calls once the page has booted App Bridge.
    2. Signed session cookie — issued on the initial HMAC-verified load so
       plain link navigation inside the iframe keeps working.
    3. HMAC-signed query params — Shopify's initial embedded-app load
       (shop/host/hmac/...). Verified, then a session cookie is issued.
    Dev/mock: `?shop=` query param (enabled only when mock shopify mode).
    """
    from .security import (
        sign_session_cookie,
        verify_oauth_hmac,
        verify_session_cookie,
    )

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = verify_session_token(auth[7:], settings)
        dest = payload["dest"]  # https://{shop}/
        return dest.replace("https://", "").rstrip("/")
    # id_token query param: Shopify passes a signed session-token JWT on the
    # initial embedded-app load (managed install flow). Verify it and issue
    # a session cookie for subsequent iframe navigation.
    id_token = request.query_params.get("id_token")
    if id_token:
        try:
            payload = verify_session_token(id_token, settings)
            dest = payload["dest"]
            shop_domain = dest.replace("https://", "").rstrip("/")
            response.set_cookie(
                "sr_session",
                sign_session_cookie(shop_domain, settings),
                max_age=86400,
                httponly=True,
                secure=True,
                samesite="none",
            )
            return shop_domain
        except HTTPException:
            pass
    cookie = request.cookies.get("sr_session")
    if cookie:
        domain = verify_session_cookie(cookie, settings)
        if domain:
            return domain
    params = dict(request.query_params)
    shop = params.get("shop")
    if (
        shop
        and params.get("hmac")
        and settings.shopify_api_secret
        and verify_oauth_hmac(params, settings.shopify_api_secret)
    ):
        response.set_cookie(
            "sr_session",
            sign_session_cookie(shop, settings),
            max_age=86400,
            httponly=True,
            secure=True,
            samesite="none",
        )
        return shop
    if settings.use_mock_shopify:
        shop = request.query_params.get("shop")
        if shop:
            return shop
    # No valid auth. Try to extract the shop domain (unverified) so we can
    # initiate OAuth. This handles Shopify's managed install flow where the
    # initial load has no session yet.
    shop_domain = request.query_params.get("shop")
    if not shop_domain and id_token:
        try:
            payload_b64 = id_token.split(".")[1]
            payload = json.loads(
                base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
            )
            dest = payload.get("dest", "")
            shop_domain = dest.replace("https://", "").rstrip("/")
        except Exception:
            pass
    if shop_domain:
        # Redirect to OAuth to establish a session. FastAPI turns this
        # 302 HTTPException into a redirect response.
        raise _bounce_or_401(request, shop_domain)
    raise HTTPException(401, "Missing or invalid session")


def get_current_shop(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Shop:
    domain = get_shop_domain_from_request(request, response, settings)
    shop = db.query(Shop).filter_by(shop_domain=domain).one_or_none()
    if not shop or shop.uninstalled_at:
        # Shop hasn't completed OAuth yet — redirect to install flow.
        raise _bounce_or_401(request, domain)
    return shop
