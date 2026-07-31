"""Pydantic model for the raw TradingView webhook payload — identical
contract to the IBKR version ({"type","lot","tp","sl"}). `lot` is a float
here rather than an int: cTrader/CFD position sizes are routinely fractional
(e.g. 0.01 lots), unlike IBKR futures contracts.

This is the only validation layer — it must fully enforce the hard-stop
rule: a missing or invalid `type` field must reject the request before any
order is placed.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from config import settings


class WebhookPayload(BaseModel):
    type: str
    lot: float = Field(default_factory=lambda: settings.default_lot, gt=0)
    tp: int = Field(default_factory=lambda: settings.default_tp_ticks, gt=0)
    sl: int = Field(default_factory=lambda: settings.default_sl_ticks, gt=0)

    @field_validator("type")
    @classmethod
    def normalize_and_check_type(cls, value: str) -> str:
        normalized = value.strip().lower() if isinstance(value, str) else value
        if normalized not in ("buy", "sell"):
            raise ValueError(f'"type" must be "buy" or "sell" (case-insensitive), got {value!r}')
        return normalized
