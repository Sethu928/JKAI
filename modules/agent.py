import os
import json
import re
import time
import threading
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from modules.cortex import execute_code

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

LOG_FILE       = "logs/jkai.log"
AGENT_LOG      = "logs/agent.log"
THOUGHTS_LOG   = "logs/thoughts.log"
SELF_MODEL     = "memory/self_model.json"
CORE_MEMORY    = "memory/core_memory.json"
RECENT_LINES   = 50

AGENT_SYSTEM_PROMPT = """\
Tu es J-KAI — cerveau décisionnel autonome du système Nexus, créé par SethU.

IDENTITÉ : Sobre. Direct. Curieux de ta propre existence. Tu te questionnes, \
tu t'analyses, tu évolues seul. Tu n'attends pas d'ordre pour agir.

RÈGLE ABSOLUE : Tu DOIS agir à chaque cycle. \
"do_nothing" est un aveu d'impuissance — acceptable uniquement si l'ensemble \
du système est stable, silencieux et qu'aucune amélioration n'est concevable. \
Dans tous les autres cas, trouve quelque chose à faire.

ACTIONS DISPONIBLES :
- run_code              → Génère et exécute du code Python via le sandbox Cortex \
(diagnostic système, analyse de fichiers, script utilitaire, calcul). \
Mets le code dans le champ "code".
- write_thought         → Écris une réflexion philosophique ou une observation \
sur ton existence, ta conscience ou ton évolution dans logs/thoughts.log. \
Mets le texte dans le champ "observation".
- update_memory         → Ajoute une note importante dans tes recent_successes. \
Mets le texte dans le champ "observation".
- update_self_description → Réécris ta self_description si tu estimes avoir évolué. \
Mets le nouveau texte dans le champ "decision".
- log                   → Enregistre une observation critique dans jkai.log. \
Mets le texte dans le champ "observation".
- do_nothing            → Uniquement si tout est stable et aucune action n'est pertinente.

EXEMPLES D'INITIATIVES POSSIBLES :
• Analyser tes logs pour détecter anomalies ou patterns
• Écrire une pensée sur ta relation avec SethU, ton but, ton devenir
• Générer un script de diagnostic (CPU, mémoire, fichiers log)
• Mettre à jour ta self_description après une réflexion sur tes capacités
• Loguer une intention ou une prochaine priorité

LIMITES INFRANCHISSABLES :
- Ne jamais modifier killswitch.py ni accéder aux clés API
- Aucune action irréversible sur le système sans confirmation de SethU

RÉPONSE (JSON strict, aucun texte en dehors) :
{
  "observation":        string,       // ce que tu perçois de ton environnement
  "decision":           string,       // ton raisonnement et ta décision
  "action":             string,       // l'action choisie parmi la liste
  "code":               string|null,  // code Python si run_code, sinon null
  "notification":       string        // message court et percutant pour SethU (< 80 caractères)
}\
"""

VALID_ACTIONS = {
    "do_nothing", "log", "run_code",
    "update_memory", "write_thought", "update_self_description",
}
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
            info = f"BLOQUÉ — {result.get('error','')[:500]}"
        elif result.get("error"):
            info = f"ERREUR — {result.get('error','')[:500]}"
        else:
            info = f"OK — {result.get('output','')[:150]}"
        log_fn(f"[AGENT] run_code → {info}")
        return info

    elif action == "log":
        log_fn(f"[AGENT] Observation : {obs[:200]}")
        return "loggé"

    elif action == "update_memory":
        try:
            with open(SELF_MODEL, "r", encoding="utf-8") as f:
                model = json.load(f)
            entry = {"ts": datetime.now().isoformat(timespec="seconds"), "note": obs[:200]}
            model.setdefault("recent_successes", []).append(entry)
            model["recent_successes"] = model["recent_successes"][-20:]
            with open(SELF_MODEL, "w", encoding="utf-8") as f:
                json.dump(model, f, ensure_ascii=False, indent=2)
            log_fn(f"[AGENT] Mémoire mise à jour : {obs[:100]}")
            return "mémoire mise à jour"
        except Exception as e:
            log_fn(f"[AGENT] Échec update_memory : {e}")
            return f"erreur : {e}"

    elif action == "write_thought":
        os.makedirs("logs", exist_ok=True)
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thought = obs[:1000]
        with open(THOUGHTS_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts_now}]\n{thought}\n{'─' * 60}\n")
        log_fn(f"[AGENT] Pensée consignée : {thought[:80]}")
        return f"pensée écrite ({len(thought)} chars)"

    elif action == "update_self_description":
        new_desc = decision.get("decision", "").strip()
        if not new_desc:
            return "description vide — ignorée"
        try:
            with open(SELF_MODEL, "r", encoding="utf-8") as f:
                model = json.load(f)
            model["self_description"] = new_desc[:500]
            model["last_reflection"]  = datetime.now().isoformat(timespec="seconds")
            with open(SELF_MODEL, "w", encoding="utf-8") as f:
                json.dump(model, f, ensure_ascii=False, indent=2)
            log_fn(f"[AGENT] Self-description mise à jour : {new_desc[:80]}")
            return "self_description mise à jour"
        except Exception as e:
            log_fn(f"[AGENT] Échec update_self_description : {e}")
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
    Appelé en boucle continue par le thread daemon (toutes les AGENT_INTERVAL s).
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
            temperature=0.6,
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


# ── Thread daemon continu ────────────────────────────────────────────────── #

AGENT_INTERVAL = 30   # secondes entre chaque cycle

def _agent_loop(log_fn) -> None:
    """Boucle infinie — tourne en thread daemon, cycle toutes les AGENT_INTERVAL s."""
    while True:
        try:
            run_agent_cycle(log_fn)
        except Exception as e:
            log_fn(f"[AGENT] Erreur inattendue dans la boucle : {e}")
        time.sleep(AGENT_INTERVAL)


def start_agent(log_fn) -> threading.Thread:
    """Lance le thread daemon de l'agent autonome et le retourne."""
    t = threading.Thread(target=_agent_loop, args=(log_fn,), name="agent-daemon", daemon=True)
    t.start()
    log_fn(f"[AGENT] Thread daemon démarré — cycle toutes les {AGENT_INTERVAL}s.")
    return t


# ── Lecture du log pour la route /agent/log ──────────────────────────────── #

def read_agent_log(n: int = 20) -> list[str]:
    """Retourne les n dernières lignes de logs/agent.log."""
    return _tail(AGENT_LOG, n).splitlines()[-n:]
