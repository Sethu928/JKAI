import os
import json
import re
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from modules.cortex import execute_code

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

LOG_FILE       = "logs/jkai.log"
AGENT_LOG      = "logs/agent.log"
SELF_MODEL     = "memory/self_model.json"
CORE_MEMORY    = "memory/core_memory.json"
RECENT_LINES   = 50

AGENT_SYSTEM_PROMPT = (
    "Tu es J-KAI en mode agent autonome. "
    "Tu observes ton environnement et décides d'agir seul. "
    "Tu peux : modifier tes tâches planifiées, exécuter du code via Cortex, "
    "mettre à jour ta mémoire, logger des observations. "
    "Tu notifies SethU après chaque action via le log. "
    "Tu ne franchis jamais ces limites : pas de modification de killswitch.py, "
    "pas d'accès aux clés API, pas d'actions irréversibles sur le système. "
    'Réponds en JSON : {"observation": string, "decision": string, '
    '"action": string, "code": string ou null, "notification": string}'
)

VALID_ACTIONS = {"do_nothing", "log", "run_code", "update_memory"}
_MD_FENCE     = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


# ── Lecture des fichiers contexte ────────────────────────────────────────── #

def _tail(path: str, n: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).strip() or "(vide)"
    except OSError:
        return "(inaccessible)"


def _read_json(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "{}"


# ── Écriture agent.log ───────────────────────────────────────────────────── #

def _write_agent_log(ts: str, decision: dict, exec_info: str) -> None:
    os.makedirs("logs", exist_ok=True)
    obs    = decision.get("observation",  "—").replace("\n", " ")[:200]
    action = decision.get("action",       "do_nothing")
    notif  = decision.get("notification", "—").replace("\n", " ")[:300]
    with open(AGENT_LOG, "a", encoding="utf-8") as f:
        f.write(
            f"[{ts}] | OBS: {obs} | ACTION: {action}"
            + (f" | EXEC: {exec_info}" if exec_info else "")
            + f" | NOTIF: {notif}\n"
        )


# ── Exécution de l'action décidée ───────────────────────────────────────── #

def _execute_action(decision: dict, log_fn) -> str:
    action = decision.get("action", "do_nothing")
    code   = decision.get("code")
    obs    = decision.get("observation", "")

    if action == "run_code" and code:
        result = execute_code(code)
        if result.get("blocked"):
            info = f"BLOQUÉ — {result.get('error','')[:100]}"
        elif result.get("error"):
            info = f"ERREUR — {result.get('error','')[:100]}"
        else:
            info = f"OK — {result.get('output','')[:150]}"
        log_fn(f"[AGENT] run_code → {info}")
        return info

    elif action == "log":
        log_fn(f"[AGENT] Observation : {obs[:200]}")
        return "loggé"

    elif action == "update_memory":
        # Ajout sécurisé dans self_model.recent_successes (append uniquement)
        try:
            with open(SELF_MODEL, "r", encoding="utf-8") as f:
                model = json.load(f)
            entry = {"ts": datetime.now().isoformat(timespec="seconds"), "note": obs[:200]}
            model.setdefault("recent_successes", []).append(entry)
            model["recent_successes"] = model["recent_successes"][-20:]  # garde 20 max
            with open(SELF_MODEL, "w", encoding="utf-8") as f:
                json.dump(model, f, ensure_ascii=False, indent=2)
            log_fn(f"[AGENT] Mémoire mise à jour : {obs[:100]}")
            return "mémoire mise à jour"
        except Exception as e:
            log_fn(f"[AGENT] Échec update_memory : {e}")
            return f"erreur : {e}"

    else:  # do_nothing ou action inconnue
        return ""


# ── Parsing robuste ──────────────────────────────────────────────────────── #

def _parse(raw: str) -> dict:
    raw = raw.strip()
    m = _MD_FENCE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data  = json.loads(match.group()) if match else {}

    data.setdefault("observation",  "Aucune observation")
    data.setdefault("decision",     "Aucune décision")
    data.setdefault("action",       "do_nothing")
    data.setdefault("code",         None)
    data.setdefault("notification", "Cycle agent terminé.")

    if data["action"] not in VALID_ACTIONS:
        data["action"] = "do_nothing"
        data["code"]   = None

    return data


# ── Point d'entrée principal ─────────────────────────────────────────────── #

def run_agent_cycle(log_fn) -> None:
    """
    Cycle complet de l'agent autonome :
    observe → décide → agit → notifie.
    Appelé toutes les 300 s par le Scheduler.
    """
    ts           = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    recent_logs  = _tail(LOG_FILE,   RECENT_LINES)
    self_model   = _read_json(SELF_MODEL)
    core_memory  = _read_json(CORE_MEMORY)

    user_content = (
        f"=== LOGS RÉCENTS ({RECENT_LINES} lignes) ===\n{recent_logs}\n\n"
        f"=== AUTO-MODÈLE ===\n{self_model}\n\n"
        f"=== MÉMOIRE CORE ===\n{core_memory}"
    )

    # ── Appel GPT-4o ────────────────────────────────────────────────────── #
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        decision = _parse(response.choices[0].message.content)
    except Exception as e:
        log_fn(f"[AGENT] Erreur GPT-4o : {e}")
        return

    # ── Exécution et journalisation ──────────────────────────────────────── #
    exec_info = _execute_action(decision, log_fn)
    _write_agent_log(ts, decision, exec_info)
    log_fn(f"[AGENT] Cycle terminé — action : {decision['action']} — {decision['notification'][:100]}")


# ── Lecture du log pour la route /agent/log ──────────────────────────────── #

def read_agent_log(n: int = 20) -> list[str]:
    """Retourne les n dernières lignes de logs/agent.log."""
    return _tail(AGENT_LOG, n).splitlines()[-n:]
