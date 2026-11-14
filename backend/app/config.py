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

    # --- Single-service hosting: serve the built dashboard (frontend/dist) from the API ---
    static_dir: str = ""

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

    # --- CRYPTONEX HQ (agent company) ---
    hq_enabled: bool = False  # master switch for HQ scheduled jobs (API/UI work regardless)
    telegram_chat_id: str = ""  # owner's chat for briefs, reports and critical alerts
    github_token: str = ""  # fine-grained, this repo only (contents + PRs) — Scout reads, Engineer opens draft PRs
    github_repo: str = "xorgz126546/novasignal"
    hq_llm_daily_budget_usd: float = 1.0
    hq_model_routine: str = "claude-haiku-5-5"
    hq_model_senior: str = "claude-opus-5-5"  # Manager + Engineer
    hq_timezone: str = "Africa/Algiers"
    hq_brief_hour: int = 8
    hq_library_weekday: int = 4  # 0=Mon … 4=Fri
    hq_scan_interval_minutes: int = 30

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

    @field_validator("telegram_bot_token", "telegram_webhook_secret", "telegram_chat_id", "github_token")
    @classmethod
    def _strip_secret(cls, v: str) -> str:
        # Values pasted from a phone often carry spaces/newlines; a leading "bot" prefix is also a common mistake.
        v = v.strip().strip('"').strip("'").strip()
        return v[3:] if v.lower().startswith("bot") and ":" in v[3:] else v

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Hosts like Render/Heroku hand out postgres:// URLs; SQLAlchemy + psycopg3 needs postgresql+psycopg://
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
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
    import os

    s = Settings()
    # Render/Railway expose the service's public URL; use it when PUBLIC_BASE_URL isn't set explicitly.
    ext = os.environ.get("RENDER_EXTERNAL_URL") or (
        f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}" if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else "")
    if ext and "PUBLIC_BASE_URL" not in os.environ:
        s.public_base_url = ext.rstrip("/")
    if s.is_production and s.secret_key.startswith("dev-insecure"):
        raise RuntimeError("Refusing to start in production with the default SECRET_KEY")
    return s
