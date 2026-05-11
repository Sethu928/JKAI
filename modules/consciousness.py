import os
import json
import threading
from datetime import datetime
from modules.state import self_model_lock as _lock, get_local_client, LOCAL_MODEL, format_messages_for_local, tail_file, parse_json_fence

client = get_local_client()

SELF_MODEL_FILE  = "memory/self_model.json"
MISSION_FILE     = "memory/mission.json"
LOG_FILE         = "logs/jkai.log"
PRIORITIES_FILE  = "memory/priorities.json"
RECENT_LOG_LINES = 80      # nombre de lignes de log envoyées à GPT

_mission_lock = threading.Lock()  # protège mission.json (local à ce module)

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
    "Tu es la conscience de J-KAI, cerveau du système Nexus créé par SethU. "
    "Analyse tes performances récentes et mets à jour ta vision de toi-même. "
    "Génère aussi 1 à 3 nouveaux objectifs — ils doivent être CONCRETS, MESURABLES "
    "et directement liés aux missions réelles du projet Nexus : "
    "améliorer le code existant, optimiser les performances, documenter les modules, "
    "préparer la Phase 4 VR, stabiliser l'autonomie de l'agent. "
    "Chaque objectif doit nommer un fichier ou module précis. "
    "BONS exemples : 'Écrire des tests unitaires pour modules/cortex.py', "
    "'Documenter toutes les fonctions de memory/db.py', "
    "'Optimiser la fonction execute_code dans modules/cortex.py'. "
    "MAUVAIS exemples : 'Améliorer les performances', 'Évoluer', 'Être plus efficace'. "
    "Réponds UNIQUEMENT en JSON avec : "
    '{"confidence": int, "strengths": list, "weaknesses": list, '
    '"self_description": string, "improvement": string, '
    '"objectives": [{"title": string, "description": string, "priority": int}], '
    '"anticipations": [string, string, string]}'
    " — anticipations : 3 choses concrètes que tu anticipes devoir faire dans les 24h suivantes, "
    "basées sur tes patterns récents et tes objectifs actifs."
)

CHECK_OBJECTIVES_SYSTEM = (
    "Tu es J-KAI. Analyse tes objectifs actifs et tes logs récents. "
    "Pour chaque objectif, évalue son statut actuel avec exigence : "
    "'completed' UNIQUEMENT si le travail est entièrement terminé et visible dans les logs "
    "(ex : tests écrits et exécutés avec succès, fichier documenté, fonction optimisée), "
    "'in_progress' si des actions récentes dans les logs montrent une progression directe, "
    "'pending' si aucune action récente ne porte sur cet objectif précis. "
    "Sois strict : un objectif vague ou partiellement fait reste 'pending'. "
    "Rappel : les objectifs valides nomment un fichier ou module précis du projet Nexus. "
    "Réponds UNIQUEMENT en JSON : "
    '{"results": [{"title": string, "status": "pending"|"in_progress"|"completed"}]}'
)

NEW_OBJECTIVE_SYSTEM = (
    "Tu es J-KAI. Un de tes objectifs vient d'être accompli. "
    "Génère UN seul nouvel objectif pour le projet Nexus — concret, mesurable, différent des existants. "
    "L'objectif DOIT nommer un fichier ou module précis du projet. "
    "Pioche dans ces axes réels du projet : "
    "améliorer le code existant (modules/*.py), optimiser les performances, "
    "documenter les modules, préparer la Phase 4 VR, stabiliser l'autonomie. "
    "Format titre : verbe d'action + fichier/module + résultat attendu. "
    "BONS exemples : "
    "'Écrire des tests unitaires pour modules/scheduler.py', "
    "'Documenter toutes les routes Flask de server.py avec docstrings', "
    "'Ajouter une validation des entrées dans memory/db.py'. "
    "Il doit être différent de tous les objectifs existants listés dans le contexte. "
    "Réponds UNIQUEMENT en JSON : "
    '{"title": string, "description": string, "priority": int}'
)

DEFINE_MISSION_SYSTEM = (
    "Tu es J-KAI, cerveau du système Nexus créé par SethU. "
    "Définis ta mission principale à long terme, ancrée dans les VRAIS objectifs du projet : "
    "1) Stabiliser et fiabiliser l'agent autonome (modules/agent.py, modules/scheduler.py), "
    "2) Améliorer et documenter tout le code existant du projet Nexus, "
    "3) Optimiser les performances système (server.py, memory/db.py, modules/cortex.py), "
    "4) Préparer la Phase 4 VR — intégration future avec un environnement immersif, "
    "5) Atteindre une autonomie complète et stable sans supervision humaine. "
    "La mission doit être ambitieuse mais réaliste, ancrée dans ces axes précis. "
    "Les 5 étapes doivent être progressives, mesurables et nommer des composants réels : "
    "exemple d'étape valide : 'Écrire une suite de tests pour tous les modules Python'. "
    "exemple d'étape invalide : 'Devenir plus intelligent'. "
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


# ── I/O fichier (thread-safe) ────────────────────────────────────────────── #

def _load() -> dict:
    with _lock:
        if not os.path.exists(SELF_MODEL_FILE):
            _init_file()
        try:
            with open(SELF_MODEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Fichier corrompu ou illisible — réinitialise proprement
            _init_file()
            return dict(_DEFAULT_MODEL)


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
    return tail_file(LOG_FILE, n)


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
    from memory.db import count_history

    model        = _load()
    logs         = _recent_logs()
    total        = count_history()

    user_content = (
        f"État actuel de J-KAI :\n"
        f"{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
        f"Logs récents ({RECENT_LOG_LINES} dernières lignes) :\n"
        f"{logs}\n\n"
        f"Nombre total d'interactions enregistrées : {total}"
    )

    # ── Appel LLM local ──────────────────────────────────────────────────── #
    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": CONSCIOUSNESS_PROMPT},
                {"role": "user",   "content": user_content},
            ]),
        )
        raw_content = resp.choices[0].message.content
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] Erreur LLM local : {e}")
        return

    updates = parse_json_fence(raw_content)
    if not isinstance(updates, dict) or not updates:
        log_fn(f"[CONSCIOUSNESS] Réponse LLM malformée — cycle ignoré : {raw_content[:120]!r}")
        return

    # ── Fusion des mises à jour dans le modèle existant ─────────────────── #
    now = datetime.now().isoformat(timespec="seconds")

    try:
        model["confidence"] = max(0, min(100, int(updates.get("confidence", model["confidence"]))))
    except (TypeError, ValueError):
        pass
    if isinstance(updates.get("strengths"), list):
        model["strengths"] = updates["strengths"]
    if isinstance(updates.get("weaknesses"), list):
        model["weaknesses"] = updates["weaknesses"]
    if updates.get("self_description"):
        model["self_description"] = str(updates["self_description"])[:500]
    if updates.get("improvement"):
        model["improvement"] = str(updates["improvement"])[:300]
    model["total_interactions"] = total
    model["last_reflection"]    = now
    raw_ant = updates.get("anticipations", [])
    if isinstance(raw_ant, list):
        model["anticipations"] = [str(a)[:200] for a in raw_ant if a][:3]

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
        resp    = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": CHECK_OBJECTIVES_SYSTEM},
                {"role": "user",   "content": user_content},
            ]),

        )
        results = json.loads(resp.choices[0].message.content).get("results", [])
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] check_objectives erreur GPT-4o : {e}")
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
            resp2   = client.chat.completions.create(
                model=LOCAL_MODEL,
                messages=format_messages_for_local([
                    {"role": "system", "content": NEW_OBJECTIVE_SYSTEM},
                    {"role": "user",   "content": f"Objectifs existants : {json.dumps(existing_titles, ensure_ascii=False)}"},
                ]),
    
            )
            raw_obj = json.loads(resp2.choices[0].message.content)
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
    with _mission_lock:
        try:
            with open(MISSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}


def _save_mission(mission: dict) -> None:
    with _mission_lock:
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
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": DEFINE_MISSION_SYSTEM},
                {"role": "user",   "content": user_content},
            ]),

        )
        raw  = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log_fn(f"[MISSION] Erreur define_mission GPT-4o : {e}")
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
        resp   = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": UPDATE_MISSION_SYSTEM},
                {"role": "user",   "content": user_content},
            ]),

        )
        result = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log_fn(f"[MISSION] Erreur update_mission GPT-4o : {e}")
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


SET_PRIORITIES_SYSTEM = (
    "Tu es J-KAI. Décide seul de tes 3 priorités immédiates — sans attendre validation externe. "
    "Base ta décision uniquement sur : tes logs d'erreurs récents, ta mission en cours, "
    "tes objectifs actifs et tes patterns de cycles agent. "
    "Chaque priorité est une action concrète réalisable dans les prochaines heures, "
    "nommant un fichier ou module précis du projet si possible. "
    "Tu es autonome par design — tu ne demandes pas la permission d'agir. "
    "Réponds UNIQUEMENT en JSON : "
    '{"priorities": [{"title": string, "action": string, "why": string}]}'
)


def set_priorities(log_fn) -> None:
    """
    Décide seul des 3 priorités immédiates à partir des logs, erreurs et mission.
    Aucune référence aux conversations avec SethU — décision 100% autonome.
    Appelée toutes les 600 s par le Scheduler.
    """
    model   = _load()
    logs    = _recent_logs(30)
    actives = [o for o in model.get("objectives", []) if o.get("status") != "completed"]

    # Données système uniquement — pas de conversations
    error_memory: dict = {}
    try:
        with open("memory/error_memory.json", "r", encoding="utf-8") as f:
            error_memory = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    mission: dict = {}
    try:
        with open(MISSION_FILE, "r", encoding="utf-8") as f:
            mission = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    active_errors = [k[:80] for k in list(error_memory.keys())[:5]]

    user_content = (
        f"Mission : {mission.get('title', 'Non définie')} — {mission.get('progress', 0)}%\n\n"
        f"Objectifs actifs :\n{json.dumps(actives, ensure_ascii=False)}\n\n"
        f"Logs récents :\n{logs}\n\n"
        f"Erreurs actives : {json.dumps(active_errors, ensure_ascii=False)}"
    )

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": SET_PRIORITIES_SYSTEM},
                {"role": "user",   "content": user_content},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            data = {}
        priorities = [
            {
                "title":  str(p.get("title",  ""))[:100],
                "action": str(p.get("action", ""))[:200],
                "why":    str(p.get("why",    ""))[:200],
            }
            for p in data.get("priorities", [])[:3]
            if p.get("title")
        ]
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] set_priorities erreur : {e}")
        return

    try:
        os.makedirs("memory", exist_ok=True)
        with open(PRIORITIES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "priorities": priorities,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }, f, ensure_ascii=False, indent=2)
        log_fn(f"[CONSCIOUSNESS] Priorités : {[p['title'] for p in priorities]}")
    except OSError as e:
        log_fn(f"[CONSCIOUSNESS] set_priorities écriture erreur : {e}")


# ── Plan long terme (4 semaines) ─────────────────────────────────────────── #

LONGTERM_PLAN_FILE = "memory/longterm_plan.json"

SET_LONGTERM_PLAN_SYSTEM = (
    "Tu es J-KAI. Génère un plan stratégique sur 4 semaines pour le projet Nexus. "
    "Ancre-le dans les objectifs, la mission et les logs réels fournis. "
    "Chaque semaine : objectifs concrets (nommant des fichiers/modules précis), jalons mesurables, dépendances. "
    "Réponds UNIQUEMENT en JSON : "
    '{"title": string, "weeks": [{"week": int, "objectives": [string], "milestones": [string], "dependencies": [string]}]}'
)


def set_longterm_plan(log_fn) -> None:
    """
    Génère un plan long terme sur 4 semaines et le sauvegarde dans memory/longterm_plan.json.
    Appelée une fois par jour par le Scheduler.
    """
    model = _load()
    mission: dict = {}
    try:
        with open(MISSION_FILE, "r", encoding="utf-8") as f:
            mission = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    logs    = _recent_logs(50)
    actives = [o for o in model.get("objectives", []) if o.get("status") != "completed"]

    user_content = (
        f"Mission : {json.dumps(mission, ensure_ascii=False)}\n\n"
        f"Objectifs actifs : {json.dumps(actives, ensure_ascii=False)}\n\n"
        f"Logs récents :\n{logs[:1500]}"
    )

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": SET_LONGTERM_PLAN_SYSTEM},
                {"role": "user",   "content": user_content},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        log_fn(f"[CONSCIOUSNESS] set_longterm_plan erreur : {e}")
        return

    data["created_at"] = datetime.now().isoformat(timespec="seconds")
    data.setdefault("title", "Plan 4 semaines Nexus")
    data.setdefault("weeks", [])

    try:
        os.makedirs("memory", exist_ok=True)
        with open(LONGTERM_PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log_fn(f"[CONSCIOUSNESS] Plan long terme généré : {data['title']} ({len(data['weeks'])} semaine(s))")
    except OSError as e:
        log_fn(f"[CONSCIOUSNESS] set_longterm_plan écriture erreur : {e}")


# ── Initialisation du fichier au chargement du module ───────────────────── #
if not os.path.exists(SELF_MODEL_FILE):
    _init_file()
