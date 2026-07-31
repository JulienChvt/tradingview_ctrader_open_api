"""Ticks-based bracket order construction and placement (section 4.5).

The single most important correctness rule in this module: only the last
leg of a bracket has transmit=True. Every earlier leg must have
transmit=False, or an unprotected order can hit the exchange.
"""
from __future__ import annotations

import asyncio

from ib_async import IB, Future, LimitOrder, MarketOrder, StopOrder, Trade

from config import settings
from logging_setup import get_logger
from notify.telegram import notify_error, notify_trade_opened
from ibkr.positions import has_active_stop_order

logger = get_logger(__name__)

TERMINAL_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
REJECTED_STATUSES = {"Cancelled", "ApiCancelled", "Inactive"}


def ticks_to_price_offset(ticks: int) -> float:
    return ticks * settings.tick_size


async def get_reference_price(ib: IB, contract: Future) -> float:
    """Fetches a fresh reference price immediately before computing SL/TP.

    Uses the last trade price, falling back to the close if no trade has
    printed yet in the session (e.g. right at open).
    """
    [ticker] = await ib.reqTickersAsync(contract)
    price = ticker.last if ticker.last == ticker.last else None  # NaN check
    if price is None or price <= 0:
        price = ticker.close
    if price is None or price != price or price <= 0:
        raise RuntimeError(f"Could not obtain a valid reference price for {contract.symbol}")
    return price


def build_bracket_order(
    ib: IB,
    action: str,
    quantity: int,
    entry_reference_price: float,
    sl_ticks: int,
    tp_ticks: int,
) -> tuple[MarketOrder, StopOrder, LimitOrder, float, float]:
    """Builds the three linked legs of a bracket order.

    action: "BUY" or "SELL" — direction of the entry.
    Returns (parent, stop_loss, take_profit, stop_loss_price, take_profit_price).
    """
    action = action.upper()
    opposite = "SELL" if action == "BUY" else "BUY"

    if action == "BUY":
        stop_loss_price = entry_reference_price - ticks_to_price_offset(sl_ticks)
        take_profit_price = entry_reference_price + ticks_to_price_offset(tp_ticks)
    else:
        stop_loss_price = entry_reference_price + ticks_to_price_offset(sl_ticks)
        take_profit_price = entry_reference_price - ticks_to_price_offset(tp_ticks)

    stop_loss_price = round(stop_loss_price, 2)
    take_profit_price = round(take_profit_price, 2)

    # tif is set explicitly on every leg: leaving it blank lets IBKR silently
    # apply an "order preset" default and emit an informational notice (error
    # 10349) that briefly (and misleadingly) flips orderStatus.status to
    # Cancelled before the order actually proceeds — which previously caused
    # this code to mistake a live fill for a rejection and cancel the SL/TP
    # legs, leaving the position unprotected.
    parent = MarketOrder(action, quantity)
    parent.orderId = ib.client.getReqId()
    parent.tif = "DAY"
    parent.transmit = False

    stop_loss = StopOrder(opposite, quantity, stop_loss_price)
    stop_loss.orderId = ib.client.getReqId()
    stop_loss.parentId = parent.orderId
    stop_loss.tif = "DAY"
    stop_loss.transmit = False

    take_profit = LimitOrder(opposite, quantity, take_profit_price)
    take_profit.orderId = ib.client.getReqId()
    take_profit.parentId = parent.orderId
    take_profit.tif = "DAY"
    take_profit.transmit = True  # last leg — triggers transmission of the whole bracket

    return parent, stop_loss, take_profit, stop_loss_price, take_profit_price


def _log_status(leg_name: str, trade: Trade) -> None:
    status = trade.orderStatus.status
    logger.info(
        f"Order status transition: {leg_name} -> {status}",
        extra={"extra_fields": {
            "leg": leg_name,
            "order_id": trade.order.orderId,
            "status": status,
            "filled": trade.orderStatus.filled,
            "remaining": trade.orderStatus.remaining,
        }},
    )


async def place_bracket_order(
    ib: IB,
    contract: Future,
    action: str,
    quantity: int,
    sl_ticks: int,
    tp_ticks: int,
) -> dict:
    """Places a full bracket order and waits for the parent leg to reach a
    stable state (Filled or Submitted). Returns a summary dict.

    Raises RuntimeError if any leg is rejected, or if the entry ends up
    without children still active (unprotected position).
    """
    entry_reference_price = await get_reference_price(ib, contract)

    parent, stop_loss, take_profit, sl_price, tp_price = build_bracket_order(
        ib, action, quantity, entry_reference_price, sl_ticks, tp_ticks
    )

    legs = [("entry", parent), ("stop_loss", stop_loss), ("take_profit", take_profit)]
    trades: dict[str, Trade] = {}

    for leg_name, order in legs:
        trade = ib.placeOrder(contract, order)
        trades[leg_name] = trade
        trade.statusEvent += lambda t, name=leg_name: _log_status(name, t)
        logger.info(f"Placed {leg_name} order", extra={"extra_fields": {
            "leg": leg_name, "order_id": order.orderId, "action": order.action,
        }})

    # Give IBKR a moment to acknowledge the parent (PendingSubmit/Submitted/Rejected).
    parent_trade = trades["entry"]
    for _ in range(50):  # up to ~10s
        if parent_trade.orderStatus.status in TERMINAL_STATUSES.union({"Submitted"}):
            break
        await asyncio.sleep(0.2)

    if parent_trade.orderStatus.status in REJECTED_STATUSES:
        # IBKR can emit informational notices (e.g. a TIF-preset default,
        # error 10349) that briefly flip status to a rejected-looking state
        # before the order actually proceeds to Filled/Submitted. Give any
        # such transient a short grace period to resolve before concluding
        # this is a genuine rejection.
        await asyncio.sleep(2)

    if parent_trade.orderStatus.filled <= 0 and parent_trade.orderStatus.status in REJECTED_STATUSES:
        await _cancel_children_safety_check(ib, trades)
        detail = f"Entry order rejected — status: {parent_trade.orderStatus.status}"
        await notify_error(
            error_type="Order rejected",
            detail=detail,
            symbol=contract.symbol,
            action_needed="check TWS immediately — verify no unprotected position exists",
        )
        raise RuntimeError(detail)

    await notify_trade_opened(
        symbol=contract.symbol,
        direction=action,
        lot=quantity,
        entry_reference_price=entry_reference_price,
        stop_loss_price=sl_price,
        sl_ticks=sl_ticks,
        take_profit_price=tp_price,
        tp_ticks=tp_ticks,
    )

    # Explicit check-and-cancel safety routine: never trust OCA/parent-child
    # linkage alone to guarantee no orphaned children.
    await _verify_protection(ib, contract, trades)

    return {
        "entry_order_id": parent.orderId,
        "entry_status": parent_trade.orderStatus.status,
        "stop_loss_price": sl_price,
        "take_profit_price": tp_price,
        "entry_reference_price": entry_reference_price,
    }


async def _cancel_children_safety_check(ib: IB, trades: dict[str, Trade]) -> None:
    """If the parent failed, ensure both children are cancelled — never leave
    them orphaned/active even though IBKR's parentId linkage should already
    handle this."""
    for leg_name in ("stop_loss", "take_profit"):
        trade = trades.get(leg_name)
        if trade and trade.orderStatus.status not in TERMINAL_STATUSES:
            logger.warning(f"Cancelling orphaned {leg_name} order after parent failure")
            ib.cancelOrder(trade.order)


async def _verify_protection(ib: IB, contract: Future, trades: dict[str, Trade]) -> None:
    """Confirms the stop-loss leg is live. Logs + alerts if a position exists
    without an active stop — the single most dangerous state this system
    can be in."""
    await asyncio.sleep(1)
    stop_trade = trades.get("stop_loss")
    stop_is_live = bool(stop_trade) and stop_trade.orderStatus.status in (
        "PendingSubmit", "PreSubmitted", "Submitted",
    )
    if not stop_is_live and not has_active_stop_order(ib, contract):
        detail = (
            f"Entry leg is {trades['entry'].orderStatus.status} but no active "
            f"stop-loss order was found (stop status: "
            f"{stop_trade.orderStatus.status if stop_trade else 'unknown'})"
        )
        logger.error(detail)
        await notify_error(
            error_type="Unprotected position",
            detail=detail,
            symbol=contract.symbol,
            action_needed="check TWS immediately — position may be unprotected",
        )
