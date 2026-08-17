# TradingView → FTMO (cTrader Open API) Automated Execution

A third, independent trading backend for the same TradingView webhook contract as `execution-service/` (IBKR) and `execution-service-pepperstone/` (Pepperstone) — same `/webhook` payload shape, same shared-secret auth, same Telegram notifications — but placing trades on **FTMO via the cTrader Open API**.

This is functionally a clone of the Pepperstone backend: the cTrader Open API protocol is broker-agnostic (any cTrader-based broker or prop firm is identified purely by the `ctidTraderAccountId` your token has access to), so all the `ctrader/` and `api/` code is identical between the two — only `.env` (credentials, port, instrument) and this README differ. See `execution-service-pepperstone/README.md` for more background on the design decisions (no local terminal app, atomic SL/TP attachment, custom asyncio transport instead of the official Twisted client).

**All three services can run side by side** on different ports; nothing here modifies `execution-service/` or `execution-service-pepperstone/`.

## About the FTMO free trial

FTMO's free trial gives you a **14-day simulated evaluation account** — no real money, regardless of what environment cTrader classifies it under. **Don't assume it's a `demo`-flagged account** — confirmed in practice that FTMO trial accounts can show up as `isLive: true` in cTrader's own account-list metadata (`get_access_token.py` prints this per account). If so, `.env` needs `CTRADER_ENVIRONMENT=live` **and** `CONFIRM_LIVE=true` to actually connect, even though it's not real capital — that flag just matches cTrader's server classification, not FTMO's marketing language. Always check the printed `isLive` value for your specific account rather than assuming either way. The trial expires after 14 days; if `get_access_token.py` or the service stops finding your account, that's the first thing to check.

**Also don't confuse your FTMO trading *login* with the `ctidTraderAccountId`** the API actually needs — `get_access_token.py`'s account listing prints both per account (`ctidTraderAccountId=... login=...`); `CTRADER_ACCOUNT_ID` in `.env` must be the former. Using the login number there fails with `CH_CTID_TRADER_ACCOUNT_NOT_FOUND`.

## 1. One-time setup: register a cTrader Open API application

This step can't be automated — it's tied to your own FTMO/cTrader ID login:

1. Go to **https://openapi.ctrader.com** and register a new application (or reuse an existing one — a single registered app can authorize against any cTrader broker's accounts, since the app itself isn't broker-specific). This gives you a **Client ID** and **Client Secret**.
2. In that app's settings, add a redirect URI: `http://localhost:5052/callback` (this default deliberately avoids port 5000/5051 — see the macOS port warning below — but set your own and match it in `.env` if you prefer).
3. Copy `.env.example` to `.env` and fill in `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_REDIRECT_URI`, and a strong `WEBHOOK_SECRET`. Leave `CTRADER_ENVIRONMENT=demo` for now.

**FTMO uses its own cTrader ID portal**, separate from the generic cTrader.com login: **id-ct.ftmo.com**. FTMO emails you a link to set your cTrader password the first time a trial/challenge account is provisioned for you. If the OAuth authorize screen in step 2.1 below shows no trading accounts to grant access to, you're very likely logged into the wrong cTrader ID (e.g. one created directly on ctrader.com, or tied to a different broker like Pepperstone) — log out and back in with the cTrader ID that received the FTMO account-provisioning email instead.

**macOS port 5000 conflict:** macOS's Control Center "AirPlay Receiver" feature squats on port 5000 (IPv6 loopback) by default, and this repo's Pepperstone service already uses 5051 — that's why this service defaults to **5052**. If your browser still shows something like "Access to localhost was denied" right after authorizing, either disable AirPlay Receiver (System Settings → General → AirDrop & Handoff), or pick a different free port for `CTRADER_REDIRECT_URI` — if you do, you **must** update the redirect URI registered on the app's settings page at openapi.ctrader.com to match exactly, or the token exchange will fail. Authorization codes are single-use and expire in about a minute, so if one attempt fails, get a fresh code rather than retrying the same URL.

## 2. Service setup

```bash
cd execution-service-ftmo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

(Already done once for you if this README shipped alongside an already-populated `venv/` — check `venv/bin/python` exists before redoing this.)

### 2.1 Get an access token (one-time OAuth flow)

```bash
python get_access_token.py
```

This opens your browser to authorize the app against your FTMO cTrader account (log in with the cTrader ID tied to FTMO — this script never sees your password, only the OAuth redirect). It then prints an access token and refresh token to paste into `.env`, plus the `ctidTraderAccountId`(s) linked to that token — check the `isLive` flag it prints per account to confirm which one is your FTMO trial account. Access tokens last ~30 days; the refresh token doesn't expire and can mint new access tokens without repeating this browser flow.

### 2.2 Standalone connection test (do this before anything else)

```bash
python tests/test_ctrader_connection.py
```

This connects to your account, resolves the configured symbol (default `XAUUSD` — confirm FTMO actually offers this on your trial account; adjust `SYMBOL_NAME` in `.env` if not), places one market order with a wide dummy SL/TP, and prints the resulting position. **Confirm in the cTrader web/desktop platform** that the position has both a stop-loss and take-profit attached before proceeding.

### 2.3 Run the service

```bash
python main.py
```

Connects to cTrader and starts `/webhook` on `EXECUTION_SERVICE_HOST:EXECUTION_SERVICE_PORT` (default `0.0.0.0:8002` — distinct from IBKR's 8000 and Pepperstone's 8001, so all three can run at once).

### 2.4 Standalone test of `/webhook`

```bash
curl -X POST http://localhost:8002/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your WEBHOOK_SECRET>" \
  -d '{"type": "buy"}'
```

Confirm this places a real trial-account trade with default lot/SL/TP. Also try omitting the header (expect `401`) and an invalid `type` (expect `400`, no order placed).

### 2.5 Unit tests

```bash
pytest tests/test_webhook_payloads.py
```

## 3. Exposing `/webhook` to TradingView

Same as the other two services — use a tunnel (Cloudflare Tunnel or ngrok) pointed at port 8002, and configure the TradingView alert's webhook URL + `X-Webhook-Secret` header (or `?secret=` query param) accordingly. The `manager/` control panel handles this for you — see its README.

## 4. Telegram notifications

Same setup and contract as the other services — same bot/chat works across all three if you want one bot notifying for everything, or use a separate bot to tell them apart. See `execution-service/README.md` section 4 for the BotFather steps if you haven't done this before. Not yet configured for this service (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` blank in `.env` — notifications are currently no-ops).

## 5. Demo → live switch

Only relevant if FTMO provisions you a real, funded account on cTrader (not the free trial):

```
CTRADER_ENVIRONMENT=live
CONFIRM_LIVE=true       # required — the service refuses to start otherwise
```

Then run `get_access_token.py` again against that account (demo and live are entirely separate account universes in cTrader — a demo-account access token will not work against live) and update `.env` with the new tokens. Re-run the full safety checklist below before trusting it with real capital.

## 6. Instrument notes — read before sizing positions

- Confirm what FTMO's cTrader offering actually calls gold (or whichever instrument you intend to trade) and its lot economics before assuming `XAUUSD` / 1 lot = 100oz carries over unchanged from Pepperstone — prop-firm cTrader setups can differ. `get_access_token.py` and the standalone test both log the resolved symbol's `digits`/`lotSize`/volume constraints.
- `TICK_SIZE` in `.env` is used to convert the webhook's `tp`/`sl` (in ticks) into cTrader's relative SL/TP unit. Confirm it matches your account's actual price precision for whatever symbol you use.
- Check the account's actual balance/leverage before sizing `lot` — an order that exceeds available margin gets **no response at all** from cTrader (see the gotcha below), not a clean rejection.

## 7. Safety checklist (verify before any live-money use)

- [x] `CTRADER_ENVIRONMENT`/`CONFIRM_LIVE` correctly gate the connection — this account required both set (see the gotcha above); connection confirmed working 2026-08-17 (`cTrader account authenticated`, symbol resolved, spot prices subscribed).
- [ ] Every filled position is confirmed to carry both a stop-loss and take-profit (`ctrader/orders.py` checks this and alerts if not).
- [ ] `/webhook` rejects any request without a valid `WEBHOOK_SECRET`.
- [ ] A missing or invalid `type` field results in no order being placed (tested, not just reviewed).
- [ ] Ticks-to-relative-SL/TP conversion verified correct for both BUY and SELL with a real trial-account trade.
- [ ] All order placements and outcomes are logged with timestamps.
- [ ] Reconnect-with-backoff logic works after a real network drop, and a stuck/disconnected state is surfaced via log + Telegram.
- [ ] A Telegram message actually arrives (tested live) for: trade opened, rejected order, and cTrader disconnection/token invalidation.

Connection-level items are confirmed; **no actual order has been placed against this account yet**. The code is shared with the already-verified Pepperstone backend (see its README's section 9 for what was found and fixed there), but that verification doesn't automatically carry over to a different broker/account — confirm every remaining item here before trusting it with real webhook traffic.

## 8. Architecture notes

- `ctrader/positions.py` is a read-only placeholder today — structured for a future flatten/close action, not implemented in v1. There is currently no way to close a position through this service; do it from the cTrader platform directly.
- Same non-goals as the other services: no risk-based position sizing, no multi-symbol support, no strategy logic in this system, no web dashboard.

## 9. Known gotchas (inherited from the Pepperstone backend — same code, so same failure modes apply)

- **Order rejections could be silently swallowed** if the fire-and-forget order path's error routing regresses — `ctrader/client.py`'s `_on_message` must route `ProtoOAErrorRes` to `_execution_waiters` (not just `_pending`), or a real rejection reason from cTrader disappears behind an opaque 15-second timeout.
- **False "unprotected position" alarms** are possible right after a fill — cTrader's `ORDER_FILLED` event can carry a stale `position` snapshot that hasn't caught up to the SL/TP it just applied. `ctrader/orders.py` re-confirms via `CTraderClient.get_position()` (a `ProtoOAReconcileReq` call) before concluding a position is genuinely unprotected.
- **Orders that exceed available margin get no response at all** from cTrader — not even a rejection tied to the request — and hit the same 15-second timeout with a blank error. If `/webhook` orders seem to silently hang, check the requested `lot` size against the account's actual balance/leverage before assuming it's a code bug.
- **`CH_CTID_TRADER_ACCOUNT_NOT_FOUND` on startup** almost always means `CTRADER_ACCOUNT_ID` in `.env` was set to the trading **login** number instead of the `ctidTraderAccountId` — they're different numbers. Re-run `get_access_token.py` (or check its earlier output) and use the `ctidTraderAccountId=` value, not `login=`.
- **This FTMO trial account is classified `LIVE` by cTrader**, confirmed via the account listing, despite carrying no real money — see "About the FTMO free trial" above. Don't assume a trial/demo-sounding account is safe to leave `CTRADER_ENVIRONMENT=demo` for; check the actual `isLive` flag.
