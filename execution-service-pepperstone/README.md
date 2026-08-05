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
2. In that app's settings, add a redirect URI: `http://localhost:5000/callback` (or set your own and match it in `.env`). See the port 5000 warning below before picking this.
3. Copy `.env.example` to `.env` and fill in `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_REDIRECT_URI`, and a strong `WEBHOOK_SECRET`. Leave `CTRADER_ENVIRONMENT=demo` for now.

**Pepperstone uses its own cTrader ID portal**, separate from the generic cTrader.com login: your cTrader ID for Pepperstone accounts is created from inside Pepperstone's Secure Client Area (pepperstone.com), and can also be managed at **id-ct.pepperstone.com**. If the OAuth authorize screen in step 2.1 below shows no trading accounts to grant access to, you're very likely logged into the wrong cTrader ID (e.g. one created directly on ctrader.com, or for a different broker) — log out and back in with the cTrader ID tied to your Pepperstone account instead.

**macOS port 5000 conflict:** macOS's Control Center "AirPlay Receiver" feature squats on port 5000 (IPv6 loopback) by default. If your browser shows something like "Access to localhost was denied" right after authorizing, this is why — the OAuth redirect hit AirPlay Receiver instead of `get_access_token.py`'s local callback server. Either disable AirPlay Receiver (System Settings → General → AirDrop & Handoff), or change `CTRADER_REDIRECT_URI` in `.env` to a different port (e.g. `http://localhost:5051/callback`) — if you do the latter, you **must** update the redirect URI registered on the app's settings page at openapi.ctrader.com to match exactly, or the token exchange will fail. Authorization codes are single-use and short-lived (~1 minute), so if one attempt fails, get a fresh code rather than retrying the same URL.

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

- [x] `CTRADER_ENVIRONMENT` defaults to `demo`; switching to `live` requires both the environment change **and** `CONFIRM_LIVE=true`.
- [x] Every filled position is confirmed to carry both a stop-loss and take-profit (`ctrader/orders.py` checks this and alerts if not — verified live 2026-08-05 for both BUY and SELL).
- [x] `/webhook` rejects any request without a valid `WEBHOOK_SECRET`.
- [x] A missing or invalid `type` field results in no order being placed (tested, not just reviewed).
- [x] Ticks-to-relative-SL/TP conversion verified correct for both BUY and SELL with a real demo trade (2026-08-05).
- [x] All order placements and outcomes are logged with timestamps.
- [ ] Reconnect-with-backoff logic works after a real network drop, and a stuck/disconnected state is surfaced via log + Telegram.
- [ ] A Telegram message actually arrives (tested live) for: trade opened, rejected order, and cTrader disconnection/token invalidation. (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` not yet configured for this service — notifications are currently no-ops.)

## 8. Architecture notes

- `ctrader/positions.py` is a read-only placeholder today, same as the IBKR version's `ibkr/positions.py` — structured for a future flatten/close action, not implemented in v1. There is currently no way to close a position through this service; do it from the cTrader platform directly.
- Same non-goals as the IBKR version: no risk-based position sizing, no multi-symbol support, no strategy logic in this system, no web dashboard.

## 9. Known gotchas (found during live verification, 2026-08-05)

- **Order rejections used to be silently swallowed.** Order placement uses fire-and-forget messaging correlated by `clientMsgId` via `_execution_waiters`, but the message dispatcher originally only routed `ProtoOAErrorRes` (broker rejection responses) to a *different* tracking map used by request/response setup calls. A real rejection reason from cTrader was dropped entirely, surfacing only as a blank `"Order placement failed: "` after an opaque 15-second timeout. Fixed: `ctrader/client.py`'s `_on_message` now routes `ProtoOAErrorRes` to `_execution_waiters` too, and `ctrader/orders.py` raises with the real error code/description as soon as it arrives.
- **False "unprotected position" alarms.** cTrader's `ORDER_FILLED` execution event can carry a `position` snapshot that hasn't yet caught up to the stop-loss/take-profit it just applied, momentarily reporting `stopLoss`/`takeProfit` as `0.0` even though the broker already set them correctly. Fixed: before concluding a position is genuinely unprotected, `ctrader/orders.py` now re-confirms via `CTraderClient.get_position()` (a `ProtoOAReconcileReq` call) against live account state.
- **Orders that exceed available margin get *no response at all* from cTrader** — not even a rejection event tied to the request. This hits the same 15-second timeout as above, but there's nothing to catch: the broker never acknowledges an order it can't margin. If `/webhook` orders seem to silently hang, check the requested `lot` size against the account's actual balance/leverage before assuming it's a code bug — a 5-lot XAUUSD order needs roughly $90k in margin at 1:30 leverage, for context.
