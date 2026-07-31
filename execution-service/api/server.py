"""FastAPI app + /webhook route.

This is the single entrypoint for TradingView alerts: it authenticates,
validates the raw payload, dedupes, and places the IBKR bracket order —
all in one local process. There is no longer a separate public-facing
Cloud Function; this route is what TradingView's alert hits directly
(typically via a tunnel, since a MacBook has no stable public IP).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from api.auth import verify_shared_secret
from api.models import WebhookPayload
from config import settings
from ibkr.orders import place_bracket_order
from logging_setup import get_logger
from notify.telegram import notify_error

logger = get_logger(__name__)

app = FastAPI(title="IBKR Trading Service")


class _Dedup:
    """In-memory idempotency check: rejects a repeat of the same payload
    within DEDUP_WINDOW_SECONDS, guarding against TradingView double-firing
    the same alert or a retried delivery."""

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._seen: dict[str, float] = {}

    def _key(self, payload: WebhookPayload) -> str:
        raw = f"{payload.type}:{payload.lot}:{payload.tp}:{payload.sl}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def is_duplicate(self, payload: WebhookPayload) -> bool:
        now = time.monotonic()
        # prune expired entries
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.window_seconds}
        key = self._key(payload)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


dedup = _Dedup(settings.dedup_window_seconds)


@app.post("/webhook", dependencies=[Depends(verify_shared_secret)])
async def webhook(request: Request, body: dict[str, Any]) -> dict:
    try:
        payload = WebhookPayload.model_validate(body)
    except Exception as e:
        logger.warning(f"Rejected /webhook request: invalid payload: {e}", extra={
            "extra_fields": {"raw_body": body, "outcome": "rejected_invalid"}
        })
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    if dedup.is_duplicate(payload):
        logger.warning("Rejected /webhook request: duplicate within dedup window", extra={
            "extra_fields": {"payload": payload.model_dump(), "outcome": "rejected_duplicate"}
        })
        raise HTTPException(status_code=409, detail="Duplicate alert within dedup window")

    ibkr_client = request.app.state.ibkr_client

    if not ibkr_client.is_connected():
        detail = "IBKR is disconnected — cannot place order"
        logger.error(detail, extra={"extra_fields": {"payload": payload.model_dump()}})
        await notify_error(
            error_type="IBKR connection lost",
            detail=detail,
            action_needed="check TWS/Gateway — trade was NOT placed",
        )
        raise HTTPException(status_code=503, detail=detail)

    logger.info("Accepted /webhook request", extra={
        "extra_fields": {"payload": payload.model_dump(), "outcome": "accepted"}
    })

    try:
        result = await place_bracket_order(
            ib=ibkr_client.ib,
            contract=ibkr_client.contract,
            action=payload.type,
            quantity=payload.lot,
            sl_ticks=payload.sl,
            tp_ticks=payload.tp,
        )
    except Exception as e:
        logger.error(f"Order placement failed: {e}", extra={
            "extra_fields": {"payload": payload.model_dump(), "outcome": "order_failed"}
        })
        raise HTTPException(status_code=502, detail=f"Order placement failed: {e}")

    logger.info("Order placed successfully", extra={
        "extra_fields": {"payload": payload.model_dump(), "result": result, "outcome": "order_placed"}
    })
    return {"status": "accepted", **result}


@app.get("/health")
async def health(request: Request) -> dict:
    ibkr_client = getattr(request.app.state, "ibkr_client", None)
    return {"ibkr_connected": bool(ibkr_client and ibkr_client.is_connected())}
