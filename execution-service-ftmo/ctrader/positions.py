"""Position inspection helpers.

Not wired to any API route yet — this module exists so a future flatten/close
action can be added without restructuring the ctrader package, mirroring the
IBKR version's ibkr/positions.py. v1 only reads position state; it does not
place closing orders.

Note: unlike IBKR, a cTrader position's stop-loss/take-profit are attributes
of the position itself (set via relativeStopLoss/relativeTakeProfit on the
entry order) rather than separate resting orders — so "is this position
protected" is just checking ProtoOAPosition.stopLoss / .takeProfit are set,
no separate order lookup required.
"""
from __future__ import annotations

from logging_setup import get_logger

logger = get_logger(__name__)


def is_position_protected(position) -> bool:
    """Checks whether a ProtoOAPosition (as received on an ExecutionEvent)
    has both a stop-loss and take-profit attached."""
    return bool(position.stopLoss) and bool(position.takeProfit)
