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

    # Client mode
    shopify_client_mode: str = "mock"  # "real" | "mock"

    # OAuth scopes needed: read products (catalog), write discounts
    # (agent-only promotions). Note: we do NOT request write_products —
    # visibility is enforced at the app's agent-channel layer (Catalog API /
    # publication scoping happens via the agent sales channel, not by mutating
    # the merchant's catalog).
    oauth_scopes: str = "read_products,read_product_listings,write_discounts,read_discounts"

    # Database
    database_url: str = "sqlite:///./dev.db"

    # Default plan for new installs
    default_plan: str = "free"  # "free" | "pro"

    # Shopify Billing API: test (non-charge) subscriptions
    shopify_billing_test: bool = True

    # Security
    encryption_key: str = ""

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
        return self.default_plan if self.default_plan in ("free", "pro") else "free"


@lru_cache
def get_settings() -> Settings:
    return Settings()
