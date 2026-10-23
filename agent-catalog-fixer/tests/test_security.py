"""Tests for security helpers."""
import base64
import hashlib
import hmac
import time

import pytest

from app import security
from app.config import get_settings


@pytest.fixture()
def settings():
    return get_settings()


def test_verify_webhook_hmac(settings):
    body = b'{"id":1}'
    digest = hmac.new(settings.shopify_api_secret.encode(), body, hashlib.sha256).digest()
    good = base64.b64encode(digest).decode()
    assert security.verify_webhook_hmac(body, good, settings.shopify_api_secret)
    assert not security.verify_webhook_hmac(body, "bad", settings.shopify_api_secret)
    assert not security.verify_webhook_hmac(body, good, "")
    assert not security.verify_webhook_hmac(body, None, settings.shopify_api_secret)


def test_verify_oauth_hmac(settings):
    params = {"shop": "test.myshopify.com", "timestamp": "123"}
    msg = "shop=test.myshopify.com&timestamp=123"
    sig = hmac.new(settings.shopify_api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    assert security.verify_oauth_hmac({**params, "hmac": sig}, settings.shopify_api_secret)
    assert not security.verify_oauth_hmac({**params, "hmac": "bad"}, settings.shopify_api_secret)


def test_oauth_state_roundtrip(settings):
    state = security.new_oauth_state(settings)
    assert security.verify_oauth_state(state, settings)
    assert not security.verify_oauth_state("garbage", settings)


def test_oauth_state_tampered(settings):
    state = security.new_oauth_state(settings)
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    tampered = base64.urlsafe_b64encode((decoded + "x").encode()).decode()
    assert not security.verify_oauth_state(tampered, settings)


def test_session_cookie_roundtrip(settings):
    cookie = security.sign_session_cookie("test.myshopify.com", settings)
    assert security.verify_session_cookie(cookie, settings) == "test.myshopify.com"
    assert security.verify_session_cookie("garbage", settings) is None


def test_token_encrypt_roundtrip(settings):
    enc = security.encrypt_token("shpat_abc123", settings)
    assert security.decrypt_token(enc, settings) == "shpat_abc123"


def test_verify_session_token_rejects_bad_sig(settings):
    with pytest.raises(Exception):
        from fastapi import HTTPException

        from app.deps import verify_session_token

        verify_session_token("a.b.c", settings)
