"""Trading service entrypoint.

Persistent process: connects to IBKR, then serves the /webhook FastAPI app
that TradingView alerts hit directly (typically via a tunnel, since this
runs locally rather than behind a public Cloud Function). Designed to run
alongside TWS/IB Gateway on the same machine — this is not a one-shot
script.
"""
from __future__ import annotations

import asyncio

import uvicorn

from config import settings
from ibkr.client import IBKRClient
from logging_setup import configure_logging, get_logger
from api.server import app

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    ibkr_client = IBKRClient()
    logger.info("Starting trading service — connecting to IBKR...")
    await ibkr_client.connect()

    app.state.ibkr_client = ibkr_client

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
        await ibkr_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
