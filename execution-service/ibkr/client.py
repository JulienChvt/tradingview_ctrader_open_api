"""IBKR connection management: connect, reconnect-on-drop with backoff, and
front-month futures contract qualification."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ib_async import IB, Future

from config import settings
from logging_setup import get_logger
from notify.telegram import notify_error

logger = get_logger(__name__)

# Backoff schedule for reconnect attempts (seconds). Holds at the last value.
RECONNECT_BACKOFF = [1, 2, 5, 10, 30, 60]


class IBKRClient:
    """Wraps an ib_async IB() connection with reconnect/backoff handling.

    TWS/Gateway restarts daily and can drop the socket at any time; this
    class detects that via ib_async's disconnectedEvent and keeps retrying
    in the background rather than letting the service go silently offline.
    """

    def __init__(self) -> None:
        self.ib = IB()
        self.contract: Future | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._stopping = False

        self.ib.disconnectedEvent += self._on_disconnected

    async def connect(self) -> None:
        self._stopping = False
        await self._connect_once()
        self.contract = await self.qualify_front_month_contract()

    async def _connect_once(self) -> None:
        logger.info(
            "Connecting to IBKR",
            extra={"extra_fields": {
                "host": settings.ibkr_host,
                "port": settings.ibkr_port,
                "client_id": settings.ibkr_client_id,
            }},
        )
        await self.ib.connectAsync(
            host=settings.ibkr_host,
            port=settings.ibkr_port,
            clientId=settings.ibkr_client_id,
        )
        # Falls back to delayed data when the account has no real-time market
        # data subscription for the instrument (IBKR still serves real-time
        # when entitled — this only changes behavior when it isn't).
        self.ib.reqMarketDataType(3)
        logger.info("Connected to IBKR")

    def _on_disconnected(self) -> None:
        if self._stopping:
            return
        logger.warning("IBKR connection dropped, scheduling reconnect")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while not self._stopping and not self.ib.isConnected():
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            logger.warning(f"Reconnect attempt {attempt + 1} in {delay}s")
            await asyncio.sleep(delay)
            try:
                await self._connect_once()
                self.contract = await self.qualify_front_month_contract()
                logger.info("Reconnected to IBKR successfully")
                return
            except Exception as e:
                attempt += 1
                logger.error(f"Reconnect attempt failed: {e}")
                if attempt == 3:
                    # Only alert after a few failures to avoid spamming Telegram
                    # for a single transient blip.
                    await notify_error(
                        error_type="IBKR connection lost",
                        detail=f"Reconnect failing after {attempt} attempts: {e}",
                    )

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    async def qualify_front_month_contract(self) -> Future:
        """Resolves the current front-month contract for the configured symbol.

        If FUTURES_CONTRACT_MONTH is set in config, that expiry is used
        directly (still qualified against IBKR). Otherwise queries
        reqContractDetails for all available expiries and picks the nearest
        one that (a) hasn't already expired and (b) is more than
        FRONT_MONTH_MIN_DAYS_OUT days from expiry — IBKR rejects new orders
        on physically-settled futures (gold included) inside its
        near-expiry/physical-delivery risk window, well before the actual
        last trading day, so the nearest unexpired contract is often not
        actually tradable.
        """
        if settings.futures_contract_month:
            candidate = Future(
                symbol=settings.futures_symbol,
                exchange=settings.futures_exchange,
                currency=settings.futures_currency,
                lastTradeDateOrContractMonth=settings.futures_contract_month,
            )
            qualified = await self._qualify(candidate)
            logger.info(f"Using configured contract month {settings.futures_contract_month}")
            return qualified

        generic = Future(
            symbol=settings.futures_symbol,
            exchange=settings.futures_exchange,
            currency=settings.futures_currency,
        )
        details = await self.ib.reqContractDetailsAsync(generic)
        if not details:
            raise RuntimeError(
                f"No contract details found for {settings.futures_symbol} on "
                f"{settings.futures_exchange} — cannot resolve front month"
            )

        now = datetime.now(timezone.utc)
        today = now.strftime("%Y%m%d")
        expiries = sorted(
            d.contract.lastTradeDateOrContractMonth for d in details
            if d.contract.lastTradeDateOrContractMonth >= today
        )
        if not expiries:
            raise RuntimeError(
                f"All known expiries for {settings.futures_symbol} are in the "
                "past — cannot resolve front month"
            )

        safe_expiries = [
            e for e in expiries
            if (datetime.strptime(e, "%Y%m%d").replace(tzinfo=timezone.utc) - now).days
            >= settings.front_month_min_days_out
        ]
        if not safe_expiries:
            logger.warning(
                f"No {settings.futures_symbol} expiry is more than "
                f"{settings.front_month_min_days_out} days out — falling back to the "
                "nearest unexpired contract, which IBKR may reject as too close to expiry"
            )
        front_month = safe_expiries[0] if safe_expiries else expiries[0]
        candidate = Future(
            symbol=settings.futures_symbol,
            exchange=settings.futures_exchange,
            currency=settings.futures_currency,
            lastTradeDateOrContractMonth=front_month,
        )
        qualified = await self._qualify(candidate)
        logger.info(f"Resolved front-month contract: {front_month}")
        return qualified

    async def _qualify(self, contract: Future) -> Future:
        try:
            qualified = await self.ib.qualifyContractsAsync(contract)
        except Exception as e:
            await notify_error(
                error_type="Contract qualification failure",
                detail=str(e),
            )
            raise
        if not qualified:
            await notify_error(
                error_type="Contract qualification failure",
                detail=f"IBKR returned no qualified contract for {contract}",
            )
            raise RuntimeError(f"Failed to qualify contract: {contract}")
        return qualified[0]

    async def disconnect(self) -> None:
        self._stopping = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
        self.ib.disconnect()
