# Trading System Manager

A local control panel for the two trading services (`execution-service/` for IBKR, `execution-service-pepperstone/` for Pepperstone) and their ngrok tunnel — start/stop each independently, see live connection status, and get the exact webhook URL + JSON to paste into a TradingView alert.

**This app binds to `127.0.0.1` only and must never be exposed via a tunnel itself** — it can start/stop processes, so it's a control plane, not something to put on the public internet.

## Setup

```bash
cd manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

Easiest way — a script that handles the venv activation for you:

```bash
./manage.sh start      # start it (no-ops if already running)
./manage.sh stop       # stop it
./manage.sh restart
./manage.sh status
```

Or manually:

```bash
source venv/bin/activate
python app.py
```

Then open **http://127.0.0.1:9000** in your browser — not by opening `static/index.html` directly, which the browser will block from making any of the API calls this page needs.

## What it does

- **Service cards** (IBKR, Pepperstone): Start/Stop buttons launch or kill that service's `main.py` using its own `venv`. Status reflects reality even if the process was started by hand outside the manager (detected by which PID is listening on that service's port) — and if a start fails (e.g. TWS isn't running), the last few log lines are shown so you know why.
- **Tunnel control**: because ngrok's free tier allows only **one agent session at a time**, this doesn't run two separate `ngrok http` processes. Instead there's a single shared agent, and toggling a service's tunnel on/off adds or removes that service from the shared agent's endpoint list (briefly restarting it — a couple of seconds). A generated `ngrok_endpoints.yml` in this directory defines both services' endpoints; your existing ngrok authtoken is picked up from `~/Library/Application Support/ngrok/ngrok.yml`.
- **Webhook info**: once a service's tunnel is up, its card shows the exact webhook URL, the `X-Webhook-Secret` header (read from that service's own `.env`), a JSON body example (using that service's configured defaults), and the query-param fallback URL — with copy buttons for each.
- **🛑 Stop All Services & Tunnels**: the safety button. Stops the shared tunnel first (cutting public reachability immediately), then both backend services. Confirmed to leave zero listening ports and zero related processes.

## Notes

- Starting a service's tunnel assigns a **new random ngrok URL** each time the shared agent restarts (free tier — no reserved domain). If you've already put a URL into a TradingView alert, you'll need to update it after any tunnel restart.
- This manager doesn't touch TWS or the Pepperstone OAuth flow — those remain manual steps (see each service's own README).
- Logs for everything the manager starts live in `manager/logs/`.
