"""Environment-based configuration. Secrets come ONLY from environment variables / .env.

Never hard-code credentials in source code. See ../.env.example for every variable.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "CRYPTONEX AI"
    environment: str = Field("development", description="development | test | production")
    secret_key: str = Field("dev-insecure-change-me", description="JWT signing key; MUST be overridden in production")
    access_token_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:5173"
    public_base_url: str = "http://localhost:5173"  # dashboard URL used in Telegram messages

    # --- Database (single source of truth: PostgreSQL in production, SQLite for dev/tests) ---
    database_url: str = "sqlite:///./cryptonex.db"

    # --- Binance (PUBLIC market data only; no keys needed, no trading, no withdrawals) ---
    binance_base_url: str = "https://data-api.binance.vision"
    binance_timeout_s: float = 10.0
    binance_max_weight_per_minute: int = 1000  # stay well under Binance's 6000/min IP limit

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""  # sent by Telegram in X-Telegram-Bot-Api-Secret-Token
    telegram_api_base: str = "https://api.telegram.org"

    # --- Optional providers ---
    cryptopanic_api_key: str = ""  # news (Agent 07). Empty => agent reports NO_DATA, never invents news
    anthropic_api_key: str = ""  # optional LLM narrative for the Chief agent
    anthropic_model: str = "claude-opus-5-5"

    # --- Scheduler ---
    enable_scheduler: bool = False
    scan_interval_minutes: int = 15
    tracker_interval_seconds: int = 60
    daily_summary_hour_utc: int = 21

    # --- Billing (disabled until payments/legal/reliability are verified) ---
    billing_enabled: bool = False
    subscription_price_usd_cents: int = 2500

    # --- Bootstrap admin (only used if no admin exists) ---
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    @field_validator("secret_key")
    @classmethod
    def _secret_len(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("SECRET_KEY must be at least 16 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.secret_key.startswith("dev-insecure"):
        raise RuntimeError("Refusing to start in production with the default SECRET_KEY")
    return s
