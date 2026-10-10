"""Shared FastAPI dependencies: DB sessions, adapter singletons, auth."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
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


def get_shop_domain_from_request(request: Request, settings: Settings) -> str:
    """Resolve the shop for embedded admin routes.

    Production: App Bridge session token (Authorization: Bearer ...).
    Dev/mock: `?shop=` query param (enabled only when mock shopify mode).
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = verify_session_token(auth[7:], settings)
        dest = payload["dest"]  # https://{shop}/
        return dest.replace("https://", "").rstrip("/")
    if settings.use_mock_shopify:
        shop = request.query_params.get("shop")
        if shop:
            return shop
    raise HTTPException(401, "Missing or invalid session")


def get_current_shop(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Shop:
    domain = get_shop_domain_from_request(request, settings)
    shop = db.query(Shop).filter_by(shop_domain=domain).one_or_none()
    if not shop or shop.uninstalled_at:
        raise HTTPException(401, "Shop not installed")
    return shop
