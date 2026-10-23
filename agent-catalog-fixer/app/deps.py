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

from .config import Settings, get_settings
from .db import SessionLocal
from .models import Shop
from .security import cookie_name, sign_session_cookie, verify_oauth_hmac, verify_session_cookie


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> Settings:
    return get_settings()


def _shopify_client_for(shop: Shop):
    """Build a Shopify client bound to a specific shop's access token."""
    from . import tokens
    from .shopify.client import ShopifyGraphQLClient
    from .shopify.mock import MockShopifyClient

    settings = get_settings()
    if settings.use_mock_shopify:
        return MockShopifyClient()
    token = tokens.fresh_access_token(shop, settings)
    return ShopifyGraphQLClient(shop.shop_domain, token, settings.shopify_api_version)


# A process-wide mock singleton so dev-mode writes persist across requests.
_mock_singleton: dict = {}


def shopify_client_for(shop: Shop):
    settings = get_settings()
    if settings.use_mock_shopify:
        key = shop.shop_domain
        if key not in _mock_singleton:
            from .shopify.mock import MockShopifyClient

            _mock_singleton[key] = MockShopifyClient()
        return _mock_singleton[key]
    return _shopify_client_for(shop)


# -- auth -----------------------------------------------------------------

def verify_session_token(token: str, settings: Settings) -> dict:
    """Verify a Shopify App Bridge session token (JWT HS256, secret=API secret)."""
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

    1. App Bridge session token (Authorization: Bearer ...)
    2. id_token query param (initial embedded load) -> session cookie issued
    3. Signed session cookie
    4. HMAC-signed query params -> session cookie issued
    Dev/mock: `?shop=` query param.
    No valid auth -> 302 redirect to /auth?shop=...
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = verify_session_token(auth[7:], settings)
        return payload["dest"].replace("https://", "").rstrip("/")

    id_token = request.query_params.get("id_token")
    if id_token:
        try:
            payload = verify_session_token(id_token, settings)
            shop_domain = payload["dest"].replace("https://", "").rstrip("/")
            response.set_cookie(
                cookie_name(), sign_session_cookie(shop_domain, settings),
                max_age=86400, httponly=True, secure=True, samesite="none",
            )
            return shop_domain
        except HTTPException:
            pass

    cookie = request.cookies.get(cookie_name())
    if cookie:
        domain = verify_session_cookie(cookie, settings)
        if domain:
            return domain

    params = dict(request.query_params)
    shop = params.get("shop")
    if (
        shop and params.get("hmac") and settings.shopify_api_secret
        and verify_oauth_hmac(params, settings.shopify_api_secret)
    ):
        response.set_cookie(
            cookie_name(), sign_session_cookie(shop, settings),
            max_age=86400, httponly=True, secure=True, samesite="none",
        )
        return shop

    if settings.use_mock_shopify:
        shop = request.query_params.get("shop")
        if shop:
            return shop

    shop_domain = request.query_params.get("shop")
    if not shop_domain and id_token:
        try:
            payload_b64 = id_token.split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
            shop_domain = payload.get("dest", "").replace("https://", "").rstrip("/")
        except Exception:
            pass
    if shop_domain:
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
        raise _bounce_or_401(request, domain)
    return shop
