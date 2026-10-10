"""Security unit tests: HMAC verification, token encryption, OAuth state."""
import base64
import hashlib
import hmac
import time
import urllib.parse

from cryptography.fernet import Fernet

from app import security
from app.config import get_settings


def _settings():
    return get_settings()


def test_webhook_hmac_roundtrip():
    settings = _settings()
    body = b'{"id":123}'
    digest = hmac.new(settings.shopify_api_secret.encode(), body, hashlib.sha256).digest()
    header = base64.b64encode(digest).decode()
    assert security.verify_webhook_hmac(body, header, settings.shopify_api_secret) is True


def test_webhook_hmac_rejects_tampered_body():
    settings = _settings()
    body = b'{"id":123}'
    digest = hmac.new(settings.shopify_api_secret.encode(), body, hashlib.sha256).digest()
    header = base64.b64encode(digest).decode()
    assert security.verify_webhook_hmac(b'{"id":124}', header, settings.shopify_api_secret) is False
    assert security.verify_webhook_hmac(body, "bogus", settings.shopify_api_secret) is False


def test_oauth_hmac_verification():
    settings = _settings()
    params = {"shop": "test.myshopify.com", "code": "abc", "timestamp": "123"}
    encoded = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac.new(settings.shopify_api_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    params["hmac"] = digest
    assert security.verify_oauth_hmac(params, settings.shopify_api_secret) is True
    params["code"] = "tampered"
    assert security.verify_oauth_hmac(params, settings.shopify_api_secret) is False


def test_token_encrypt_decrypt_roundtrip():
    settings = _settings()
    token = "shpat_super_secret_token"
    enc = security.encrypt_token(token, settings)
    assert enc != token.encode()
    assert security.decrypt_token(enc, settings) == token


def test_token_encryption_is_nondeterministic():
    settings = _settings()
    assert security.encrypt_token("x", settings) != security.encrypt_token("x", settings)


def test_oauth_state_roundtrip():
    settings = _settings()
    state = security.new_oauth_state(settings)
    assert security.verify_oauth_state(state, settings) is True


def test_oauth_state_rejects_tampering():
    settings = _settings()
    state = security.new_oauth_state(settings)
    tampered = state[:-2] + ("aa" if not state.endswith("aa") else "bb")
    assert security.verify_oauth_state(tampered, settings) is False
    assert security.verify_oauth_state("not-a-state", settings) is False


def test_oauth_state_rejects_expired():
    settings = _settings()
    raw = f"{int(time.time()) - 7200}:{'00' * 16}".encode()
    sig = hmac.new(settings.shopify_api_secret.encode(), raw, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{raw.decode()}:{sig}".encode()).decode()
    assert security.verify_oauth_state(token, settings) is False


def test_oauth_state_binds_to_secret():
    settings = _settings()
    state = security.new_oauth_state(settings)
    other = settings.model_copy(update={"shopify_api_secret": "different-secret"})
    assert security.verify_oauth_state(state, other) is False
