"""Local control-panel API for the trading system: start/stop each broker
service and its ngrok tunnel, monitor connection status, and surface the
exact webhook URL + JSON payload to paste into a TradingView alert.

Binds to 127.0.0.1 only — this is a control plane that can start/stop
processes, so it must never be reachable from outside this machine, and
must never be put behind a tunnel itself.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import process_manager as pm

app = FastAPI(title="Trading System Manager")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {
        name: {
            "label": pm.SERVICES[name]["label"],
            "service": pm.service_status(name),
            "tunnel": pm.tunnel_status(name),
        }
        for name in pm.SERVICES
    }


@app.post("/api/service/{name}/start")
async def start_service(name: str) -> dict:
    _validate_name(name)
    return pm.start_service(name)


@app.post("/api/service/{name}/stop")
async def stop_service(name: str) -> dict:
    _validate_name(name)
    return pm.stop_service(name)


@app.post("/api/tunnel/{name}/start")
async def start_tunnel(name: str) -> dict:
    _validate_name(name)
    return pm.start_tunnel(name)


@app.post("/api/tunnel/{name}/stop")
async def stop_tunnel(name: str) -> dict:
    _validate_name(name)
    return pm.stop_tunnel(name)


@app.post("/api/stop-all")
async def stop_all() -> dict:
    return pm.stop_everything()


@app.get("/api/webhook-info/{name}")
async def webhook_info(name: str) -> dict:
    _validate_name(name)
    env = dotenv_values(pm.SERVICES[name]["dir"] / ".env")
    secret = env.get("WEBHOOK_SECRET", "")
    tunnel = pm.tunnel_status(name)
    base_url = tunnel["public_url"]

    if name == "pepperstone":
        json_example = {"type": "buy", "lot": 0.5, "tp": 600, "sl": 300}
    else:
        default_lot = env.get("DEFAULT_LOT", "5")
        default_tp = env.get("DEFAULT_TP_TICKS", "40")
        default_sl = env.get("DEFAULT_SL_TICKS", "40")
        json_example = {
            "type": "buy",
            "lot": _coerce_number(default_lot),
            "tp": _coerce_number(default_tp),
            "sl": _coerce_number(default_sl),
        }

    return {
        "tunnel_running": tunnel["running"],
        "webhook_url": f"{base_url}/webhook" if base_url else None,
        "header_name": "X-Webhook-Secret",
        "header_value": secret,
        "query_param_fallback": f"{base_url}/webhook?secret={secret}" if base_url else None,
        "json_example": json_example,
    }


class AccountIdUpdate(BaseModel):
    account_id: str


@app.get("/api/service/{name}/account-id")
async def get_account_id(name: str) -> dict:
    _validate_name(name)
    return {"account_id": pm.get_env_setting(name, "CTRADER_ACCOUNT_ID")}


@app.post("/api/service/{name}/account-id")
async def set_account_id(name: str, body: AccountIdUpdate) -> dict:
    _validate_name(name)
    value = body.account_id.strip()
    if value and not value.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Account ID must be a numeric ctidTraderAccountId, or blank to auto-pick",
        )
    pm.set_env_setting(name, "CTRADER_ACCOUNT_ID", value)
    return {"account_id": value, "restart_required": pm.service_status(name)["running"]}


def _coerce_number(value: str):
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _validate_name(name: str) -> None:
    if name not in pm.SERVICES:
        raise HTTPException(status_code=404, detail=f"Unknown service: {name}")


if __name__ == "__main__":
    import uvicorn

    # 127.0.0.1 only, deliberately — see module docstring.
    uvicorn.run(app, host="127.0.0.1", port=9000)
