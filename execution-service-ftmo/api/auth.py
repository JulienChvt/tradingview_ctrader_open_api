"""Shared-secret validation for the /webhook endpoint.

Rejects any request not authenticated with WEBHOOK_SECRET, carried either
as the X-Webhook-Secret header or a ?secret= query param (fallback for
webhook configs that can't set a custom header).
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from config import settings
from logging_setup import get_logger
from notify.telegram import notify_error

logger = get_logger(__name__)


async def verify_shared_secret(request: Request) -> None:
    provided = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret")
    if not provided or not hmac.compare_digest(provided, settings.webhook_secret):
        logger.warning("Rejected /webhook request: invalid or missing shared secret")
        await notify_error(
            error_type="Authentication failure",
            detail="/webhook received a request with an invalid or missing shared secret",
            action_needed="verify this is expected (misconfiguration) — possible intrusion attempt",
        )
        raise HTTPException(status_code=401, detail="Invalid or missing shared secret")
