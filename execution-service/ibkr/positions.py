"""Position inspection helpers.

Not wired to any API route yet — this module exists so a future flatten/close
action (see section 5.3 point 6 of the project spec) can be added without
restructuring the ibkr package. v1 only reads position state; it does not
place closing orders.
"""
from __future__ import annotations

from ib_async import IB, Future

from logging_setup import get_logger

logger = get_logger(__name__)


def get_open_position(ib: IB, contract: Future) -> float:
    """Returns the net position size for `contract` (positive=long, negative=short, 0=flat)."""
    for pos in ib.positions():
        if pos.contract.conId == contract.conId:
            return pos.position
    return 0.0


def has_active_stop_order(ib: IB, contract: Future) -> bool:
    """Checks whether a live (non-cancelled, non-filled) stop order exists for `contract`.

    Used to confirm a position is protected — see the safety check in
    ibkr/orders.py that runs after every bracket placement.
    """
    live_statuses = {"PendingSubmit", "PreSubmitted", "Submitted"}
    for trade in ib.openTrades():
        if (
            trade.contract.conId == contract.conId
            and trade.order.orderType == "STP"
            and trade.orderStatus.status in live_statuses
        ):
            return True
    return False
