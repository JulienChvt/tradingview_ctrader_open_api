"""Subprocess lifecycle management for the two trading services and their
shared ngrok tunnel.

Deliberately doesn't assume this manager is the only thing that ever
started these processes — a service already running from a prior session
(started by hand) is detected via its listening port, and can still be
stopped cleanly by looking up the PID bound to that port rather than relying
only on a Popen handle this process happens to hold.

Tunnels: ngrok's free tier allows only one agent session at a time, so this
does NOT run one `ngrok http` process per service. Instead it runs a single
shared agent process with both services defined as named endpoints in a
generated config file, started with `ngrok start <name> [<name>...]` for
whichever service(s) currently have their tunnel enabled — toggling one
service's tunnel restarts the shared agent with the updated name list.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

SERVICES = {
    "ibkr": {
        "label": "IBKR",
        "dir": REPO_ROOT / "execution-service",
        "default_port": 8000,
        "health_key": "ibkr_connected",
    },
    "pepperstone": {
        "label": "Pepperstone",
        "dir": REPO_ROOT / "execution-service-pepperstone",
        "default_port": 8001,
        "health_key": "ctrader_connected",
    },
}

NGROK_INSPECTOR_PORT = 4040
NGROK_DEFAULT_CONFIG = Path.home() / "Library/Application Support/ngrok/ngrok.yml"
NGROK_MANAGER_CONFIG = Path(__file__).resolve().parent / "ngrok_endpoints.yml"

# Popen handles for processes *this manager instance* started — used for the
# fast/clean stop path. Port-based lookup is the fallback for anything else.
_service_procs: dict[str, subprocess.Popen] = {}
_ngrok_proc: subprocess.Popen | None = None
_active_tunnel_names: set[str] = set()


def _service_env(name: str) -> dict:
    env_file = SERVICES[name]["dir"] / ".env"
    return dotenv_values(env_file) if env_file.exists() else {}


def get_service_port(name: str) -> int:
    env = _service_env(name)
    port = env.get("EXECUTION_SERVICE_PORT")
    return int(port) if port else SERVICES[name]["default_port"]


def get_env_setting(name: str, key: str) -> str:
    return _service_env(name).get(key) or ""


def set_env_setting(name: str, key: str, value: str) -> None:
    """Updates a single key in a service's .env file in place, preserving
    every other line. Appends the key if it isn't present yet."""
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError("value must not contain newlines")

    env_file = SERVICES[name]["dir"] / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")


def _find_pid_on_port(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in result.stdout.strip().splitlines() if p]
        return int(pids[0]) if pids else None
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_pid(pid: int, timeout: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _is_pid_alive(pid):
                return
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# -- services -----------------------------------------------------------------

def service_status(name: str) -> dict:
    port = get_service_port(name)
    pid = _find_pid_on_port(port)
    running = pid is not None
    connected = None
    if running:
        health_key = SERVICES[name]["health_key"]
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
            connected = bool(resp.json().get(health_key))
        except Exception:
            connected = None
    return {"running": running, "pid": pid, "connected": connected, "port": port}


def start_service(name: str) -> dict:
    port = get_service_port(name)
    if _find_pid_on_port(port):
        return service_status(name)

    service_dir = SERVICES[name]["dir"]
    venv_python = service_dir / "venv" / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError(
            f"{venv_python} not found — run the setup steps in "
            f"{service_dir.name}/README.md first (venv + pip install)"
        )

    log_path = LOG_DIR / f"{name}.log"
    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        [str(venv_python), "main.py"],
        cwd=service_dir,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    _service_procs[name] = proc

    # Give it a moment to bind its port before reporting status back.
    for _ in range(30):  # up to ~6s
        if _find_pid_on_port(port):
            break
        if proc.poll() is not None:
            break  # process already exited — no point waiting further
        time.sleep(0.2)

    result = service_status(name)
    if not result["running"]:
        result["error_hint"] = _tail_log(log_path)
    return result


def _tail_log(path: Path, lines: int = 6) -> str:
    try:
        content = path.read_text().splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


def stop_service(name: str) -> dict:
    port = get_service_port(name)
    proc = _service_procs.pop(name, None)
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        pid = _find_pid_on_port(port)
        if pid:
            _kill_pid(pid)
    return service_status(name)


# -- shared ngrok tunnel ----------------------------------------------------

def _write_ngrok_endpoints_config() -> None:
    lines = ["version: 3", "endpoints:"]
    for name in SERVICES:
        port = get_service_port(name)
        lines.append(f"  - name: {name}")
        lines.append("    upstream:")
        lines.append(f"      url: {port}")
    NGROK_MANAGER_CONFIG.write_text("\n".join(lines) + "\n")


def _ngrok_agent_running() -> bool:
    return _ngrok_proc is not None and _ngrok_proc.poll() is None


def _restart_ngrok_agent() -> None:
    """(Re)starts the single shared ngrok agent to match `_active_tunnel_names`.
    Stops it outright if that set is empty."""
    global _ngrok_proc

    if _ngrok_proc is not None and _ngrok_proc.poll() is None:
        _ngrok_proc.terminate()
        try:
            _ngrok_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ngrok_proc.kill()
    _ngrok_proc = None

    # Also reclaim the inspector port from any stray/orphaned agent this
    # manager didn't itself start (e.g. one launched by hand earlier) —
    # ngrok's free tier only allows one agent session, so a leftover agent
    # here would make the new one fail to authenticate.
    stray_pid = _find_pid_on_port(NGROK_INSPECTOR_PORT)
    if stray_pid:
        _kill_pid(stray_pid)

    if not _active_tunnel_names:
        return

    _write_ngrok_endpoints_config()
    log_file = open(LOG_DIR / "ngrok.log", "a")
    args = [
        "ngrok", "start", *sorted(_active_tunnel_names),
        "--config", str(NGROK_DEFAULT_CONFIG),
        "--config", str(NGROK_MANAGER_CONFIG),
        "--log=stdout",
    ]
    _ngrok_proc = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT)

    for _ in range(50):  # up to ~10s for the agent to authenticate and come up
        try:
            httpx.get(f"http://127.0.0.1:{NGROK_INSPECTOR_PORT}/api/tunnels", timeout=1)
            return
        except Exception:
            time.sleep(0.2)


def _get_endpoint_url(name: str) -> str | None:
    try:
        resp = httpx.get(f"http://127.0.0.1:{NGROK_INSPECTOR_PORT}/api/tunnels", timeout=2)
        tunnels = resp.json().get("tunnels", [])
    except Exception:
        return None

    for t in tunnels:
        if t.get("name") == name:
            return t.get("public_url")

    # Fallback: match by upstream port if the inspector API doesn't echo names.
    port = get_service_port(name)
    for t in tunnels:
        addr = str(t.get("config", {}).get("addr", ""))
        if addr.endswith(f":{port}") or addr == str(port):
            return t.get("public_url")
    return None


def tunnel_status(name: str) -> dict:
    running = name in _active_tunnel_names and _ngrok_agent_running()
    public_url = _get_endpoint_url(name) if running else None
    return {"running": running, "public_url": public_url, "inspector_port": NGROK_INSPECTOR_PORT}


def start_tunnel(name: str) -> dict:
    _active_tunnel_names.add(name)
    _restart_ngrok_agent()
    return tunnel_status(name)


def stop_tunnel(name: str) -> dict:
    _active_tunnel_names.discard(name)
    _restart_ngrok_agent()
    return tunnel_status(name)


def stop_everything() -> dict:
    """Stops tunnels first (cutting public reachability immediately), then
    the backend services."""
    result = {}
    _active_tunnel_names.clear()
    _restart_ngrok_agent()
    for name in SERVICES:
        result[f"{name}_tunnel"] = tunnel_status(name)
    for name in SERVICES:
        result[f"{name}_service"] = stop_service(name)
    return result
