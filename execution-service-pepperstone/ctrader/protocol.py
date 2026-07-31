"""Low-level asyncio TCP+TLS transport for cTrader Open API's Protobuf wire
protocol.

Deliberately bypasses the official `ctrader-open-api` package's Twisted-based
Client/TcpProtocol classes: this service already runs on asyncio (FastAPI/
uvicorn), and bridging in a second event-loop system would only add cross-loop
overhead for no benefit. We reuse Spotware's bundled protobuf message
definitions and OAuth helper from that package, but the transport here is a
plain asyncio implementation of the same wire format:

    4-byte big-endian length prefix + that many bytes of a serialized
    ProtoMessage envelope (payloadType, payload bytes, optional clientMsgId).
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import struct
from typing import Awaitable, Callable

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
    ProtoHeartbeatEvent,
    ProtoMessage,
)
from ctrader_open_api.protobuf import Protobuf

from logging_setup import get_logger

logger = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10
LENGTH_PREFIX_FORMAT = ">I"
LENGTH_PREFIX_SIZE = struct.calcsize(LENGTH_PREFIX_FORMAT)

MessageHandler = Callable[[object, str], Awaitable[None]]
DisconnectHandler = Callable[[], Awaitable[None]]


class CTraderProtocol:
    """One TCP+TLS connection to a cTrader Open API host. Framing, heartbeat,
    and message dispatch only — auth flow and reconnect policy live in
    ctrader/client.py."""

    def __init__(
        self,
        host: str,
        port: int,
        on_message: MessageHandler,
        on_disconnected: DisconnectHandler,
    ) -> None:
        self.host = host
        self.port = port
        self._on_message = on_message
        self._on_disconnected = on_disconnected
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._read_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stopping = False

    async def connect(self) -> None:
        self._stopping = False
        ssl_context = ssl.create_default_context()
        logger.info(f"Connecting to cTrader at {self.host}:{self.port}")
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port, ssl=ssl_context
        )

        # Disable Nagle's algorithm: this API exchanges many small messages
        # (heartbeats, order requests, spot ticks) and waiting to coalesce
        # them only adds latency on the order-placement path.
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Connected to cTrader")

    async def send(self, inner_message, client_msg_id: str | None = None) -> None:
        envelope = ProtoMessage(
            payload=inner_message.SerializeToString(),
            payloadType=inner_message.payloadType,
            clientMsgId=client_msg_id,
        )
        data = envelope.SerializeToString()
        frame = struct.pack(LENGTH_PREFIX_FORMAT, len(data)) + data
        async with self._write_lock:
            self._writer.write(frame)
            await self._writer.drain()

    async def _read_loop(self) -> None:
        try:
            while not self._stopping:
                prefix = await self._reader.readexactly(LENGTH_PREFIX_SIZE)
                (length,) = struct.unpack(LENGTH_PREFIX_FORMAT, prefix)
                data = await self._reader.readexactly(length)

                envelope = ProtoMessage()
                envelope.ParseFromString(data)

                if envelope.payloadType == ProtoHeartbeatEvent().payloadType:
                    continue  # server heartbeat — no action needed

                inner = Protobuf.extract(envelope)
                await self._on_message(inner, envelope.clientMsgId)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError) as e:
            if not self._stopping:
                logger.warning(f"cTrader connection lost: {e}")
                await self._on_disconnected()
        except Exception:
            logger.exception("Unexpected error in cTrader read loop")
            if not self._stopping:
                await self._on_disconnected()

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if self._stopping:
                return
            try:
                await self.send(ProtoHeartbeatEvent())
            except Exception as e:
                logger.warning(f"Failed to send heartbeat: {e}")
                return

    async def disconnect(self) -> None:
        self._stopping = True
        for task in (self._read_task, self._heartbeat_task):
            if task:
                task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
