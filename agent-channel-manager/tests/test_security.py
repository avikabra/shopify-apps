"""Tests for HMAC verification, session cookies, and OAuth state."""
import hashlib
import hmac
import time

from app import security


class _S:
    shopify_api_secret = "test-api-secret"
    encryption_key = ""


def test_webhook_hmac_valid():
    body = b'{"id":1}'
    digest = hmac.new(b"test-api-secret", body, hashlib.sha256).digest()
    import base64
    header = base64.b64encode(digest).decode()
    assert security.verify_webhook_hmac(body, header, "test-api-secret")


def test_webhook_hmac_invalid():
    assert not security.verify_webhook_hmac(b"{}", "bogus", "test-api-secret")
    assert not security.verify_webhook_hmac(b"{}", None, "test-api-secret")


def test_oauth_hmac_valid():
    params = {"shop": "x.myshopify.com", "timestamp": "123"}
    msg = "shop=x.myshopify.com&timestamp=123"
    params["hmac"] = hmac.new(b"test-api-secret", msg.encode(), hashlib.sha256).hexdigest()
    assert security.verify_oauth_hmac(params, "test-api-secret")


def test_oauth_hmac_rejects_tampered():
    params = {"shop": "evil.myshopify.com", "hmac": "deadbeef"}
    assert not security.verify_oauth_hmac(params, "test-api-secret")


def test_session_cookie_roundtrip():
    s = _S()
    s.shopify_api_secret = "test-api-secret"
    cookie = security.sign_session_cookie("test.myshopify.com", s)
    assert security.verify_session_cookie(cookie, s) == "test.myshopify.com"


def test_session_cookie_rejects_forgery():
    s = _S()
    s.shopify_api_secret = "test-api-secret"
    assert security.verify_session_cookie("forged", s) is None


def test_oauth_state_roundtrip():
    s = _S()
    s.shopify_api_secret = "test-api-secret"
    state = security.new_oauth_state(s)
    assert security.verify_oauth_state(state, s)


def test_oauth_state_rejects_tampered():
    s = _S()
    s.shopify_api_secret = "test-api-secret"
    assert not security.verify_oauth_state("bogus", s)


def test_token_encrypt_roundtrip():
    from cryptography.fernet import Fernet

    s = _S()
    s.encryption_key = Fernet.generate_key().decode()
    enc = security.encrypt_token("shpat_abc123", s)
    assert security.decrypt_token(enc, s) == "shpat_abc123"
