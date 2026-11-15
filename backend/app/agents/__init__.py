from .a01_market_scanner import MarketScanner
from .a02_technical_analyst import TechnicalAnalyst
from .a03_trend_detector import TrendDetector
from .a04_volume_intelligence import VolumeIntelligence
from .a05_support_resistance import SupportResistance
from .a06_price_action import PriceActionAnalyst
from .a07_news_sentiment import NewsSentiment
from .a08_risk_manager import RiskManager
from .a09_strategy_validator import StrategyValidator
from .a10_entry_exit import EntryExitEngine
from .a11_chief_trading_officer import ChiefTradingOfficer

ALL_AGENTS = [MarketScanner, TechnicalAnalyst, TrendDetector, VolumeIntelligence, SupportResistance, PriceActionAnalyst,
              NewsSentiment, RiskManager, StrategyValidator, EntryExitEngine, ChiefTradingOfficer]
AGENT_IDS = [a.id for a in ALL_AGENTS]
