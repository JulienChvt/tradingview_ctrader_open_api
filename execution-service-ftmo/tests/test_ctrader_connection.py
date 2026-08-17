"""Standalone cTrader Open API demo-account smoke test — the FTMO
equivalent of the IBKR version's test_ibkr_connection.py.

Run this BEFORE wiring up the webhook layer. It:
  1. Connects and authenticates (app + account) against your demo account.
  2. Resolves the configured symbol (default XAUUSD) and subscribes to spots.
  3. Places one market order with a wide dummy SL/TP.
  4. Prints execution status transitions and the resulting position's
     protection (SL/TP prices) once filled.

Confirm manually in the cTrader web/desktop platform that the position
appears correctly with both a stop-loss and take-profit attached.

Usage:
    cd execution-service-ftmo
    python tests/test_ctrader_connection.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logging_setup import configure_logging, get_logger  # noqa: E402
from ctrader.client import CTraderClient  # noqa: E402
from ctrader.orders import place_market_order_with_protection  # noqa: E402

configure_logging()
logger = get_logger("test_ctrader_connection")

# Dummy SL/TP distance for this smoke test — deliberately wide so the test
# order isn't likely to hit either level instantly against a fast-moving market.
TEST_SL_TICKS = 2000
TEST_TP_TICKS = 2000
TEST_LOTS = 0.01


async def main() -> None:
    client = CTraderClient()
    logger.info("Connecting to cTrader (demo)...")
    await client.connect()

    logger.info(f"Resolved symbol id={client.symbol_id}, account={client.account_id}")

    logger.info("Waiting for a live spot price...")
    for _ in range(50):  # up to ~5s
        if client.latest_bid and client.latest_ask:
            break
        await asyncio.sleep(0.1)
    logger.info(f"Live spot: bid={client.latest_bid} ask={client.latest_ask}")

    logger.info(f"Placing test market order (BUY, {TEST_LOTS} lots, {TEST_SL_TICKS}-tick SL/TP)...")
    result = await place_market_order_with_protection(
        client=client,
        action="buy",
        lots=TEST_LOTS,
        sl_ticks=TEST_SL_TICKS,
        tp_ticks=TEST_TP_TICKS,
    )
    logger.info(f"Order result: {result}")

    await client.disconnect()
    logger.info("Disconnected. Test complete — verify the position in cTrader.")


if __name__ == "__main__":
    asyncio.run(main())
