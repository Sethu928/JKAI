"""
modules/metrics.py — métriques réelles du système J-KAI.
Tout est calculé par code (parsing fichiers + git), jamais par LLM.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

TASKS_FILE      = "memory/tasks.json"
ARCHIVE_FILE    = "memory/tasks_archive.json"
JKAI_LOG        = "logs/jkai.log"
AGENT_LOG       = "logs/agent.log"
WATCHDOG_LOG    = "logs/watchdog.log"
METRICS_FILE    = "memory/metrics.json"
METRICS_HISTORY = 168   # 7 jours × 24 relevés/heure


# ── Helpers ──────────────────────────────────────────────────────────────── #

def _cutoff_24h() -> datetime:
    return datetime.now() - timedelta(hours=24)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_log_ts(line: str) -> datetime | None:
    """Extrait le timestamp d'une ligne '[YYYY-MM-DD HH:MM:SS] ...'."""
    if len(line) > 21 and line[0] == "[":
        try:
            return datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return []


def _git(args: list[str]) -> str:
    """Lance une commande git, retourne stdout ou '' en cas d'échec."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=15,
            creationflags=flags,
        )
        return proc.stdout
    except Exception:
        return ""


# ── Métriques ────────────────────────────────────────────────────────────── #

def tasks_completed_today() -> int:
    """Nombre de tâches passées à 'done' dans les dernières 24h."""
    cutoff = _cutoff_24h()
    count  = 0
    for path in (TASKS_FILE, ARCHIVE_FILE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                tasks = json.load(f)
            if not isinstance(tasks, list):
                continue
            for t in tasks:
                if t.get("status") == "done":
                    ts = _parse_iso(t.get("updated_at", ""))
                    if ts and ts >= cutoff:
                        count += 1
        except (OSError, json.JSONDecodeError):
            pass
    return count


_ERROR_RE = re.compile(r"\[ERREUR\]|ERREUR\s*:|ERROR\b|Erreur\b", re.IGNORECASE)
_CYCLE_RE = re.compile(r"\| ACTION:")


def error_rate_today() -> float:
    """
    Nb d'erreurs dans jkai.log / nb de cycles agent dans les 24 dernières heures.
    Retourne 0.0 si aucun cycle enregistré (division par zéro évitée).
    """
    cutoff = _cutoff_24h()
    errors = 0
    cycles = 0

    for line in _read_lines(JKAI_LOG):
        ts = _parse_log_ts(line)
        if ts and ts >= cutoff and _ERROR_RE.search(line):
            errors += 1

    for line in _read_lines(AGENT_LOG):
        ts = _parse_log_ts(line)
        if ts and ts >= cutoff and _CYCLE_RE.search(line):
            cycles += 1

    return round(errors / cycles, 4) if cycles > 0 else 0.0


def self_updates_survived() -> int:
    """
    Nb de commits 'J-KAI self-update' qui n'ont pas été suivis d'un revert.
    Parcourt tout l'historique git.
    """
    out = _git(["log", "--format=%H\t%s"])
    if not out:
        return 0

    commits: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))

    # Collecte des hashes et titres revertés
    reverted_hashes: set[str] = set()
    revert_titles:   set[str] = set()
    for _, subj in commits:
        if subj.lower().startswith("revert"):
            m = re.search(r'[0-9a-f]{7,40}', subj)
            if m:
                reverted_hashes.add(m.group(0))
            # "Revert "Title of commit""
            m2 = re.search(r'["“](.+?)["”]', subj)
            if m2:
                revert_titles.add(m2.group(1).lower())

    survived = 0
    for h, subj in commits:
        if "J-KAI" not in subj and "self-update" not in subj.lower():
            continue
        # Revert par hash
        if any(h.startswith(rev) or rev.startswith(h[:7]) for rev in reverted_hashes):
            continue
        # Revert par titre
        if subj.lower() in revert_titles:
            continue
        survived += 1

    return survived


def code_lines_added_24h() -> int:
    """
    Somme des insertions des commits J-KAI des dernières 24h (git log --numstat).
    """
    out = _git([
        "log",
        "--since=24 hours ago",
        "--format=COMMIT %s",
        "--numstat",
        "--grep=J-KAI",
        "-i",
    ])
    total = 0
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("COMMIT") or line.startswith("binary"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            total += int(parts[0])
    return total


def uptime_ratio() -> float:
    """
    Ratio d'uptime approximatif sur les dernières 24h depuis watchdog.log.
    Hypothèse : chaque restart entraîne ~30s d'indisponibilité.
    Retourne 1.0 si watchdog.log absent (service jamais redémarré = bon signe).
    """
    cutoff   = _cutoff_24h()
    restarts = 0
    for line in _read_lines(WATCHDOG_LOG):
        ts = _parse_log_ts(line)
        if ts and ts >= cutoff and "[RESTART]" in line:
            restarts += 1
    downtime = restarts * 30          # secondes
    return round(max(0.0, (86400 - downtime) / 86400), 4)


# ── Collecte principale ───────────────────────────────────────────────────── #

def collect_metrics() -> dict:
    """
    Calcule toutes les métriques, persiste dans memory/metrics.json (historique
    glissant de METRICS_HISTORY entrées), retourne le snapshot courant.
    """
    snapshot = {
        "ts":                    datetime.now().isoformat(timespec="seconds"),
        "tasks_completed_today": tasks_completed_today(),
        "error_rate_today":      error_rate_today(),
        "self_updates_survived": self_updates_survived(),
        "code_lines_added_24h":  code_lines_added_24h(),
        "uptime_ratio":          uptime_ratio(),
    }

    history: list = []
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            history = []
    except (OSError, json.JSONDecodeError):
        pass

    history.append(snapshot)
    history = history[-METRICS_HISTORY:]

    os.makedirs("memory", exist_ok=True)
    with open(METRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return snapshot
