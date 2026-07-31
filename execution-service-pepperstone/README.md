# TradingView → Pepperstone (cTrader Open API) Automated Execution

A second, independent trading backend for the same TradingView webhook contract as `execution-service/` (the IBKR version) — same `/webhook` payload shape, same shared-secret auth, same Telegram notifications — but placing trades on **Pepperstone via the cTrader Open API** instead of Interactive Brokers.

**Both services can run side by side** on different ports; nothing here modifies `execution-service/`.

## How this differs from the IBKR version (and why it's faster)

- **No local terminal application.** IBKR requires TWS/IB Gateway running locally and a socket connection to it. cTrader Open API connects directly over TLS to the broker's cloud host (`demo.ctraderapi.com` or `live.ctraderapi.com`) — there's nothing to install or keep logged in alongside this service.
- **One request per trade, not three.** IBKR bracket orders need a parent (entry) + two child orders (stop, limit), placed and tracked separately, with careful `transmit` ordering to avoid a window where the position is unprotected. cTrader lets you attach `relativeStopLoss`/`relativeTakeProfit` directly to the market order itself — entry and protection are created atomically in a single request.
- **No reference-price fetch before ordering.** The IBKR version fetches a fresh quote before computing absolute SL/TP prices, adding a round-trip to every order. Here, SL/TP are sent as *relative* offsets — the broker computes the absolute levels from the actual fill price itself. A live spot-price subscription still runs in the background (used for logging/Telegram context), but it's off the order-placement critical path.
- **Custom asyncio transport, not the official Twisted-based client.** This service reuses Spotware's official `ctrader-open-api` package only for its bundled protobuf message definitions and OAuth helper — the actual socket/heartbeat/reconnect logic (`ctrader/protocol.py`) is a small native `asyncio` implementation, so it shares one event loop with the FastAPI server instead of bridging two separate event-loop systems. `TCP_NODELAY` is set explicitly to avoid Nagle's-algorithm delay on small, frequent messages.

## 1. One-time setup: register a cTrader Open API application

This step can't be automated — it's tied to your own Pepperstone/cTrader ID login:

1. Go to **https://openapi.ctrader.com** and register a new application. This gives you a **Client ID** and **Client Secret**.
2. In that app's settings, add a redirect URI: `http://localhost:5000/callback` (or set your own and match it in `.env`).
3. Copy `.env.example` to `.env` and fill in `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_REDIRECT_URI`, and a strong `WEBHOOK_SECRET`. Leave `CTRADER_ENVIRONMENT=demo` for now.

## 2. Service setup

```bash
cd execution-service-pepperstone
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2.1 Get an access token (one-time OAuth flow)

```bash
python get_access_token.py
```

This opens your browser to authorize the app against your Pepperstone cTrader account (log in with your normal cTrader ID credentials — this script never sees your password, only the OAuth redirect). It then prints an access token and refresh token to paste into `.env`, plus the `ctidTraderAccountId`(s) linked to that token. Access tokens last ~30 days; the refresh token doesn't expire and can mint new access tokens without repeating this browser flow (re-run `get_access_token.py` if you'd rather just get a fresh access token manually).

### 2.2 Standalone connection test (do this before anything else)

```bash
python tests/test_ctrader_connection.py
```

This connects to your **demo** account, resolves the XAUUSD symbol, places one market order with a wide dummy SL/TP, and prints the resulting position. **Confirm in the cTrader web/desktop platform** that the position has both a stop-loss and take-profit attached before proceeding.

### 2.3 Run the service

```bash
python main.py
```

Connects to cTrader and starts `/webhook` on `EXECUTION_SERVICE_HOST:EXECUTION_SERVICE_PORT` (default `0.0.0.0:8001` — different from the IBKR version's 8000, so both can run at once).

### 2.4 Standalone test of `/webhook`

```bash
curl -X POST http://localhost:8001/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your WEBHOOK_SECRET>" \
  -d '{"type": "buy"}'
```

Confirm this places a real demo trade with default lot/SL/TP. Also try omitting the header (expect `401`) and an invalid `type` (expect `400`, no order placed).

### 2.5 Unit tests

```bash
pytest tests/test_webhook_payloads.py
```

## 3. Exposing `/webhook` to TradingView

Same as the IBKR version — use a tunnel (Cloudflare Tunnel or ngrok) pointed at port 8001, and configure the TradingView alert's webhook URL + `X-Webhook-Secret` header (or `?secret=` query param) accordingly.

## 4. Telegram notifications

Same setup and contract as the IBKR version — same bot/chat works for both services if you want one bot notifying for both, or use a second bot to tell them apart. See `execution-service/README.md` section 4 for the BotFather steps if you haven't done this before.

## 5. Demo → live switch

```
CTRADER_ENVIRONMENT=live
CONFIRM_LIVE=true       # required — the service refuses to start otherwise
```

Then run `get_access_token.py` again against your **live** account (demo and live are entirely separate account universes in cTrader — a demo-account access token will not work against live) and update `.env` with the new tokens. Re-run the full safety checklist below before trusting it with real capital.

## 6. Instrument notes — read before sizing positions

- **Gold here is a CFD (`XAUUSD`), not a COMEX future.** The IBKR version trades `MGC`/`GC` futures contracts; Pepperstone's cTrader offering is a spot gold CFD with entirely different lot economics (1 standard lot = 100 oz). `DEFAULT_LOT` in `.env` defaults to a conservative `0.01` for exactly this reason — don't copy over the IBKR version's `5` without understanding the notional exposure difference.
- `TICK_SIZE` in `.env` is used to convert the webhook's `tp`/`sl` (in ticks) into cTrader's relative SL/TP unit. Confirm it matches your account's actual XAUUSD price precision — both `get_access_token.py`'s account listing and the standalone test log the resolved symbol's `digits`/`lotSize`/volume constraints.

## 7. Safety checklist (verify before any live-money use)

- [ ] `CTRADER_ENVIRONMENT` defaults to `demo`; switching to `live` requires both the environment change **and** `CONFIRM_LIVE=true`.
- [ ] Every filled position is confirmed to carry both a stop-loss and take-profit (`ctrader/orders.py` checks this and alerts if not — but verify it once yourself in the platform too).
- [ ] `/webhook` rejects any request without a valid `WEBHOOK_SECRET`.
- [ ] A missing or invalid `type` field results in no order being placed (tested, not just reviewed).
- [ ] Ticks-to-relative-SL/TP conversion verified correct for both BUY and SELL with a real demo trade.
- [ ] All order placements and outcomes are logged with timestamps.
- [ ] Reconnect-with-backoff logic works after a real network drop, and a stuck/disconnected state is surfaced via log + Telegram.
- [ ] A Telegram message actually arrives (tested live) for: trade opened, rejected order, and cTrader disconnection/token invalidation.

## 8. Architecture notes

- `ctrader/positions.py` is a read-only placeholder today, same as the IBKR version's `ibkr/positions.py` — structured for a future flatten/close action, not implemented in v1.
- Same non-goals as the IBKR version: no risk-based position sizing, no multi-symbol support, no strategy logic in this system, no web dashboard.
