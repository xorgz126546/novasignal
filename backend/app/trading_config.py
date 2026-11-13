"""Runtime-configurable trading/risk/indicator settings (stored in DB, editable by admins)."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from .models import AppSetting

KEY = "trading_config"


class IndicatorConfig(BaseModel):
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_min: float = Field(45, ge=0, le=100)  # bullish momentum floor
    rsi_max: float = Field(70, ge=0, le=100)  # avoid overbought entries
    macd_fast: int = Field(12, ge=2, le=100)
    macd_slow: int = Field(26, ge=3, le=200)
    macd_signal: int = Field(9, ge=2, le=100)
    bb_period: int = Field(20, ge=5, le=100)
    bb_std: float = Field(2.0, gt=0, le=5)
    atr_period: int = Field(14, ge=2, le=100)
    adx_min: float = Field(20, ge=0, le=100)  # below => sideways
    ema_periods: list[int] = [9, 21, 50, 200]
    primary_timeframe: str = "1h"
    timeframes: list[str] = ["15m", "1h", "4h", "1d"]

    @model_validator(mode="after")
    def _check(self):
        if self.rsi_min >= self.rsi_max:
            raise ValueError("rsi_min must be < rsi_max")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be < macd_slow")
        return self


class RiskConfig(BaseModel):
    max_signals_per_day: int = Field(3, ge=0, le=10)
    target_move_min_pct: float = Field(3.0, gt=0, le=50)
    target_move_max_pct: float = Field(5.0, gt=0, le=50)
    min_risk_reward: float = Field(2.0, ge=0.5, le=20)
    min_stop_distance_pct: float = Field(0.8, gt=0, le=20)
    max_stop_distance_pct: float = Field(2.5, gt=0, le=20)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=5)
    max_position_pct: float = Field(25.0, gt=0, le=100)
    max_open_user_trades: int = Field(1, ge=1, le=20)
    max_open_signals: int = Field(3, ge=1, le=20)
    fee_rate_pct: float = Field(0.10, ge=0, le=1)  # Binance spot taker, per side
    slippage_pct: float = Field(0.05, ge=0, le=2)  # assumed per side for paper trading
    min_quote_volume_usdt: float = Field(20_000_000, ge=0)
    max_spread_pct: float = Field(0.10, ge=0, le=5)
    min_confidence: float = Field(60, ge=0, le=100)
    min_agent_agreement: int = Field(4, ge=1, le=6)
    entry_validity_hours: float = Field(4, gt=0, le=72)
    signal_horizon_hours: float = Field(48, gt=0, le=24 * 14)
    symbol_cooldown_hours: float = Field(24, ge=0, le=24 * 14)
    shortlist_size: int = Field(12, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self):
        if self.target_move_min_pct > self.target_move_max_pct:
            raise ValueError("target_move_min_pct must be <= target_move_max_pct")
        if self.min_stop_distance_pct >= self.max_stop_distance_pct:
            raise ValueError("min_stop_distance_pct must be < max_stop_distance_pct")
        return self


class TradingConfig(BaseModel):
    paused: bool = False
    indicators: IndicatorConfig = IndicatorConfig()
    risk: RiskConfig = RiskConfig()
    excluded_bases: list[str] = [
        "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "TRY", "BRL", "AEUR", "USDE", "PAXG", "WBTC", "XUSD", "USD1",
    ]


def load_config(db: Session) -> TradingConfig:
    row = db.get(AppSetting, KEY)
    if row is None:
        return TradingConfig()
    return TradingConfig.model_validate(row.value)


def save_config(db: Session, cfg: TradingConfig) -> TradingConfig:
    row = db.get(AppSetting, KEY)
    data = cfg.model_dump()
    if row is None:
        db.add(AppSetting(key=KEY, value=data))
    else:
        row.value = data
    db.commit()
    return cfg
