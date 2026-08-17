# TradingView → Automated Execution

Three independent backends implementing the same TradingView webhook contract — pick one, or run several at once on different ports:

- **[`execution-service/`](execution-service/README.md)** — Interactive Brokers, via `ib_async` + TWS/IB Gateway. Trades gold futures (MGC/GC).
- **[`execution-service-pepperstone/`](execution-service-pepperstone/README.md)** — Pepperstone, via the cTrader Open API. Trades gold as a spot CFD (XAUUSD). No local terminal app required — connects straight to the broker's cloud host.
- **[`execution-service-ftmo/`](execution-service-ftmo/README.md)** — FTMO, also via the cTrader Open API (same broker-agnostic protocol as Pepperstone, just different account credentials). Currently set up against an FTMO free-trial (simulated) account.

All three expose the identical `/webhook` contract (`{"type","lot","tp","sl"}`, shared-secret auth, Telegram notifications), so a TradingView alert can point at whichever one you're running. This document covers the IBKR version; see each directory's own README for the cTrader-based ones.

## Managing everything: `manager/`

**[`manager/`](manager/README.md)** is a local control-panel web app for starting/stopping any of the services and its ngrok tunnel, seeing live connection status, and getting the exact webhook URL + JSON to paste into TradingView — including a one-click "stop everything" to make sure your Mac isn't exposed when you step away. Run `python manager/app.py` and open http://127.0.0.1:9000. This is the easiest way to operate day-to-day; the sections below are for setting up each backend the first time.

## IBKR version

A single long-running Python service (`execution-service/`) that runs on your MacBook alongside TWS/IB Gateway:

- Receives TradingView webhook alerts directly at `/webhook`
- Validates the payload + shared secret, applies defaults
- Builds a ticks-based bracket order (entry + stop-loss + take-profit) and places it via IBKR (`ib_async`)
- Sends Telegram notifications on trade open and on error

There is no separate Cloud Function — webhook receiving, order execution, and notifications all live in one process. This trades 24/7 availability for simplicity: the service (and your ability to receive alerts) is only "live" while your MacBook and this process are running, which fits a use case of testing strategies while you're at the keyboard rather than unattended live trading.

Trades gold futures (`MGC` by default, or `GC`) on COMEX. v1 scope only: no risk-based sizing, no multi-symbol support, no strategy logic — TradingView owns signal generation, this system only executes.

## 1. One-time manual setup: TWS / IB Gateway API access

This step cannot be automated — do it once yourself in TWS or IB Gateway:

1. Open **Configuration → API → Settings**.
2. Enable **"Enable ActiveX and Socket Clients"**.
3. Add `127.0.0.1` to **Trusted IPs**.
4. Set the **Socket port** to match the environment you're running:

   | Environment | Application | Port |
   |---|---|---|
   | Paper | TWS | 7497 |
   | Paper | IB Gateway | 4002 |
   | Live | TWS | 7496 |
   | Live | IB Gateway | 4001 |

Leave TWS/Gateway running whenever the service needs to place orders.

## 2. Service setup

```bash
cd execution-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`: set `WEBHOOK_SECRET` to a strong random value, and fill in `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (see section 4 below). Leave `IBKR_PORT=7497` for paper trading against TWS.

### 2.1 Standalone IBKR connection test (do this before anything else)

With TWS or IB Gateway running in paper mode:

```bash
python tests/test_ibkr_connection.py
```

This connects, qualifies the front-month `MGC` contract, places one bracket order with a wide dummy SL/TP, and prints status transitions. **Confirm in TWS's paper account** that the entry filled and both SL and TP legs are live before proceeding.

### 2.2 Run the service

```bash
python main.py
```

This connects to IBKR and starts the `/webhook` API on `EXECUTION_SERVICE_HOST:EXECUTION_SERVICE_PORT` (default `0.0.0.0:8000`).

### 2.3 Standalone test of `/webhook` (before wiring up TradingView)

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your WEBHOOK_SECRET>" \
  -d '{"type": "buy"}'
```

Confirm this places a real paper trade with default lot/SL/TP (5 / 40 ticks / 40 ticks). Also try omitting `X-Webhook-Secret` (expect `401`) and an invalid `type` (expect `400`, no order placed).

### 2.4 Unit tests

```bash
pytest tests/test_webhook_payloads.py
```

## 3. Exposing `/webhook` to TradingView

TradingView fires alerts from its own servers, not your browser — so it needs a public URL to hit, and a MacBook has no stable public IP. Use a tunnel to expose your local `/webhook` port publicly:

- **Cloudflare Tunnel** (recommended — free, and a named tunnel gives you a stable hostname that doesn't change on restart):
  ```bash
  brew install cloudflared
  cloudflared tunnel --url http://localhost:8000
  ```
  (A quick tunnel prints a random `*.trycloudflare.com` URL each run — fine for testing sessions where you re-paste the URL; set up a named tunnel with your own domain if you want it stable.)
- **ngrok** also works (`ngrok http 8000`), but its free tier changes the URL every restart.

Whichever you use, only start the tunnel while you're actively testing — closing it (or your laptop) means TradingView alerts simply fail to deliver, which is the intended behavior here (no trades open while you're not around).

### 3.1 TradingView alert configuration

Use the tunnel URL + `/webhook` as the alert's webhook URL (e.g. `https://your-tunnel-host/webhook`). Set the shared secret via the `X-Webhook-Secret` custom header if TradingView's alert UI supports it; otherwise append `?secret=<WEBHOOK_SECRET>` to the URL as a fallback. The alert message body must stay exactly:

```json
{ "type": "buy", "lot": 5, "tp": 40, "sl": 40 }
```

(`lot`, `tp`, `sl` are optional and default to 5/40/40.)

## 4. Telegram notifications setup

1. Message **@BotFather** on Telegram, run `/newbot`, and copy the returned token → `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot.
3. Call `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read the `chat.id` field from the response → `TELEGRAM_CHAT_ID`.
4. Set both values in `execution-service/.env`.

Test each trigger explicitly before relying on it:
- Trade opened → fire a valid alert against paper trading.
- Order rejected → attempt an order IBKR will reject (e.g. an invalid tick price).
- IBKR disconnected → stop TWS/Gateway while the service is running.
- Auth failure → call `/webhook` with a wrong `X-Webhook-Secret`.

## 5. Paper → live switch

Changing environments is a config-only change — no code changes required.

```
IBKR_PORT=7496          # or 4001 for IB Gateway live
CONFIRM_LIVE=true       # required — the service refuses to start on a live port otherwise
```

Re-run the full safety checklist below against live before trusting it with real capital.

## 6. Safety checklist (verify before any live-money use)

- [ ] `IBKR_PORT` defaults to paper (7497/4002); switching to live requires both a port change **and** `CONFIRM_LIVE=true`.
- [ ] Every bracket order's SL and TP legs are confirmed live in TWS before the entry is considered protected.
- [ ] `/webhook` rejects any request without a valid `WEBHOOK_SECRET`.
- [ ] A missing or invalid `type` field results in no order being placed (tested, not just reviewed).
- [ ] Ticks-to-price conversion verified correct for both BUY and SELL with a real paper trade.
- [ ] All order placements and outcomes are logged with timestamps.
- [ ] IBKR reconnect-with-backoff logic works, and a stuck/disconnected state is surfaced via log + Telegram.
- [ ] A Telegram message actually arrives (tested live) for: trade opened, rejected order, and IBKR disconnection.

## 7. Architecture notes

- This service is the only component that can move money — it validates every incoming payload itself; nothing upstream is trusted.
- Exposing `/webhook` via a tunnel means the shared secret is your only auth boundary — keep it long and random, and treat repeated auth-failure Telegram alerts as a signal to check who's hitting the endpoint.
- `execution-service/ibkr/positions.py` is a read-only placeholder today — structured so a future flatten/close action can be added without restructuring, but not implemented in v1.
- Non-goals for v1: dynamic/risk-based position sizing, multi-symbol support, strategy logic inside this system, a web dashboard, and automated contract rollover trading logic beyond resolving the current front month at startup.
