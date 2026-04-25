"""Runtime config loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Config:
    finnhub_key: str = os.getenv("FINNHUB_API_KEY", "")
    discord_webhook: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    polygon_key: str = os.getenv("POLYGON_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "Trading Bot contact@example.com")

    price_min: float = _f("SCAN_PRICE_MIN", 3.0)
    price_max: float = _f("SCAN_PRICE_MAX", 20.0)
    max_float: int = _i("SCAN_MAX_FLOAT", 30_000_000)
    min_gap_pct: float = _f("SCAN_MIN_GAP_PCT", 10.0)
    min_premarket_volume: int = _i("SCAN_MIN_PREMARKET_VOLUME", 50_000)
    min_relative_volume: float = _f("SCAN_MIN_RELATIVE_VOLUME", 2.0)

    # Lane controls
    enable_long_lane: bool = os.getenv("ENABLE_LONG_LANE", "true").lower() == "true"
    enable_short_lane: bool = os.getenv("ENABLE_SHORT_LANE", "true").lower() == "true"

    # Long lane requires either bullish news score or no bearish news
    long_min_bullish_score: float = _f("LONG_MIN_BULLISH_SCORE", 10.0)

    # Short lane filters
    short_min_gap_pct: float = _f("SHORT_MIN_GAP_PCT", 30.0)            # only fade big gappers
    short_min_bearish_score: float = _f("SHORT_MIN_BEARISH_SCORE", 15.0) # OR has dilution/bad news
    short_parabolic_extension_pct: float = _f("SHORT_PARABOLIC_EXTENSION_PCT", 60.0)

    account_equity: float = _f("ACCOUNT_EQUITY", 800.0)
    max_risk_per_trade_pct: float = _f("MAX_RISK_PER_TRADE_PCT", 2.0)
    max_position_size_pct: float = _f("MAX_POSITION_SIZE_PCT", 25.0)

    # Daily loss circuit breaker (% of equity). Sizing locks once breached.
    daily_loss_limit_pct: float = _f("DAILY_LOSS_LIMIT_PCT", 6.0)
    consecutive_loss_cooldown_minutes: int = _i("CONSECUTIVE_LOSS_COOLDOWN_MINUTES", 30)

    # Trading window in NY local time (HH:MM, 24-hour). Outside this window
    # the scanner still ticks (and updates the live tile if enabled) but
    # new-message Discord notifications are suppressed.
    trading_window_start: str = os.getenv("TRADING_WINDOW_START", "04:00")
    trading_window_end: str = os.getenv("TRADING_WINDOW_END", "10:00")

    # Live status tile — single persistent Discord message edited in-place
    # each scan. Always-current snapshot of TOP PICK + ranked candidates.
    # Doesn't trigger notification sounds. New-message alerts still fire
    # for actionable events (new HIGH conviction, TOP PICK change, ORB).
    enable_live_tile: bool = os.getenv("ENABLE_LIVE_TILE", "true").lower() == "true"

    # Optional dedicated webhook for the live tile. When set, the tile
    # posts/edits in this channel only — keeping it permanently visible
    # without competing with notification messages. Recommended setup:
    #   #premarket-status (live tile webhook here)
    #   #premarket-alerts (DISCORD_WEBHOOK_URL here)
    # Falls back to DISCORD_WEBHOOK_URL if empty.
    live_tile_webhook: str = os.getenv("LIVE_TILE_WEBHOOK_URL", "")

    # Standalone local dashboard (Flask app) — same data as the live tile,
    # rendered in a browser window. Runs on http://127.0.0.1:DASHBOARD_PORT.
    dashboard_port: int = _i("DASHBOARD_PORT", 8765)

    # Session top-pick: % delta a new candidate must beat by to dethrone the
    # current session leader. Prevents gold marker flicking around.
    session_top_pick_delta_pct: float = _f("SESSION_TOP_PICK_DELTA_PCT", 5.0)

    # Auto-scheduler intervals
    auto_ibkr_import_minutes: int = _i("AUTO_IBKR_IMPORT_MINUTES", 10)
    auto_daily_review_after_hhmm: str = os.getenv("AUTO_DAILY_REVIEW_AFTER", "16:30")
    auto_backtest_weekday: int = _i("AUTO_BACKTEST_WEEKDAY", 6)  # Sunday = 6
    auto_backtest_after_hhmm: str = os.getenv("AUTO_BACKTEST_AFTER", "18:00")

    # Conviction tiers — Discord only pings on HIGH by default.
    high_conviction_min_score: float = _f("HIGH_CONVICTION_MIN_SCORE", 60.0)
    medium_conviction_min_score: float = _f("MEDIUM_CONVICTION_MIN_SCORE", 35.0)
    discord_min_conviction: str = os.getenv("DISCORD_MIN_CONVICTION", "high").lower()

    # Float rotation (premarket vol / float). >1x is hot, >5x is parabolic.
    rotation_warn_threshold: float = _f("ROTATION_WARN_THRESHOLD", 1.0)
    rotation_parabolic_threshold: float = _f("ROTATION_PARABOLIC_THRESHOLD", 5.0)

    # Opening Range Breakout window (minutes after the open)
    orb_minutes: int = _i("ORB_MINUTES", 5)

    # Short interest data
    enable_short_interest: bool = os.getenv("ENABLE_SHORT_INTEREST", "true").lower() == "true"

    # IBKR Flex Web Service auto-import
    ibkr_flex_token: str = os.getenv("IBKR_FLEX_TOKEN", "")
    ibkr_flex_query_id: str = os.getenv("IBKR_FLEX_QUERY_ID", "")

    # Polygon for backtest historical bars
    polygon_backtest_key: str = os.getenv("POLYGON_BACKTEST_KEY", "")

    # Re-alert thresholds for tickers already alerted this session.
    realert_price_pct: float = _f("REALERT_PRICE_PCT", 5.0)
    realert_volume_multiple: float = _f("REALERT_VOLUME_MULTIPLE", 2.0)
    realert_cooldown_seconds: int = _i("REALERT_COOLDOWN_SECONDS", 300)

    cache_dir: Path = ROOT / "data_cache"
    db_path: Path = ROOT / "journal.db"


CONFIG = Config()
CONFIG.cache_dir.mkdir(exist_ok=True)
