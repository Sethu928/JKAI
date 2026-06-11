#!/usr/bin/env python3
"""
Watchdog J-KAI — destiné à tourner en cron toutes les 2 minutes.
Vérifie GET /health, redémarre le service si nécessaire.
Ecrit dans logs/watchdog.log uniquement en cas de problème
ou une fois par heure pour prouver qu'il tourne.
"""
import json
import os
import subprocess
from datetime import datetime
from urllib.error import URLError
from urllib.request import urlopen

HEALTH_URL          = "http://127.0.0.1:5000/health"
SERVICE_NAME        = "jkai"
LOG_FILE            = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "watchdog.log")
TIMEOUT             = 5    # secondes
HEARTBEAT_INTERVAL  = 3600 # une ligne [OK] par heure maximum
LOG_MAX_LINES       = 1000 # rotation légère pour éviter la croissance infinie


def _log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    _trim_log()


def _trim_log() -> None:
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > LOG_MAX_LINES:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-LOG_MAX_LINES:])
    except OSError:
        pass


def _last_heartbeat_age() -> float:
    """Secondes depuis le dernier [OK] dans le log. +inf si aucun."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return float("inf")
    for line in reversed(lines):
        if "[OK]" in line:
            try:
                ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                return (datetime.now() - ts).total_seconds()
            except ValueError:
                continue
    return float("inf")


def _restart(reason: str) -> None:
    _log(f"[RESTART] {reason}")
    subprocess.run(
        ["sudo", "systemctl", "restart", SERVICE_NAME],
        check=False,
        timeout=30,
    )


def main() -> None:
    reason = None

    try:
        with urlopen(HEALTH_URL, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "ok":
            reason = f"status inattendu : {data.get('status')!r}"
    except URLError as e:
        reason = f"serveur injoignable : {e.reason}"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"

    if reason:
        _restart(reason)
    elif _last_heartbeat_age() >= HEARTBEAT_INTERVAL:
        _log("[OK] service actif")


if __name__ == "__main__":
    main()
