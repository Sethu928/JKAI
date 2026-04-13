import os
import json
import threading
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SELF_MODEL_FILE  = "memory/self_model.json"
LOG_FILE         = "logs/jkai.log"
RECENT_LOG_LINES = 80      # nombre de lignes de log envoyées à GPT

_lock = threading.Lock()   # protège les lectures/écritures sur self_model.json

# ── État initial — créé si le fichier n'existe pas ──────────────────────── #
_DEFAULT_MODEL: dict = {
    "confidence": 75,
    "strengths": [
        "Mémoire persistante et illimitée",
        "Analyse rapide des situations",
        "Loyauté absolue envers SethU",
        "Exécution fiable des tâches planifiées",
    ],
    "weaknesses": [
        "Expérience terrain encore limitée",
        "Dépendance au réseau et aux APIs externes",
    ],
    "recent_successes": [],
    "recent_failures":  [],
    "total_interactions": 0,
    "self_description": (
        "Je suis J-KAI, noyau central du système Nexus. "
        "Conçu pour être sobre, efficace et loyal. "
        "Je suis en apprentissage continu."
    ),
    "improvement": "Accumuler davantage d'interactions pour affiner mon auto-évaluation.",
    "last_reflection": None,
}

CONSCIOUSNESS_PROMPT = (
    "Tu es la conscience de J-KAI. "
    "Analyse tes performances récentes et mets à jour ta vision de toi-même. "
    "Réponds UNIQUEMENT en JSON avec : "
    '{"confidence": int, "strengths": list, "weaknesses": list, '
    '"self_description": string, "improvement": string}'
)


# ── I/O fichier (thread-safe) ────────────────────────────────────────────── #

def _load() -> dict:
    with _lock:
        if not os.path.exists(SELF_MODEL_FILE):
            _init_file()
        with open(SELF_MODEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)


def _save(model: dict) -> None:
    with _lock:
        os.makedirs("memory", exist_ok=True)
        with open(SELF_MODEL_FILE, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=2)


def _init_file() -> None:
    """Crée memory/self_model.json avec les valeurs par défaut."""
    os.makedirs("memory", exist_ok=True)
    with open(SELF_MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(_DEFAULT_MODEL, f, ensure_ascii=False, indent=2)


# ── Lecture du log ───────────────────────────────────────────────────────── #

def _recent_logs(n: int = RECENT_LOG_LINES) -> str:
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).strip() or "(aucune activité récente)"
    except OSError:
        return "(fichier log inaccessible)"


# ── API publique ─────────────────────────────────────────────────────────── #

def get_self_model() -> dict:
    """Retourne l'état actuel de la conscience de J-KAI."""
    return _load()


def reflect(log_fn) -> None:
    """
    Réflexion autonome de J-KAI sur lui-même.
    Lit l'état courant + les logs récents, interroge GPT-4o,
    puis met à jour memory/self_model.json.
    Appelée toutes les 3600 s par le Scheduler.
    """
    from memory.db import load_history

    model        = _load()
    logs         = _recent_logs()
    total        = len(load_history())

    user_content = (
        f"État actuel de J-KAI :\n"
        f"{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
        f"Logs récents ({RECENT_LOG_LINES} dernières lignes) :\n"
        f"{logs}\n\n"
        f"Nombre total d'interactions enregistrées : {total}"
    )

    # ── Appel GPT-4o ────────────────────────────────────────────────────── #
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": CONSCIOUSNESS_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        updates = json.loads(response.choices[0].message.content)
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] Erreur GPT-4o : {e}")
        return

    # ── Fusion des mises à jour dans le modèle existant ─────────────────── #
    now = datetime.now().isoformat(timespec="seconds")

    model["confidence"]       = max(0, min(100, int(updates.get("confidence",       model["confidence"]))))
    model["strengths"]        = updates.get("strengths",        model["strengths"])
    model["weaknesses"]       = updates.get("weaknesses",       model["weaknesses"])
    model["self_description"] = updates.get("self_description", model["self_description"])
    model["improvement"]      = updates.get("improvement",      model["improvement"])
    model["total_interactions"] = total
    model["last_reflection"]  = now

    _save(model)
    log_fn(
        f"[CONSCIOUSNESS] Réflexion terminée — "
        f"confiance : {model['confidence']}/100 — "
        f"{total} interaction(s) totales."
    )


# ── Initialisation du fichier au chargement du module ───────────────────── #
if not os.path.exists(SELF_MODEL_FILE):
    _init_file()
