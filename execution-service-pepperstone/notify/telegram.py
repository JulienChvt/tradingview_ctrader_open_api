"""Telegram notifications: required on trade-opened and on error (mirrors the
IBKR version's section 4.8 requirements).

A Telegram send failure must never crash or block trade execution — every
call here catches and logs instead of raising.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import settings
from logging_setup import get_logger

logger = get_logger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")


async def send_telegram_message(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured, skipping notification")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


def _paris_now() -> str:
    return datetime.now(PARIS_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


async def notify_trade_opened(
    *,
    symbol: str,
    direction: str,
    lot: float,
    entry_reference_price: float,
    stop_loss_price: float,
    sl_ticks: int,
    take_profit_price: float,
    tp_ticks: int,
) -> None:
    emoji = "🟢" if direction.upper() == "BUY" else "🔴"
    text = (
        f"{emoji} TRADE OPENED\n"
        f"Symbol: {symbol}\n"
        f"Direction: {direction.upper()}\n"
        f"Lot: {lot}\n"
        f"Entry (ref price): {entry_reference_price:.2f}\n"
        f"Stop-loss: {stop_loss_price:.2f} ({sl_ticks} ticks)\n"
        f"Take-profit: {take_profit_price:.2f} ({tp_ticks} ticks)\n"
        f"Time: {_paris_now()}"
    )
    await send_telegram_message(text)


async def notify_error(
    *,
    error_type: str,
    detail: str,
    symbol: str | None = None,
    action_needed: str | None = None,
) -> None:
    lines = [f"🔴 ERROR", f"Type: {error_type}", f"Detail: {detail}"]
    if symbol:
        lines.append(f"Symbol: {symbol}")
    lines.append(f"Time: {_paris_now()}")
    if action_needed:
        lines.append(f"Action needed: {action_needed}")
    await send_telegram_message("\n".join(lines))
