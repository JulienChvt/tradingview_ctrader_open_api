"""Trading service entrypoint (Pepperstone / cTrader Open API version).

Persistent process: connects to cTrader, then serves the /webhook FastAPI
app that TradingView alerts hit directly (typically via a tunnel). Unlike
IBKR, cTrader Open API connects straight to the broker's cloud host — there
is no local terminal application (TWS/Gateway equivalent) to install or keep
running alongside this process.
"""
from __future__ import annotations

import asyncio

import uvicorn

from api.server import app
from config import settings
from ctrader.client import CTraderClient
from logging_setup import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    ctrader_client = CTraderClient()
    logger.info("Starting trading service — connecting to cTrader...")
    await ctrader_client.connect()

    app.state.ctrader_client = ctrader_client

    config = uvicorn.Config(
        app,
        host=settings.execution_service_host,
        port=settings.execution_service_port,
        log_config=None,  # we manage logging ourselves via logging_setup
    )
    server = uvicorn.Server(config)

    try:
        logger.info(
            f"Webhook listening on "
            f"{settings.execution_service_host}:{settings.execution_service_port}/webhook"
        )
        await server.serve()
    finally:
        await ctrader_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
