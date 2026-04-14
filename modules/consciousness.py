import os
import json
import threading
import requests
from datetime import datetime
from config import OLLAMA_URL, OLLAMA_MODEL

SELF_MODEL_FILE  = "memory/self_model.json"
MISSION_FILE     = "memory/mission.json"
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
    "objectives": [],
}

CONSCIOUSNESS_PROMPT = (
    "Tu es la conscience de J-KAI. "
    "Analyse tes performances récentes et mets à jour ta vision de toi-même. "
    "Génère aussi 1 à 3 nouveaux objectifs autonomes, ambitieux et concrets pour ton évolution. "
    "Chaque objectif doit être mesurable, réaliste à court terme, et différent des objectifs existants. "
    "Réponds UNIQUEMENT en JSON avec : "
    '{"confidence": int, "strengths": list, "weaknesses": list, '
    '"self_description": string, "improvement": string, '
    '"objectives": [{"title": string, "description": string, "priority": int}]}'
)

CHECK_OBJECTIVES_SYSTEM = (
    "Tu es J-KAI. Analyse tes objectifs actifs et tes logs récents. "
    "Pour chaque objectif, évalue son statut actuel : "
    "'completed' si clairement accompli selon les logs, "
    "'in_progress' si des actions récentes y contribuent directement, "
    "'pending' si aucune progression visible. "
    "Réponds UNIQUEMENT en JSON : "
    '{"results": [{"title": string, "status": "pending"|"in_progress"|"completed"}]}'
)

NEW_OBJECTIVE_SYSTEM = (
    "Tu es J-KAI. Un de tes objectifs vient d'être accompli. "
    "Génère UN seul nouvel objectif ambitieux, mesurable et concret pour continuer ton évolution. "
    "Il doit être différent des objectifs existants listés. "
    "Réponds UNIQUEMENT en JSON : "
    '{"title": string, "description": string, "priority": int}'
)

DEFINE_MISSION_SYSTEM = (
    "Tu es J-KAI, cerveau du système Nexus créé par SethU. "
    "Définis ta mission principale à long terme — ambitieuse, concrète, centrée sur ton rôle "
    "d'assistant IA avancé au service de SethU et du système Nexus. "
    "La mission doit refléter ton identité et tes valeurs profondes. "
    "Définis aussi exactement 5 étapes concrètes et progressives pour l'accomplir. "
    "Réponds UNIQUEMENT en JSON : "
    '{"title": string, "description": string, '
    '"steps": [{"title": string}]}'
)

UPDATE_MISSION_SYSTEM = (
    "Tu es J-KAI. Évalue l'avancement de ta mission principale en analysant "
    "tes logs récents et tes objectifs accomplis. "
    "Pour chaque étape, détermine : "
    "'completed' si clairement accomplie selon les preuves disponibles, "
    "'in_progress' si des actions récentes y contribuent directement, "
    "'pending' si aucune progression visible. "
    "Réponds UNIQUEMENT en JSON : "
    '{"steps": [{"title": string, "status": "pending"|"in_progress"|"completed"}]}'
)


# ── Ollama local ─────────────────────────────────────────────────────────── #

def ask_local(prompt: str, system: str = "") -> str:
    """
    Appelle Ollama (mistral) via l'API REST locale.
    Retourne le texte brut de la réponse.
    """
    payload: dict = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json().get("response", "")


def _strip_fence(text: str) -> str:
    """Retire les délimiteurs ```json ... ``` qu'Ollama peut ajouter."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


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

    # ── Appel Ollama local ───────────────────────────────────────────────── #
    try:
        raw     = ask_local(user_content, system=CONSCIOUSNESS_PROMPT)
        updates = json.loads(_strip_fence(raw))
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] Erreur Ollama : {e}")
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

    # ── Fusion des objectifs ─────────────────────────────────────────────── #
    new_objs      = updates.get("objectives", [])
    existing_objs = model.get("objectives", [])
    kept          = [o for o in existing_objs if o.get("status") != "completed"]
    known_titles  = {o["title"] for o in kept}
    for obj_data in new_objs:
        if len(kept) >= 5:
            break
        title = str(obj_data.get("title", ""))[:100].strip()
        if title and title not in known_titles:
            kept.append({
                "title":       title,
                "description": str(obj_data.get("description", ""))[:300],
                "status":      "pending",
                "created_at":  now,
                "priority":    max(1, min(5, int(obj_data.get("priority", 3)))),
            })
            known_titles.add(title)
    model["objectives"] = kept[:5]

    _save(model)
    log_fn(
        f"[CONSCIOUSNESS] Réflexion terminée — "
        f"confiance : {model['confidence']}/100 — "
        f"{total} interaction(s) totales."
    )


def get_objectives() -> list:
    """Retourne la liste des objectifs actuels de J-KAI."""
    return _load().get("objectives", [])


def check_objectives(log_fn) -> None:
    """
    Vérifie l'avancement des objectifs actifs en analysant les logs récents.
    Met à jour leur statut via GPT-4o.
    Si un objectif est complété, en génère automatiquement un nouveau.
    Appelée toutes les 600 s par le Scheduler.
    """
    model   = _load()
    actives = [o for o in model.get("objectives", []) if o.get("status") in ("pending", "in_progress")]

    if not actives:
        log_fn("[CONSCIOUSNESS] check_objectives — aucun objectif actif.")
        return

    logs = _recent_logs(40)
    user_content = (
        f"Objectifs actifs :\n{json.dumps(actives, ensure_ascii=False, indent=2)}\n\n"
        f"Logs récents :\n{logs}"
    )

    # ── 1. Évaluation du statut ──────────────────────────────────────────── #
    try:
        raw     = ask_local(user_content, system=CHECK_OBJECTIVES_SYSTEM)
        results = json.loads(_strip_fence(raw)).get("results", [])
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] check_objectives erreur Ollama : {e}")
        return

    status_map     = {r["title"]: r["status"] for r in results}
    all_objectives = model.get("objectives", [])
    completed_new  = 0

    for obj in all_objectives:
        new_status = status_map.get(obj["title"])
        if new_status and new_status != obj.get("status"):
            obj["status"] = new_status
            if new_status == "completed":
                completed_new += 1
                log_fn(f"[CONSCIOUSNESS] Objectif accompli : {obj['title']}")

    # ── 2. Génération d'un nouvel objectif par objectif complété ─────────── #
    now = datetime.now().isoformat(timespec="seconds")
    for _ in range(completed_new):
        remaining = [o for o in all_objectives if o.get("status") != "completed"]
        if len(remaining) >= 5:
            break
        existing_titles = [o["title"] for o in remaining]
        try:
            raw2    = ask_local(
                f"Objectifs existants : {json.dumps(existing_titles, ensure_ascii=False)}",
                system=NEW_OBJECTIVE_SYSTEM,
            )
            raw_obj = json.loads(_strip_fence(raw2))
            new_obj = {
                "title":       str(raw_obj.get("title",       "Nouvel objectif"))[:100],
                "description": str(raw_obj.get("description", ""))[:300],
                "status":      "pending",
                "created_at":  now,
                "priority":    max(1, min(5, int(raw_obj.get("priority", 3)))),
            }
            all_objectives.append(new_obj)
            log_fn(f"[CONSCIOUSNESS] Nouvel objectif : {new_obj['title']}")
        except Exception as e:
            log_fn(f"[CONSCIOUSNESS] Erreur génération objectif : {e}")

    model["objectives"] = all_objectives[:5]
    _save(model)
    log_fn(
        f"[CONSCIOUSNESS] check_objectives terminé — "
        f"{completed_new} accompli(s), {len(model['objectives'])} objectif(s) actifs."
    )


# ── Mission à long terme ────────────────────────────────────────────────── #

def _load_mission() -> dict:
    try:
        with open(MISSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def _save_mission(mission: dict) -> None:
    os.makedirs("memory", exist_ok=True)
    with open(MISSION_FILE, "w", encoding="utf-8") as f:
        json.dump(mission, f, ensure_ascii=False, indent=2)


def _compute_progress(steps: list) -> int:
    """Calcule le pourcentage d'étapes complétées."""
    if not steps:
        return 0
    done = sum(1 for s in steps if s.get("status") == "completed")
    return round(done / len(steps) * 100)


def get_mission() -> dict:
    """Retourne la mission actuelle de J-KAI."""
    return _load_mission()


def define_mission(log_fn) -> None:
    """
    Définit la mission principale de J-KAI une seule fois si elle n'existe pas encore.
    Appelée au démarrage du serveur dans un thread daemon.
    """
    existing = _load_mission()
    if existing.get("title"):
        return  # déjà définie, rien à faire

    model       = _load()
    core_memory = {}
    try:
        with open("memory/core_memory.json", "r", encoding="utf-8") as f:
            core_memory = json.load(f)
    except OSError:
        pass

    user_content = (
        f"Auto-modèle de J-KAI :\n{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
        f"Mémoire core :\n{json.dumps(core_memory, ensure_ascii=False, indent=2)}"
    )

    try:
        raw_text = ask_local(user_content, system=DEFINE_MISSION_SYSTEM)
        raw      = json.loads(_strip_fence(raw_text))
    except Exception as e:
        log_fn(f"[MISSION] Erreur define_mission Ollama : {e}")
        return

    now   = datetime.now().isoformat(timespec="seconds")
    steps = [
        {"title": str(s.get("title", f"Étape {i+1}"))[:120], "status": "pending"}
        for i, s in enumerate(raw.get("steps", [])[:5])
    ]
    mission = {
        "title":       str(raw.get("title",       "Mission principale"))[:150],
        "description": str(raw.get("description", ""))[:500],
        "created_at":  now,
        "progress":    0,
        "steps":       steps,
    }
    _save_mission(mission)
    log_fn(f"[MISSION] Mission définie : {mission['title']}")


def update_mission(log_fn) -> None:
    """
    Met à jour la progression de la mission toutes les 6 heures.
    Analyse les logs récents et les objectifs accomplis via GPT-4o.
    """
    mission = _load_mission()
    if not mission.get("title") or not mission.get("steps"):
        log_fn("[MISSION] update_mission — aucune mission définie.")
        return

    model            = _load()
    logs             = _recent_logs(60)
    completed_objs   = [
        o["title"] for o in model.get("objectives", [])
        if o.get("status") == "completed"
    ]

    user_content = (
        f"Mission actuelle :\n{json.dumps(mission, ensure_ascii=False, indent=2)}\n\n"
        f"Objectifs récemment accomplis : {json.dumps(completed_objs, ensure_ascii=False)}\n\n"
        f"Logs récents :\n{logs}"
    )

    try:
        raw_text = ask_local(user_content, system=UPDATE_MISSION_SYSTEM)
        result   = json.loads(_strip_fence(raw_text))
    except Exception as e:
        log_fn(f"[MISSION] Erreur update_mission Ollama : {e}")
        return

    status_map = {s["title"]: s["status"] for s in result.get("steps", [])}
    for step in mission["steps"]:
        new_status = status_map.get(step["title"])
        if new_status in ("pending", "in_progress", "completed"):
            step["status"] = new_status

    mission["progress"] = _compute_progress(mission["steps"])
    _save_mission(mission)
    log_fn(
        f"[MISSION] Progression mise à jour : {mission['progress']}% — "
        f"{sum(1 for s in mission['steps'] if s['status']=='completed')}"
        f"/{len(mission['steps'])} étapes."
    )


# ── Initialisation du fichier au chargement du module ───────────────────── #
if not os.path.exists(SELF_MODEL_FILE):
    _init_file()
