"""Standalone IBKR paper trading smoke test (section 4.7).

Run this BEFORE wiring up the webhook layer. It:
  1. Connects to paper trading.
  2. Qualifies the front-month contract for the configured FUTURES_SYMBOL.
  3. Places one bracket order with dummy SL/TP a safe distance from market price.
  4. Prints order status transitions until filled or cancelled.

Confirm manually in TWS/Gateway's paper account that the bracket appears
correctly: entry filled, SL and TP both live, linked to the same parent.

Usage:
    cd execution-service
    python tests/test_ibkr_connection.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logging_setup import configure_logging, get_logger  # noqa: E402
from ibkr.client import IBKRClient  # noqa: E402
from ibkr.orders import place_bracket_order  # noqa: E402

configure_logging()
logger = get_logger("test_ibkr_connection")

# Dummy SL/TP distance for this smoke test — deliberately wide so the test
# order isn't likely to fill instantly against a fast-moving market.
TEST_SL_TICKS = 200
TEST_TP_TICKS = 200
TEST_LOT = 1


async def main() -> None:
    client = IBKRClient()
    logger.info("Connecting to IBKR paper trading...")
    await client.connect()

    logger.info(f"Qualified contract: {client.contract}")

    logger.info("Placing test bracket order (BUY, 1 lot, 200-tick SL/TP)...")
    result = await place_bracket_order(
        ib=client.ib,
        contract=client.contract,
        action="BUY",
        quantity=TEST_LOT,
        sl_ticks=TEST_SL_TICKS,
        tp_ticks=TEST_TP_TICKS,
    )
    logger.info(f"Bracket order result: {result}")

    logger.info("Waiting 30s to observe order status transitions (check TWS paper account)...")
    await asyncio.sleep(30)

    logger.info("Open trades:")
    for trade in client.ib.openTrades():
        logger.info(f"  {trade.order.orderType} {trade.order.action} "
                    f"id={trade.order.orderId} status={trade.orderStatus.status}")

    await client.disconnect()
    logger.info("Disconnected. Test complete — verify the bracket in TWS paper account.")


if __name__ == "__main__":
    asyncio.run(main())
