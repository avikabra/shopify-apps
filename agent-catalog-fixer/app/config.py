"""Application configuration. Everything comes from the environment.

See .env.example for the full list. If SHOPIFY_API_KEY is empty the app runs
in MOCK mode (fixture-backed adapters, no network calls to Shopify).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Shopify
    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    shopify_api_version: str = "2026-07"
    app_url: str = "http://localhost:8000"

    # Client modes
    shopify_client_mode: str = "mock"  # "real" | "mock"

    # Database
    database_url: str = "sqlite:///./dev.db"

    # Default plan for new installs
    default_plan: str = "free"  # "free" | "pro"

    # Security
    encryption_key: str = ""

    # Audit limits
    free_plan_product_limit: int = 25

    # Shopify Billing API: use test charges (always True unless overridden).
    shopify_billing_test: bool = True

    port: int = 8000

    @field_validator("shopify_client_mode")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @property
    def use_mock_shopify(self) -> bool:
        # Explicit mock mode, or no API key configured -> mock.
        return self.shopify_client_mode == "mock" or not self.shopify_api_key

    @property
    def resolved_default_plan(self) -> str:
        return self.default_plan.lower() if self.default_plan else "free"

    @property
    def oauth_scopes(self) -> list[str]:
        # Minimal scopes for catalog read + product/variant/metafield writes.
        return [
            "read_products",
            "write_products",  # productUpdate, metafieldsSet, image alt text
        ]

    @property
    def webhook_topics(self) -> list[str]:
        return [
            "app/uninstalled",
            "app_subscriptions/update",
            "products/update",
            "products/create",
            "customers/data_request",
            "customers/redact",
            "shop/redact",
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
