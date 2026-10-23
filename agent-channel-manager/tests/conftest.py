"""Pytest fixtures: env, isolated DB, mock adapters, test client."""
import os

# Must be set before any app module is imported.
os.environ.setdefault("SHOPIFY_CLIENT_MODE", "mock")
os.environ.setdefault("APP_URL", "http://testserver")
os.environ.setdefault("SHOPIFY_API_KEY", "test-api-key")
os.environ.setdefault("SHOPIFY_API_SECRET", "test-api-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

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
    from app.models import Plan, Shop

    s = Shop(
        shop_domain="test.myshopify.com",
        access_token_enc=b"mock",
        plan=Plan.FREE,
        settings={"brand_name": "Test Store"},
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def pro_shop(db):
    from app.models import Plan, Shop

    s = Shop(
        shop_domain="pro.myshopify.com",
        access_token_enc=b"mock",
        plan=Plan.PRO,
        settings={"brand_name": "Pro Store"},
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture(autouse=True)
def _fresh_shopify_client():
    # Avoid lru_cache leaking mock state (created discounts) between tests.
    from app import deps

    deps._shopify_client.cache_clear()
    yield
    deps._shopify_client.cache_clear()


@pytest.fixture()
def mock_shopify():
    from app.shopify.mock import MockShopifyClient

    return MockShopifyClient()


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
