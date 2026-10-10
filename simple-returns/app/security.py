"""Security helpers.

- Webhook HMAC: Shopify signs the raw request body with HMAC-SHA256 using the
  app's client secret; the base64 digest must match X-Shopify-Hmac-Sha256.
- OAuth HMAC: Shopify signs the sorted callback query params (minus hmac and
  signature) the same way, hex-encoded.
- Token encryption: shop access tokens are Fernet-encrypted before storage.
- OAuth `state`: HMAC-signed timestamp token (CSRF protection), 10-min TTL.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from cryptography.fernet import Fernet, InvalidToken

_STATE_TTL_SECONDS = 600


def verify_webhook_hmac(raw_body: bytes, hmac_header: str | None, secret: str) -> bool:
    if not hmac_header or not secret:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, hmac_header)


def verify_oauth_hmac(params: dict, secret: str) -> bool:
    received = params.get("hmac")
    if not received or not secret:
        return False
    message = "&".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac", "signature")
    )
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, received)


def _fernet(settings) -> Fernet:
    key = settings.encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set; cannot encrypt/decrypt tokens.")
    return Fernet(key.encode())


def encrypt_token(token: str, settings) -> bytes:
    """Encrypt an access token for storage (returns bytes for LargeBinary)."""
    return _fernet(settings).encrypt(token.encode())


def decrypt_token(enc: bytes, settings) -> str:
    try:
        return _fernet(settings).decrypt(bytes(enc)).decode()
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt stored token.") from exc


def new_oauth_state(settings) -> str:
    """Create a signed, timestamped OAuth state token."""
    raw = f"{int(time.time())}:{base64.urlsafe_b64encode(os.urandom(16)).decode()}"
    sig = hmac.new(settings.shopify_api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode()


def verify_oauth_state(state: str, settings) -> bool:
    """Return True iff the state token is authentic and fresh."""
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        ts, nonce, sig = decoded.rsplit(":", 2)
        raw = f"{ts}:{nonce}"
    except Exception:
        return False
    expected = hmac.new(
        settings.shopify_api_secret.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        age = time.time() - int(ts)
    except ValueError:
        return False
    return 0 <= age <= _STATE_TTL_SECONDS
