"""High-level cTrader Open API client: connection lifecycle, app/account auth,
symbol resolution, a live streaming spot-price cache, and request/response
correlation via clientMsgId.

Unlike IBKR's local TWS/Gateway, cTrader Open API connects directly to the
broker's cloud host — there is no local terminal application to install or
keep running. Reconnection here only has to handle real network drops.
"""
from __future__ import annotations

import asyncio
import uuid

from ctrader_open_api.endpoints import EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountsTokenInvalidatedEvent,
    ProtoOAApplicationAuthReq,
    ProtoOAErrorRes,
    ProtoOAExecutionEvent,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAReconcileReq,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolByIdReq,
    ProtoOASymbolsListReq,
)

from config import settings
from ctrader.protocol import CTraderProtocol
from logging_setup import get_logger
from notify.telegram import notify_error

logger = get_logger(__name__)

RECONNECT_BACKOFF = [1, 2, 5, 10, 30, 60]
PRICE_SCALE = 100_000  # cTrader ticks/spots are expressed in 1/100000 of a price unit


class CTraderClient:
    def __init__(self) -> None:
        host = (
            EndPoints.PROTOBUF_LIVE_HOST
            if settings.ctrader_environment == "live"
            else EndPoints.PROTOBUF_DEMO_HOST
        )
        self.protocol = CTraderProtocol(
            host, EndPoints.PROTOBUF_PORT, self._on_message, self._on_disconnected
        )
        self.account_id: int | None = None
        self.symbol_id: int | None = None
        self.symbol_details = None  # full ProtoOASymbol (digits, lotSize, minVolume, ...)
        self.latest_bid: float | None = None
        self.latest_ask: float | None = None

        self._pending: dict[str, asyncio.Future] = {}
        self._execution_waiters: dict[str, asyncio.Queue] = {}
        self._ready = asyncio.Event()
        self._stopping = False
        self._reconnect_task: asyncio.Task | None = None

    # -- connection lifecycle -------------------------------------------------

    async def connect(self) -> None:
        self._stopping = False
        await self.protocol.connect()
        await self._authenticate_and_setup()

    async def _authenticate_and_setup(self) -> None:
        await self._app_auth()
        accounts = await self._get_account_list()
        self.account_id = self._pick_account(accounts)
        await self._account_auth()
        await self._resolve_symbol()
        await self._subscribe_spots()
        self._ready.set()
        logger.info("cTrader client ready", extra={"extra_fields": {
            "account_id": self.account_id, "symbol_id": self.symbol_id,
        }})

    def is_connected(self) -> bool:
        return self._ready.is_set() and not self._stopping

    async def _on_disconnected(self) -> None:
        if self._stopping:
            return
        self._ready.clear()
        logger.warning("cTrader connection dropped, scheduling reconnect")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while not self._stopping and not self.is_connected():
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            logger.warning(f"cTrader reconnect attempt {attempt + 1} in {delay}s")
            await asyncio.sleep(delay)
            try:
                await self.protocol.connect()
                await self._authenticate_and_setup()
                logger.info("Reconnected to cTrader successfully")
                return
            except Exception as e:
                attempt += 1
                logger.error(f"cTrader reconnect attempt failed: {e}")
                if attempt == 3:
                    await notify_error(
                        error_type="cTrader connection lost",
                        detail=f"Reconnect failing after {attempt} attempts: {e}",
                    )

    async def disconnect(self) -> None:
        self._stopping = True
        await self.protocol.disconnect()

    # -- request/response correlation -----------------------------------------

    async def _send_and_wait(self, message, timeout: float = 10.0):
        client_msg_id = str(uuid.uuid4())
        future = asyncio.get_event_loop().create_future()
        self._pending[client_msg_id] = future
        try:
            await self.protocol.send(message, client_msg_id)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(client_msg_id, None)

    def register_execution_waiter(self) -> tuple[str, asyncio.Queue]:
        """Used by ctrader/orders.py to track every ExecutionEvent for one
        order (accepted, then filled, etc.) — a plain Future isn't enough
        since more than one event can arrive for the same clientMsgId."""
        client_msg_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()
        self._execution_waiters[client_msg_id] = queue
        return client_msg_id, queue

    def unregister_execution_waiter(self, client_msg_id: str) -> None:
        self._execution_waiters.pop(client_msg_id, None)

    async def send(self, message, client_msg_id: str | None = None) -> None:
        await self.protocol.send(message, client_msg_id)

    # -- auth / setup steps -----------------------------------------------------

    async def _app_auth(self) -> None:
        req = ProtoOAApplicationAuthReq(
            clientId=settings.ctrader_client_id,
            clientSecret=settings.ctrader_client_secret,
        )
        await self._send_and_wait(req)
        logger.info("cTrader application authenticated")

    async def _get_account_list(self) -> list:
        req = ProtoOAGetAccountListByAccessTokenReq(accessToken=settings.ctrader_access_token)
        res = await self._send_and_wait(req)
        return list(res.ctidTraderAccount)

    def _pick_account(self, accounts: list) -> int:
        if settings.ctrader_account_id:
            return settings.ctrader_account_id

        wanted_live = settings.ctrader_environment == "live"
        matching = [a.ctidTraderAccountId for a in accounts if a.isLive == wanted_live]
        if not matching:
            raise RuntimeError(
                f"No {'live' if wanted_live else 'demo'} account found for this "
                "access token — set CTRADER_ACCOUNT_ID explicitly in .env"
            )
        if len(matching) > 1:
            logger.warning(
                f"Multiple {'live' if wanted_live else 'demo'} accounts found for this "
                f"token; using the first ({matching[0]}). Set CTRADER_ACCOUNT_ID in .env "
                "to choose explicitly."
            )
        return matching[0]

    async def _account_auth(self) -> None:
        req = ProtoOAAccountAuthReq(
            ctidTraderAccountId=self.account_id,
            accessToken=settings.ctrader_access_token,
        )
        await self._send_and_wait(req)
        logger.info(f"cTrader account authenticated: {self.account_id}")

    async def get_position(self, position_id: int):
        """Fetches the authoritative current state of one open position via
        a reconcile call. Used right after a fill: the ExecutionEvent's
        embedded position snapshot can arrive before relativeStopLoss/
        relativeTakeProfit are reflected in it, even though the broker has
        already applied them — this re-checks against the live account
        state rather than trusting that snapshot."""
        req = ProtoOAReconcileReq(ctidTraderAccountId=self.account_id)
        res = await self._send_and_wait(req)
        return next((p for p in res.position if p.positionId == position_id), None)

    async def _resolve_symbol(self) -> None:
        req = ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id)
        res = await self._send_and_wait(req)
        match = next(
            (s for s in res.symbol if s.symbolName.upper() == settings.symbol_name.upper()),
            None,
        )
        if match is None:
            raise RuntimeError(
                f'Symbol "{settings.symbol_name}" not found in this account\'s symbol list'
            )
        self.symbol_id = match.symbolId

        detail_req = ProtoOASymbolByIdReq(
            ctidTraderAccountId=self.account_id, symbolId=[self.symbol_id]
        )
        detail_res = await self._send_and_wait(detail_req)
        self.symbol_details = detail_res.symbol[0]
        logger.info(
            f"Resolved symbol {settings.symbol_name} -> id={self.symbol_id}",
            extra={"extra_fields": {
                "lot_size": self.symbol_details.lotSize,
                "digits": self.symbol_details.digits,
                "min_volume": self.symbol_details.minVolume,
                "max_volume": self.symbol_details.maxVolume,
                "step_volume": self.symbol_details.stepVolume,
            }},
        )

    async def _subscribe_spots(self) -> None:
        req = ProtoOASubscribeSpotsReq(
            ctidTraderAccountId=self.account_id,
            symbolId=[self.symbol_id],
        )
        # Fire-and-forget: spot ticks arrive as an ongoing stream of
        # ProtoOASpotEvent pushes, not a single Res — this is what lets order
        # placement read a live price with zero extra round-trips.
        await self.protocol.send(req)
        logger.info(f"Subscribed to live spot prices for symbol {self.symbol_id}")

    # -- message dispatch ---------------------------------------------------

    async def _on_message(self, message, client_msg_id: str) -> None:
        if isinstance(message, ProtoOASpotEvent):
            if message.bid:
                self.latest_bid = message.bid / PRICE_SCALE
            if message.ask:
                self.latest_ask = message.ask / PRICE_SCALE
            return

        if isinstance(message, ProtoOAAccountsTokenInvalidatedEvent):
            logger.error(f"cTrader access token invalidated: {message.reason}")
            await notify_error(
                error_type="cTrader authentication invalidated",
                detail=message.reason,
                action_needed="re-run get_access_token.py to refresh credentials",
            )
            return

        if client_msg_id in self._execution_waiters:
            if isinstance(message, (ProtoOAExecutionEvent, ProtoOAErrorRes)):
                await self._execution_waiters[client_msg_id].put(message)
            return

        if client_msg_id and client_msg_id in self._pending:
            future = self._pending[client_msg_id]
            if not future.done():
                if isinstance(message, ProtoOAErrorRes):
                    future.set_exception(RuntimeError(f"{message.errorCode}: {message.description}"))
                else:
                    future.set_result(message)
