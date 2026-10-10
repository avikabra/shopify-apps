"""Pytest fixtures: env, isolated DB, mock adapters, test client."""
import os

# Must be set before any app module is imported.
os.environ.setdefault("SHOPIFY_CLIENT_MODE", "mock")
os.environ.setdefault("SHIPPING_PROVIDER", "mock")
os.environ.setdefault("EMAIL_PROVIDER", "console")
os.environ.setdefault("APP_URL", "http://testserver")
os.environ.setdefault("SHOPIFY_API_KEY", "test-api-key")
os.environ.setdefault("SHOPIFY_API_SECRET", "test-api-secret")

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app import config  # noqa: E402

config.get_settings.cache_clear()


@pytest.fixture()
def db():
    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def shop(db):
    from app.models import Plan, ReturnRule, Shop

    s = Shop(
        shop_domain="test.myshopify.com",
        access_token_enc=b"mock",
        plan=Plan.STARTER,
        settings={
            "brand_name": "Test Store",
            "support_email": "support@test.example",
            "warehouse_address": {
                "street1": "1 Test Way",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "country": "US",
            },
        },
    )
    db.add(s)
    db.flush()
    db.add(
        ReturnRule(
            shop_id=s.id,
            name="Default 30-day window",
            enabled=True,
            priority=0,
            window_days=30,
            auto_approve_reasons=[],
            excluded_product_ids=[],
            excluded_product_tags=["final-sale"],
            max_quantity=10,
        )
    )
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def mock_shopify():
    from app.shopify.mock import MockShopifyClient

    return MockShopifyClient()


@pytest.fixture()
def mock_shipping():
    from app.easypost.mock import MockShippingProvider

    return MockShippingProvider()


@pytest.fixture()
def mock_email():
    from app.email.console import ConsoleEmailProvider

    return ConsoleEmailProvider()


@pytest.fixture()
def service(db, shop, mock_shopify, mock_shipping, mock_email):
    from app.services import ReturnService

    return ReturnService(
        db=db,
        shopify=mock_shopify,
        shipping=mock_shipping,
        email=mock_email,
        app_url="http://testserver",
    )


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import create_app

    application = create_app()

    def override_db():
        yield db

    application.dependency_overrides[get_db] = override_db
    return TestClient(application, raise_server_exceptions=False)
