"""Market order placement with attached relative stop-loss/take-profit.

Unlike IBKR's 3-leg bracket (entry + separate stop order + separate limit
order, requiring careful transmit-order staging to avoid an unprotected
position if something goes wrong mid-placement), cTrader attaches SL/TP
directly to the entry order via relativeStopLoss/relativeTakeProfit — one
request creates a fully protected position atomically. There is no parent/
child order choreography and no "orphaned children" failure mode to guard
against; the one thing still worth verifying is that the resulting position
actually carries the SL/TP we asked for.
"""
from __future__ import annotations

import asyncio

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    BUY,
    MARKET,
    ORDER_ACCEPTED,
    ORDER_CANCEL_REJECTED,
    ORDER_CANCELLED,
    ORDER_EXPIRED,
    ORDER_FILLED,
    ORDER_PARTIAL_FILL,
    ORDER_REJECTED,
    ORDER_REPLACED,
    SELL,
)

from config import settings
from ctrader.client import CTraderClient
from ctrader.positions import is_position_protected
from logging_setup import get_logger
from notify.telegram import notify_error, notify_trade_opened

logger = get_logger(__name__)

PRICE_SCALE = 100_000  # cTrader relative SL/TP and spot prices: 1/100000 of a price unit

TERMINAL_EXECUTION_TYPES = {
    ORDER_FILLED, ORDER_CANCELLED, ORDER_EXPIRED, ORDER_REJECTED, ORDER_CANCEL_REJECTED,
}
REJECTED_EXECUTION_TYPES = {ORDER_CANCELLED, ORDER_EXPIRED, ORDER_REJECTED, ORDER_CANCEL_REJECTED}

_EXECUTION_TYPE_NAMES = {
    ORDER_ACCEPTED: "ORDER_ACCEPTED",
    ORDER_FILLED: "ORDER_FILLED",
    ORDER_REPLACED: "ORDER_REPLACED",
    ORDER_CANCELLED: "ORDER_CANCELLED",
    ORDER_EXPIRED: "ORDER_EXPIRED",
    ORDER_REJECTED: "ORDER_REJECTED",
    ORDER_CANCEL_REJECTED: "ORDER_CANCEL_REJECTED",
    ORDER_PARTIAL_FILL: "ORDER_PARTIAL_FILL",
}


def _execution_type_name(value: int) -> str:
    return _EXECUTION_TYPE_NAMES.get(value, str(value))


def ticks_to_relative(ticks: int, tick_size: float) -> int:
    """Converts a tick distance into cTrader's relative SL/TP unit (1/100000
    of a price unit). The broker computes the absolute SL/TP price itself
    from the actual fill price, so — unlike IBKR — no reference-price fetch
    is needed before placing the order."""
    return round(ticks * tick_size * PRICE_SCALE)


def lots_to_volume(lots: float, symbol_details) -> int:
    """Converts a lot size into cTrader's volume unit (hundredths of the
    smallest tradable unit), rounded down to the nearest valid step and
    clamped to the symbol's min/max volume."""
    raw = round(lots * symbol_details.lotSize)
    step = symbol_details.stepVolume or 1
    adjusted = (raw // step) * step
    adjusted = max(symbol_details.minVolume, min(adjusted, symbol_details.maxVolume))
    return int(adjusted)


async def place_market_order_with_protection(
    client: CTraderClient,
    action: str,
    lots: float,
    sl_ticks: int,
    tp_ticks: int,
) -> dict:
    """Places a single MARKET order with relative SL/TP attached, and waits
    on the resulting ExecutionEvent stream until a terminal state is
    reached, logging every transition. Raises RuntimeError if the order
    doesn't end up filled.
    """
    action = action.upper()
    trade_side = BUY if action == "BUY" else SELL

    volume = lots_to_volume(lots, client.symbol_details)
    relative_sl = ticks_to_relative(sl_ticks, settings.tick_size)
    relative_tp = ticks_to_relative(tp_ticks, settings.tick_size)

    client_msg_id, queue = client.register_execution_waiter()

    req = ProtoOANewOrderReq(
        ctidTraderAccountId=client.account_id,
        symbolId=client.symbol_id,
        orderType=MARKET,
        tradeSide=trade_side,
        volume=volume,
        relativeStopLoss=relative_sl,
        relativeTakeProfit=relative_tp,
    )

    logger.info("Placing market order", extra={"extra_fields": {
        "action": action, "lots": lots, "volume": volume,
        "sl_ticks": sl_ticks, "tp_ticks": tp_ticks,
    }})

    final_event = None
    try:
        await client.send(req, client_msg_id)

        while True:
            event = await asyncio.wait_for(queue.get(), timeout=15)
            exec_type_name = _execution_type_name(event.executionType)
            logger.info(f"Order status transition: entry -> {exec_type_name}", extra={
                "extra_fields": {"execution_type": exec_type_name}
            })
            if event.executionType in TERMINAL_EXECUTION_TYPES:
                final_event = event
                break
    finally:
        client.unregister_execution_waiter(client_msg_id)

    if final_event is None or final_event.executionType in REJECTED_EXECUTION_TYPES:
        status_name = _execution_type_name(final_event.executionType) if final_event else "no response"
        detail = f"Entry order not filled — final status: {status_name}"
        logger.error(detail)
        await notify_error(
            error_type="Order rejected",
            detail=detail,
            symbol=settings.symbol_name,
            action_needed="check the cTrader account — verify no unprotected position exists",
        )
        raise RuntimeError(detail)

    position = final_event.position

    if not is_position_protected(position):
        detail = (
            f"Position {position.positionId} filled but is missing stop-loss "
            f"and/or take-profit (stopLoss={position.stopLoss}, takeProfit={position.takeProfit})"
        )
        logger.error(detail)
        await notify_error(
            error_type="Unprotected position",
            detail=detail,
            symbol=settings.symbol_name,
            action_needed="check the cTrader account immediately — position may be unprotected",
        )
        # Still return normally — the position exists and the alert has been
        # sent; raising here would just duplicate the notification, and the
        # entry itself is a real fact that already happened.

    await notify_trade_opened(
        symbol=settings.symbol_name,
        direction=action,
        lot=lots,
        entry_reference_price=position.price,
        stop_loss_price=position.stopLoss,
        sl_ticks=sl_ticks,
        take_profit_price=position.takeProfit,
        tp_ticks=tp_ticks,
    )

    return {
        "position_id": position.positionId,
        "entry_price": position.price,
        "stop_loss_price": position.stopLoss,
        "take_profit_price": position.takeProfit,
        "volume": volume,
    }
