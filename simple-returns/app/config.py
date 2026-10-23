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
    shipping_provider: str = "mock"  # "real" | "mock"  (EasyPost)
    email_provider: str = "console"  # "console" | "smtp"

    # Database
    database_url: str = "sqlite:///./dev.db"

    # Default plan for new installs
    default_plan: str = "free"  # "free" | "starter" | "growth"

    # Shopify Billing API: create subscriptions in test mode (required on
    # dev stores; set false in production).
    shopify_billing_test: bool = True

    # Security
    encryption_key: str = ""

    # EasyPost
    easypost_api_key: str = ""
    easypost_carrier_accounts: str = ""  # comma-separated, optional

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = "Simple Returns <returns@example.com>"

    port: int = 8000

    @field_validator("shopify_client_mode", "shipping_provider", "email_provider")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @property
    def use_mock_shopify(self) -> bool:
        # Explicit mock mode, or no API key configured -> mock.
        return self.shopify_client_mode == "mock" or not self.shopify_api_key

    @property
    def use_mock_shipping(self) -> bool:
        return self.shipping_provider == "mock" or not self.easypost_api_key

    @property
    def use_mock_email(self) -> bool:
        return self.email_provider == "console" or not self.smtp_host

    @property
    def resolved_default_plan(self) -> str:
        return self.default_plan.lower() if self.default_plan else "free"

    @property
    def oauth_scopes(self) -> list[str]:
        # Keep this list minimal but sufficient for the v1 feature set.
        # NOTE: scope strings below are the OAuth scope identifiers. Shopify's
        # docs now render some of these as e.g. "orders" / "returns"; the
        # OAuth request strings remain read_*/write_* (verify on dev store).
        return [
            "read_orders",
            "write_orders",      # refundCreate
            "read_returns",
            "write_returns",     # returnRequest / approve / decline / returnRefund
            "read_customers",    # order lookup by email
            "read_products",      # variant lookup for exchanges
            "read_draft_orders",
            "write_draft_orders",  # exchange draft orders
            "read_gift_cards",
            "write_gift_cards",  # store-credit gift cards
        ]

    @property
    def webhook_topics(self) -> list[str]:
        return [
            "app/uninstalled",
            "app_subscriptions/update",
            "returns/request",
            "returns/approve",
            "returns/decline",
            "returns/cancel",
            "returns/close",
            "refunds/create",
            "customers/data_request",
            "customers/redact",
            "shop/redact",
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
