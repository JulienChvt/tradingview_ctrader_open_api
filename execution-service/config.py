"""Loads and validates configuration from .env for the execution service."""
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
    # IBKR connection
    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int
    confirm_live: bool

    # Instrument
    futures_symbol: str
    futures_exchange: str
    futures_currency: str
    tick_size: float
    futures_contract_month: str | None
    front_month_min_days_out: int

    # Order defaults
    default_lot: int
    default_tp_ticks: int
    default_sl_ticks: int

    # Webhook + API (this service is now the public-facing webhook receiver too)
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
    settings = Settings(
        ibkr_host=os.getenv("IBKR_HOST", "127.0.0.1"),
        ibkr_port=_env_int("IBKR_PORT", 7497),
        ibkr_client_id=_env_int("IBKR_CLIENT_ID", 1),
        confirm_live=_env_bool("CONFIRM_LIVE", False),
        futures_symbol=os.getenv("FUTURES_SYMBOL", "MGC"),
        futures_exchange=os.getenv("FUTURES_EXCHANGE", "COMEX"),
        futures_currency=os.getenv("FUTURES_CURRENCY", "USD"),
        tick_size=_env_float("TICK_SIZE", 0.10),
        futures_contract_month=os.getenv("FUTURES_CONTRACT_MONTH") or None,
        front_month_min_days_out=_env_int("FRONT_MONTH_MIN_DAYS_OUT", 30),
        default_lot=_env_int("DEFAULT_LOT", 5),
        default_tp_ticks=_env_int("DEFAULT_TP_TICKS", 40),
        default_sl_ticks=_env_int("DEFAULT_SL_TICKS", 40),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        execution_service_host=os.getenv("EXECUTION_SERVICE_HOST", "0.0.0.0"),
        execution_service_port=_env_int("EXECUTION_SERVICE_PORT", 8000),
        dedup_window_seconds=_env_int("DEDUP_WINDOW_SECONDS", 5),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_file_path=os.getenv("LOG_FILE_PATH", "./logs/trading_system.log"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )

    # Live ports are 7496 (TWS) / 4001 (Gateway). Paper ports are 7497 / 4002.
    # Refuse to connect to a live-looking port unless explicitly confirmed.
    live_ports = (7496, 4001)
    if settings.ibkr_port in live_ports and not settings.confirm_live:
        raise RuntimeError(
            f"IBKR_PORT={settings.ibkr_port} looks like a LIVE trading port, but "
            "CONFIRM_LIVE is not set to true. Refusing to start. Set CONFIRM_LIVE=true "
            "in .env only if you deliberately intend to trade live."
        )

    if not settings.webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET must be set in .env")

    return settings


settings = load_settings()
