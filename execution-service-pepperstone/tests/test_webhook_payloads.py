"""Unit tests for webhook payload validation (api/models.py).

Run with: cd execution-service-pepperstone && pytest tests/test_webhook_payloads.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.models import WebhookPayload  # noqa: E402
from config import settings  # noqa: E402


def test_defaults_applied_when_fields_omitted():
    payload = WebhookPayload.model_validate({"type": "buy"})
    assert payload.lot == settings.default_lot
    assert payload.tp == settings.default_tp_ticks
    assert payload.sl == settings.default_sl_ticks


def test_explicit_values_override_defaults():
    payload = WebhookPayload.model_validate(
        {"type": "sell", "lot": 0.5, "tp": 20, "sl": 15}
    )
    assert payload.lot == 0.5
    assert payload.tp == 20
    assert payload.sl == 15


def test_fractional_lot_accepted():
    # Unlike IBKR contracts, cTrader lots are routinely fractional.
    payload = WebhookPayload.model_validate({"type": "buy", "lot": 0.01})
    assert payload.lot == 0.01


def test_type_is_normalized_case_insensitively():
    assert WebhookPayload.model_validate({"type": "BUY"}).type == "buy"
    assert WebhookPayload.model_validate({"type": "Sell"}).type == "sell"


@pytest.mark.parametrize("bad_type", ["", "long", "short", None, 123])
def test_invalid_type_rejected(bad_type):
    with pytest.raises(ValidationError):
        WebhookPayload.model_validate({"type": bad_type})


def test_missing_type_rejected():
    with pytest.raises(ValidationError):
        WebhookPayload.model_validate({})


@pytest.mark.parametrize("field", ["lot", "tp", "sl"])
def test_non_positive_numeric_fields_rejected(field):
    with pytest.raises(ValidationError):
        WebhookPayload.model_validate({"type": "buy", field: 0})
    with pytest.raises(ValidationError):
        WebhookPayload.model_validate({"type": "buy", field: -5})
