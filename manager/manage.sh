#!/usr/bin/env bash
# Start/stop/restart the trading-system manager app itself (manager/app.py) —
# not the trading services, which the manager app controls once it's running.
#
# Usage: ./manage.sh {start|stop|restart|status}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PID_FILE="$SCRIPT_DIR/manager.pid"
LOG_FILE="$SCRIPT_DIR/logs/manager.log"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
PORT=9000

mkdir -p "$SCRIPT_DIR/logs"

# Prints the running PID and returns 0 if the manager is up, else returns 1.
running_pid() {
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return 0
    fi
  fi
  # Fallback: it may have been started some other way — check the port directly.
  port_pid=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
  if [ -n "$port_pid" ]; then
    echo "$port_pid"
    return 0
  fi
  return 1
}

cmd_start() {
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    echo "Manager already running (pid $pid) — http://127.0.0.1:$PORT"
    return 0
  fi

  if [ ! -x "$VENV_PYTHON" ]; then
    echo "Virtualenv not found at $VENV_PYTHON. Set it up first:" >&2
    echo "  cd $SCRIPT_DIR && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
    return 1
  fi

  nohup "$VENV_PYTHON" app.py >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  disown

  sleep 1
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    echo "Manager started (pid $pid) — http://127.0.0.1:$PORT"
  else
    echo "Manager failed to start — check $LOG_FILE" >&2
    return 1
  fi
}

cmd_stop() {
  pid=$(running_pid)
  if [ -z "$pid" ]; then
    echo "Manager is not running."
    rm -f "$PID_FILE"
    return 0
  fi

  kill "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.3
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null
  fi
  rm -f "$PID_FILE"
  echo "Manager stopped."
}

cmd_status() {
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    echo "Manager running (pid $pid) — http://127.0.0.1:$PORT"
  else
    echo "Manager is not running."
  fi
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
