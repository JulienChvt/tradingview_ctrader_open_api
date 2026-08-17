"""Loads and validates configuration from .env for the FTMO (cTrader
Open API) execution service."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # cTrader Open API application (register at https://openapi.ctrader.com)
    ctrader_client_id: str
    ctrader_client_secret: str
    ctrader_redirect_uri: str
    ctrader_environment: str  # "demo" or "live"
    ctrader_access_token: str
    ctrader_refresh_token: str
    ctrader_account_id: int | None  # ctidTraderAccountId; auto-picked if None and only one account
    confirm_live: bool

    # Instrument
    symbol_name: str
    tick_size: float

    # Order defaults
    default_lot: float
    default_tp_ticks: int
    default_sl_ticks: int

    # Webhook + API
    webhook_secret: str
    execution_service_host: str
    execution_service_port: int
    dedup_window_seconds: int

    # Logging
    log_level: str
    log_file_path: str

    # Telegram
    telegram_bot_token: str | None
    telegram_chat_id: str | None


def load_settings() -> Settings:
    account_id_raw = os.getenv("CTRADER_ACCOUNT_ID", "").strip()
    settings = Settings(
        ctrader_client_id=os.getenv("CTRADER_CLIENT_ID", ""),
        ctrader_client_secret=os.getenv("CTRADER_CLIENT_SECRET", ""),
        ctrader_redirect_uri=os.getenv("CTRADER_REDIRECT_URI", "http://localhost:5000/callback"),
        ctrader_environment=os.getenv("CTRADER_ENVIRONMENT", "demo").strip().lower(),
        ctrader_access_token=os.getenv("CTRADER_ACCESS_TOKEN", ""),
        ctrader_refresh_token=os.getenv("CTRADER_REFRESH_TOKEN", ""),
        ctrader_account_id=int(account_id_raw) if account_id_raw else None,
        confirm_live=_env_bool("CONFIRM_LIVE", False),
        symbol_name=os.getenv("SYMBOL_NAME", "XAUUSD"),
        tick_size=_env_float("TICK_SIZE", 0.01),
        default_lot=_env_float("DEFAULT_LOT", 0.01),
        default_tp_ticks=_env_int("DEFAULT_TP_TICKS", 400),
        default_sl_ticks=_env_int("DEFAULT_SL_TICKS", 400),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        execution_service_host=os.getenv("EXECUTION_SERVICE_HOST", "0.0.0.0"),
        execution_service_port=_env_int("EXECUTION_SERVICE_PORT", 8001),
        dedup_window_seconds=_env_int("DEDUP_WINDOW_SECONDS", 5),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_file_path=os.getenv("LOG_FILE_PATH", "./logs/trading_system.log"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )

    if settings.ctrader_environment not in ("demo", "live"):
        raise RuntimeError('CTRADER_ENVIRONMENT must be "demo" or "live"')

    # cTrader's demo/live environments are fully separate account universes —
    # there's no shared port distinguishing them like IBKR's paper/live ports,
    # so this is the equivalent deliberate-confirmation gate before trading live.
    if settings.ctrader_environment == "live" and not settings.confirm_live:
        raise RuntimeError(
            "CTRADER_ENVIRONMENT=live but CONFIRM_LIVE is not set to true. "
            "Refusing to start. Set CONFIRM_LIVE=true in .env only if you "
            "deliberately intend to trade live."
        )

    if not settings.webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET must be set in .env")

    if not settings.ctrader_client_id or not settings.ctrader_client_secret:
        raise RuntimeError(
            "CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET must be set in .env "
            "(register an app at https://openapi.ctrader.com)"
        )

    if not settings.ctrader_access_token or not settings.ctrader_refresh_token:
        raise RuntimeError(
            "CTRADER_ACCESS_TOKEN and CTRADER_REFRESH_TOKEN must be set in .env "
            "— run get_access_token.py once to obtain them"
        )

    return settings


settings = load_settings()
