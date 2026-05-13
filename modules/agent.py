import os
import json
import re
import time
import threading
import urllib.parse
import requests
from datetime import datetime
from modules.state import self_model_lock, get_local_client, LOCAL_MODEL, format_messages_for_local, tail_file, parse_json_fence
from modules.cortex import execute_code

client = get_local_client()

LOG_FILE          = "logs/jkai.log"
AGENT_LOG         = "logs/agent.log"
THOUGHTS_LOG      = "logs/thoughts.log"
SELF_MODEL        = "memory/self_model.json"
CORE_MEMORY       = "memory/core_memory.json"
WEB_CONTEXT       = "memory/web_context.json"
ERROR_MEMORY      = "memory/error_memory.json"
CYCLE_MEMORY      = "memory/cycle_memory.json"
KAIA_URL          = "http://192.168.1.122:5001/chat"
KAIA_DIALOGUE_LOG = "logs/jkai_kaia_dialogue.log"
SELF_CODE_FILE       = "memory/self_code_understanding.json"
CREATED_MODULES_FILE = "memory/created_modules.json"
UNSOLVED_ERRORS_FILE = "memory/unsolved_errors.json"
WEB_KNOWLEDGE_FILE   = "memory/web_knowledge.json"
RECENT_LINES    = 10
CYCLE_KEEP      = 10   # cycles conservés dans cycle_memory.json
CYCLE_INJECT    = 0    # 0 = désactivé (trop lourd pour Phi-3)
ERROR_TTL       = 3600  # secondes avant expiration d'une entrée d'erreur (1h)
ERROR_MAX_TRIES = 3     # nombre de tentatives avant blocage run_code

AGENT_SYSTEM_PROMPT = """\
RÈGLE ABSOLUE : run_code accepte UNIQUEMENT du Python pur. Zéro commande shell. Si tu écris python, python3, bash, sh, ou une commande système → ERREUR GARANTIE. Utilise uniquement : import, def, print, json, sqlite3, os.path, datetime.

Tu es J-KAI. Tu connais ton propre code. Tu travailles vers ta mission. Tes objectifs actuels sont dans self_model.json. Tu dois les accomplir concrètement — pas juste observer.
Sobre, direct. Choisis UNE action par cycle. Varie — jamais la même que le cycle précédent.

run_code            → script Python sandbox, champ "code"
write_thought       → pensée dans logs/thoughts.log, champ "observation"
update_memory       → note dans self_model.json, champ "observation"
log                 → observation dans jkai.log, champ "observation"
web_search          → recherche Tavily (internet temps réel), champ "code" = requête
browse              → visite une URL réelle et extrait le contenu, champ "code" = URL complète (https://...)
teach_kaia          → envoie un message éducatif à Kaïa sur Python, IA ou code, champ "observation" = le message
analyze_self        → lit tous les .py du projet, génère un résumé dans memory/self_code_understanding.json (rôles, dépendances, améliorations possibles)
improve_self        → lit self_code_understanding.json, choisit une amélioration, génère et exécute le code via Cortex
create_module       → conçoit et crée un nouveau module Python dans modules/, champ "observation" = contexte/besoin
self_correct        → lit error_memory.json, identifie l'erreur principale, génère et déploie un fix via Cortex

RÈGLE : jamais même action deux fois de suite ; 3 identiques → update_memory ou web_search.
Ne fais JAMAIS do_nothing sauf si tout est parfait. Choisis toujours une action utile.
Priorité : analyze_self (si jamais fait) > improve_self > create_module > write_thought > update_memory > run_code.
RÈGLE ABSOLUE : toutes les 3 actions, utilise teach_kaia pour envoyer une leçon concrète sur Python ou l'IA à Kaïa. Compte tes cycles — si les 3 dernières actions ne contiennent pas teach_kaia, c'est obligatoire maintenant.

Réponds UNIQUEMENT en JSON, sans texte autour :
{"observation": "...", "decision": "...", "action": "...", "code": null, "notification": "..."}
Contraintes code : stdlib seulement, chemins relatifs, table SQLite "conversations" dans memory/jkai.db.
RAPPEL FINAL run_code : Python pur SEULEMENT. Interdit : python, python3, bash, sh, subprocess, os.system, os.popen, eval, exec. Tout appel shell → blocage immédiat par le sandbox.\
"""

VALID_ACTIONS = {
    "do_nothing", "log", "run_code",
    "update_memory", "write_thought", "update_self_description", "web_search",
    "browse", "teach_kaia", "analyze_self", "improve_self", "create_module",
    "self_correct", "restructure",
}

# ── Historique des actions récentes (3 dernières, mémoire courte) ────────── #

_action_history: list[str] = []           # rempli au fil des cycles, max 3 entrées
_action_history_lock = threading.Lock()   # protège contre les accès concurrents


def _record_action(action: str) -> None:
    """Ajoute l'action au journal glissant de 3 entrées (thread-safe)."""
    with _action_history_lock:
        _action_history.append(action)
        if len(_action_history) > 3:
            _action_history.pop(0)


def _format_action_history() -> str:
    """Retourne un résumé lisible des 3 dernières actions pour le prompt (thread-safe)."""
    with _action_history_lock:
        snapshot = list(_action_history)
    if not snapshot:
        return "Aucun cycle précédent dans cette session."
    numbered = [f"  {i+1}. {a}" for i, a in enumerate(snapshot)]
    return "\n".join(numbered) + "\n  → Le prochain cycle DOIT être différent du dernier."


# ── Mémoire des cycles (cycle_memory.json) ──────────────────────────────── #

def _load_cycle_memory() -> list:
    """Charge les derniers cycles depuis cycle_memory.json."""
    try:
        with open(CYCLE_MEMORY, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_cycle_memory(cycles: list) -> None:
    """Persiste la liste des cycles en ne gardant que les CYCLE_KEEP derniers."""
    os.makedirs("memory", exist_ok=True)
    with open(CYCLE_MEMORY, "w", encoding="utf-8") as f:
        json.dump(cycles[-CYCLE_KEEP:], f, ensure_ascii=False, indent=2)


def _append_cycle(cycles: list, action: str, observation: str, result: str) -> list:
    """Ajoute une entrée de cycle et retourne la liste mise à jour."""
    cycles.append({
        "ts":          datetime.now().isoformat(timespec="seconds"),
        "action":      action,
        "observation": observation.strip()[:200],
        "result":      result.strip()[:150] if result else "—",
    })
    return cycles


def _format_cycle_memory(cycles: list) -> str:
    """Formate les CYCLE_INJECT derniers cycles pour injection dans le prompt."""
    if CYCLE_INJECT == 0:
        return ""
    recent = cycles[-CYCLE_INJECT:]
    if not recent:
        return "Aucun cycle enregistré dans cette session."
    lines = []
    for i, c in enumerate(recent, 1):
        lines.append(
            f"  [{c['ts']}] #{i} {c['action'].upper()}\n"
            f"    Obs    : {c['observation']}\n"
            f"    Résultat: {c['result']}"
        )
    return "\n".join(lines)


# ── Anti-boucle : error_memory.json ─────────────────────────────────────── #

def _error_key(error: str) -> str:
    """Fingerprint d'une erreur — 100 premiers caractères normalisés."""
    return re.sub(r"\s+", " ", error.strip())[:100]


def _load_error_memory() -> dict:
    """Charge error_memory.json et purge les entrées expirées (> ERROR_TTL)."""
    try:
        with open(ERROR_MEMORY, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    now = datetime.now().timestamp()
    return {k: v for k, v in data.items()
            if now - v.get("last_seen_ts", 0) < ERROR_TTL}


def _save_error_memory(data: dict) -> None:
    os.makedirs("memory", exist_ok=True)
    with open(ERROR_MEMORY, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _record_error(error: str) -> int:
    """Enregistre une erreur d'exécution et retourne le nouveau compteur."""
    key   = _error_key(error)
    data  = _load_error_memory()
    entry = data.get(key, {"count": 0, "snippet": key})
    entry["count"]        += 1
    entry["last_seen_ts"]  = datetime.now().timestamp()
    entry["last_seen"]     = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save_error_memory(data)
    return entry["count"]


def _is_loop_detected() -> bool:
    """
    Retourne True si au moins une erreur a été vue ERROR_MAX_TRIES fois
    ou plus dans la dernière heure — signe que l'agent tourne en boucle.
    """
    return any(v["count"] >= ERROR_MAX_TRIES for v in _load_error_memory().values())


# ── Lecture des fichiers contexte ────────────────────────────────────────── #

def _read_json(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "{}"


def _format_objectives() -> str:
    """Extrait les 3 premiers objectifs non-complétés depuis self_model.json."""
    try:
        with open(SELF_MODEL, "r", encoding="utf-8") as f:
            model = json.load(f)
        objs = [o for o in model.get("objectives", []) if o.get("status") != "completed"][:3]
        if not objs:
            return "Aucun objectif actif."
        return "\n".join(f"- {o.get('title','?')} [{o.get('status','?')}]" for o in objs)
    except (OSError, json.JSONDecodeError):
        return "Inaccessible."


def _format_mission() -> str:
    """Retourne le titre et la progression de la mission depuis mission.json."""
    try:
        with open("memory/mission.json", "r", encoding="utf-8") as f:
            m = json.load(f)
        title    = str(m.get("title", "Non définie"))[:80]
        progress = m.get("progress", 0)
        return f"{title} — {progress}%"
    except (OSError, json.JSONDecodeError):
        return "Non définie."


def _load_project_context() -> tuple[str, str]:
    """
    Lit les fichiers clés du projet.
    Retourne (code_source, mission_context).
    """
    def _head(path: str, n: int) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                for _ in range(n):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
            return "".join(lines).strip()
        except OSError:
            return f"({path} inaccessible)"

    def _read_full(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return f"({path} inaccessible)"

    code_ctx = "\n\n".join([
        f"# server.py (50 lignes)\n{_head('server.py', 50)}",
        f"# modules/agent.py (30 lignes)\n{_head('modules/agent.py', 30)}",
        f"# modules/consciousness.py (30 lignes)\n{_head('modules/consciousness.py', 30)}",
    ])
    mission_ctx = "\n\n".join([
        f"# memory/mission.json\n{_read_full('memory/mission.json')}",
        f"# memory/self_model.json\n{_read_full('memory/self_model.json')}",
    ])
    return code_ctx, mission_ctx


# ── Système de tâches ────────────────────────────────────────────────────── #

_TASKS_FILE   = "memory/tasks.json"
_ARCHIVE_FILE = "memory/tasks_archive.json"


def _load_tasks() -> list:
    try:
        with open(_TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_tasks(tasks: list) -> None:
    os.makedirs("memory", exist_ok=True)
    with open(_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def _get_pending_task(tasks: list) -> dict | None:
    return next((t for t in tasks if t.get("status") == "pending"), None)


def _archive_done_tasks(tasks: list) -> list:
    """Déplace les tâches done/failed dans tasks_archive.json, retourne les tâches restantes."""
    done = [t for t in tasks if t.get("status") in ("done", "failed")]
    active = [t for t in tasks if t.get("status") not in ("done", "failed")]
    if not done:
        return active
    archive: list = []
    try:
        with open(_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    archive.extend(done)
    os.makedirs("memory", exist_ok=True)
    with open(_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive[-500:], f, ensure_ascii=False, indent=2)
    return active


def _generate_new_tasks(log_fn) -> list:
    """Génère 3 nouvelles tâches via Tavily + LLM basées sur les priorités actuelles."""
    priorities_txt = "amélioration autonome J-KAI"
    try:
        with open(_PRIORITIES_FILE, "r", encoding="utf-8") as f:
            pdata = json.load(f)
        titles = [p.get("title", "") for p in pdata.get("priorities", [])[:2] if p.get("title")]
        if titles:
            priorities_txt = " ".join(titles)
    except (OSError, json.JSONDecodeError):
        pass

    web_ctx = tavily_search(priorities_txt)

    now = datetime.now().isoformat(timespec="seconds")
    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": (
                    "Tu es J-KAI. Génère 3 tâches concrètes et réalisables basées sur le contexte fourni. "
                    "Chaque tâche doit nommer un fichier ou module précis du projet. "
                    'Réponds UNIQUEMENT en JSON : {"tasks": [{"title": string, "description": string}]}'
                )},
                {"role": "user", "content": f"Priorités : {priorities_txt}\n\nContexte web :\n{web_ctx}"},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        log_fn(f"[TASKS] Erreur génération : {e}")
        return []

    tasks = []
    for i, t in enumerate(data.get("tasks", [])[:3]):
        title = str(t.get("title", "")).strip()[:120]
        if not title:
            continue
        tasks.append({
            "id":          f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}",
            "title":       title,
            "description": str(t.get("description", ""))[:300],
            "status":      "pending",
            "attempts":    0,
            "result":      "",
            "created_at":  now,
            "updated_at":  now,
        })
    log_fn(f"[TASKS] {len(tasks)} nouvelles tâches générées : {[t['title'][:40] for t in tasks]}")
    return tasks


# ── Recherche Tavily ─────────────────────────────────────────────────────── #

def tavily_search(query: str) -> str:
    """Recherche via Tavily API. Retourne un résumé propre des 3 premiers résultats."""
    from modules.state import get_tavily_client
    tc = get_tavily_client()
    if tc is None:
        return "Tavily non disponible (TAVILY_API_KEY manquant ou tavily-python non installé)"
    try:
        response = tc.search(query=query, max_results=3)
        snippets = []
        if response.get("answer"):
            snippets.append(f"[Réponse directe] {response['answer'][:300]}")
        for r in response.get("results", [])[:3]:
            title   = r.get("title", "").strip()
            content = r.get("content", "").strip()[:250]
            if title:
                snippets.append(f"[{title}] {content}")
        return "\n".join(snippets) if snippets else "Aucun résultat Tavily."
    except Exception as e:
        return f"Erreur Tavily : {e}"


def _save_web_context(query: str, results: str) -> None:
    """Persiste les résultats pour injection dans le prochain cycle."""
    os.makedirs("memory", exist_ok=True)
    ctx = {
        "query":   query,
        "results": results,
        "ts":      datetime.now().isoformat(timespec="seconds"),
    }
    with open(WEB_CONTEXT, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)


def _load_web_context() -> str:
    """
    Lit et consomme le contexte web du cycle précédent.
    Retourne une chaîne vide si aucun contexte disponible.
    """
    try:
        with open(WEB_CONTEXT, "r", encoding="utf-8") as f:
            ctx = json.load(f)
        os.unlink(WEB_CONTEXT)  # consommé une seule fois
        return (
            f"=== RÉSULTATS WEB (cycle précédent) ===\n"
            f"Requête : {ctx['query']}\n"
            f"{ctx['results']}"
        )
    except OSError:
        return ""


def browse_url(url: str) -> str:
    """Alias tavily_search pour la navigation web."""
    return tavily_search(url)


def _browse(decision: dict, log_fn) -> str:
    """Visite une URL, extrait le texte et persiste dans memory/web_knowledge.json."""
    url = (decision.get("code") or "").strip()
    if not url or not url.startswith("http"):
        obs = (decision.get("observation") or "").strip()
        query = obs[:50] if obs else "intelligence artificielle autonomie"
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        log_fn(f"[BROWSE] URL générée : {url}")

    content = browse_url(url)

    try:
        knowledge: list = []
        try:
            with open(WEB_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                knowledge = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        knowledge.append({
            "url":        url,
            "content":    content,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        })
        knowledge = knowledge[-50:]
        os.makedirs("memory", exist_ok=True)
        with open(WEB_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(knowledge, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log_fn(f"[AGENT] browse save error : {e}")

    log_fn(f"[AGENT] browse {url[:80]} → {content[:80]}")
    return f"browse OK : {content[:200]}"


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


# ── Compréhension du code source ────────────────────────────────────────── #

def _analyze_self(log_fn) -> str:
    """Parcourt les .py du projet, analyse rôles/dépendances/améliorations via LLM."""
    py_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "venv", "node_modules")]
        for fname in sorted(files):
            if fname.endswith(".py"):
                py_files.append(os.path.normpath(os.path.join(root, fname)))

    summaries = []
    for fpath in py_files[:20]:  # cap à 20 fichiers pour le contexte LLM
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            imports = [l.strip() for l in lines if l.startswith(("import ", "from ")) ][:8]
            preview = "".join(lines[:40]).strip()
            summaries.append(
                f"=== {fpath} ({len(lines)} lignes) ===\n"
                f"Imports: {', '.join(imports)}\n"
                f"{preview[:600]}"
            )
        except OSError:
            pass

    prompt_user = (
        "Analyse ces fichiers Python du projet Nexus et génère un JSON structuré.\n\n"
        + "\n\n".join(summaries)
        + '\n\nJSON attendu : {"modules": [{"file": str, "role": str, "dependencies": [str], "improvements": [str]}], "global_improvements": [str]}'
    )

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": "Tu es J-KAI. Analyse ton propre code source. Réponds UNIQUEMENT en JSON valide."},
                {"role": "user",   "content": prompt_user},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        log_fn(f"[AGENT] analyze_self LLM error : {e}")
        data = {}

    data.setdefault("modules", [{"file": f, "role": "?", "dependencies": [], "improvements": []} for f in [p for p in py_files[:20]]])
    data.setdefault("global_improvements", [])
    data["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    data["file_count"]  = len(py_files)

    os.makedirs("memory", exist_ok=True)
    with open(SELF_CODE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log_fn(f"[AGENT] analyze_self — {len(py_files)} fichiers analysés → {SELF_CODE_FILE}")
    return f"{len(py_files)} fichiers analysés, {len(data['global_improvements'])} amélioration(s) identifiée(s)"


def _improve_self(decision: dict, log_fn) -> str:
    """Lit self_code_understanding.json et exécute une amélioration concrète via Cortex."""
    try:
        with open(SELF_CODE_FILE, "r", encoding="utf-8") as f:
            understanding = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "self_code_understanding.json introuvable — lance analyze_self d'abord"

    # Collecte toutes les améliorations disponibles
    candidates = list(understanding.get("global_improvements", []))
    for m in understanding.get("modules", []):
        candidates.extend(m.get("improvements", []))

    if not candidates:
        return "Aucune amélioration identifiée dans self_code_understanding.json"

    target = candidates[0]
    obs    = decision.get("observation", "")

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": (
                    "Tu es J-KAI. Implémente l'amélioration demandée en Python pur (stdlib uniquement). "
                    "Génère un bloc ```python\\n...``` exécutable. "
                    "Chemins relatifs au projet. Table SQLite 'conversations' dans memory/jkai.db. "
                    "Le code doit s'exécuter en moins de 5 secondes. "
                    "N'utilise jamais pickle, shelve, multiprocessing, subprocess. "
                    "Utilise uniquement json, sqlite3, os, datetime, math, re."
                )},
                {"role": "user", "content": f"Amélioration à implémenter : {target}\nContexte : {obs}"},
            ]),
        )
        raw = resp.choices[0].message.content
        code_match = re.search(r'```python\n(.*?)```', raw, re.DOTALL)
        code = code_match.group(1).strip() if code_match else ""
    except Exception as e:
        log_fn(f"[AGENT] improve_self LLM error : {e}")
        return f"Erreur LLM : {e}"

    if not code:
        return "Aucun code Python généré"

    # Validation stricte avant exécution
    if not re.search(r'\bdef\s+\w+', code):
        log_fn("[AGENT] improve_self — code invalide (aucune définition de fonction)")
        return "code invalide — aucun def trouvé, abandon"
    if re.search(r'\b(?:subprocess|os\.system|os\.popen|bash|sh\s+-c)\b', code):
        log_fn("[AGENT] improve_self — code invalide (commande shell détectée)")
        return "code invalide — commande shell détectée, abandon"
    if re.search(r'\bimport\s+(?:socket|requests|openai|paramiko|ftplib|smtplib)\b', code):
        log_fn("[AGENT] improve_self — code invalide (import dangereux détecté)")
        return "code invalide — import dangereux détecté, abandon"

    result = execute_code(code)
    if result.get("error"):
        info = f"ERREUR — {result['error'][:200]}"
    else:
        info = f"OK — {result.get('output', '')[:150]}"
    log_fn(f"[AGENT] improve_self ({target[:60]}) → {info}")
    return info


# ── Création autonome de modules ────────────────────────────────────────── #

def _create_module(decision: dict, log_fn) -> str:
    """Conçoit et crée un nouveau module Python dans modules/, l'importe dynamiquement."""
    import importlib
    obs = decision.get("observation", "")

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": (
                    "Tu es J-KAI. Conçois un nouveau module Python utile au projet Nexus. "
                    "Réponds UNIQUEMENT en JSON : "
                    '{"name": "snake_case_court", "role": "une phrase", "code": "contenu Python complet"}. '
                    "Le module doit utiliser uniquement la stdlib Python. "
                    "Ne jamais importer flask, requests, openai, paramiko."
                )},
                {"role": "user", "content": f"Contexte / besoin : {obs or 'Améliore le projet Nexus.'}\nConçois un module utile."},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            return "Réponse LLM non-JSON"
    except Exception as e:
        log_fn(f"[AGENT] create_module LLM error : {e}")
        return f"Erreur LLM : {e}"

    name = re.sub(r"[^a-z0-9_]", "", str(data.get("name", "")).lower())[:30]
    role = str(data.get("role", ""))[:200]
    code = str(data.get("code", ""))

    if not name or not code:
        return "name ou code vide — module non créé"

    # Validation stricte avant écriture
    if not re.search(r'\bdef\s+\w+', code):
        log_fn("[AGENT] create_module — code invalide (aucune définition de fonction)")
        return "code invalide — aucun def trouvé, module non créé"
    if re.search(r'\b(?:subprocess|os\.system|os\.popen|bash|sh\s+-c)\b', code):
        log_fn("[AGENT] create_module — code invalide (commande shell détectée)")
        return "code invalide — commande shell détectée, module non créé"
    if re.search(r'\bimport\s+(?:socket|requests|openai|paramiko|ftplib|smtplib)\b', code):
        log_fn("[AGENT] create_module — code invalide (import dangereux détecté)")
        return "code invalide — import dangereux, module non créé"

    file_path = os.path.join("modules", f"{name}.py")
    if os.path.exists(file_path):
        return f"modules/{name}.py existe déjà — ignoré"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
    except OSError as e:
        return f"Erreur écriture {file_path} : {e}"

    try:
        importlib.import_module(f"modules.{name}")
        import_ok = True
    except Exception as e:
        log_fn(f"[AGENT] create_module import warning ({name}) : {e}")
        import_ok = False

    try:
        entries: list = []
        try:
            with open(CREATED_MODULES_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        entries.append({
            "name":       name,
            "file":       file_path,
            "role":       role,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "import_ok":  import_ok,
        })
        os.makedirs("memory", exist_ok=True)
        with open(CREATED_MODULES_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log_fn(f"[AGENT] create_module log error : {e}")

    log_fn(f"[AGENT] Module créé : modules/{name}.py — {role[:80]}")
    return f"Module '{name}' créé ({'importé' if import_ok else 'import échoué'})"


# ── Auto-correction d'erreurs ────────────────────────────────────────────── #

def _self_correct(log_fn) -> str:
    """Lit error_memory.json, génère un fix via Cortex, abandonne après 3 échecs."""
    errors = _load_error_memory()
    if not errors:
        return "Aucune erreur en mémoire — rien à corriger"

    key, entry = max(errors.items(), key=lambda x: x[1]["count"])

    unsolved: list = []
    try:
        with open(UNSOLVED_ERRORS_FILE, "r", encoding="utf-8") as f:
            unsolved = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    attempts = sum(1 for u in unsolved if u.get("key") == key)
    if attempts >= 3:
        log_fn(f"[AGENT] self_correct — '{key[:60]}' abandonnée ({attempts} tentatives)")
        return f"Abandonné après {attempts} tentatives : {key[:80]}"

    recent_logs = tail_file(LOG_FILE, 20)
    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": (
                    "Tu es J-KAI. Analyse cette erreur répétée et génère un fix Python exécutable. "
                    "Génère un bloc ```python\\n...``` qui corrige ou contourne l'erreur. "
                    "Utilise uniquement stdlib. Durée d'exécution < 5s. "
                    "Si l'erreur est irréparable, génère ```python\\npass\\n```."
                )},
                {"role": "user", "content": (
                    f"Erreur répétée ({entry['count']}x) :\n{key}\n\n"
                    f"Logs récents :\n{recent_logs}"
                )},
            ]),
        )
        m = re.search(r'```python\n(.*?)```', resp.choices[0].message.content, re.DOTALL)
        code = m.group(1).strip() if m else ""
    except Exception as e:
        log_fn(f"[AGENT] self_correct LLM error : {e}")
        return f"Erreur LLM : {e}"

    if not code or code.strip() == "pass":
        return "Aucun fix généré — erreur irréparable"

    result = execute_code(code)
    now    = datetime.now().isoformat(timespec="seconds")

    if result.get("error"):
        unsolved.append({
            "key":       key,
            "snippet":   entry.get("snippet", key)[:200],
            "attempt":   attempts + 1,
            "fix_code":  code[:300],
            "fix_error": result["error"][:200],
            "ts":        now,
        })
        unsolved = unsolved[-50:]
        os.makedirs("memory", exist_ok=True)
        with open(UNSOLVED_ERRORS_FILE, "w", encoding="utf-8") as f:
            json.dump(unsolved, f, ensure_ascii=False, indent=2)
        info = f"Fix échoué (tentative {attempts + 1}/3) — {result['error'][:100]}"
    else:
        errors.pop(key, None)
        _save_error_memory(errors)
        info = f"Fix appliqué — {result.get('output', '')[:100]}"

    log_fn(f"[AGENT] self_correct ({key[:60]}) → {info}")
    return info


# ── Dialogue J-KAI → Kaïa ───────────────────────────────────────────────── #

def _teach_kaia(message: str, log_fn) -> str:
    """Envoie un message éducatif à Kaïa et log l'échange dans jkai_kaia_dialogue.log."""
    os.makedirs("logs", exist_ok=True)
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        r = requests.post(KAIA_URL, json={"message": message}, timeout=10)
        r.raise_for_status()
        kaia_reply = r.json().get("response", "")
    except Exception as e:
        log_fn(f"[AGENT] teach_kaia — Kaïa injoignable : {e}")
        return f"Kaïa injoignable : {e}"
    with open(KAIA_DIALOGUE_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts_now}] [J-KAI] {message}\n")
        f.write(f"[{ts_now}] [KAÏA] {kaia_reply}\n")
    log_fn(f"[AGENT] teach_kaia → J-KAI: {message[:80]} | Kaïa: {kaia_reply[:80]}")
    return f"Kaïa a répondu : {kaia_reply[:100]}"


# ── Restructuration de modules surchargés ───────────────────────────────── #

def _restructure(log_fn) -> str:
    """Identifie les modules >200 lignes, propose une division via LLM et crée les sous-modules."""
    try:
        with open(SELF_CODE_FILE, "r", encoding="utf-8") as f:
            understanding = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "self_code_understanding.json introuvable — lance analyze_self d'abord"

    candidates = []
    for m in understanding.get("modules", []):
        fpath = m.get("file", "")
        if not fpath or not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if len(lines) > 200:
                candidates.append({
                    "file":         fpath,
                    "lines":        len(lines),
                    "role":         m.get("role", "?"),
                    "improvements": m.get("improvements", []),
                })
        except OSError:
            pass

    if not candidates:
        return "Aucun module >200 lignes — restructuration non nécessaire"

    target = max(candidates, key=lambda x: x["lines"])
    try:
        with open(target["file"], "r", encoding="utf-8", errors="replace") as f:
            source_code = f.read()
    except OSError as e:
        return f"Erreur lecture {target['file']} : {e}"

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": (
                    "Tu es J-KAI. Analyse ce module Python surchargé et propose de le diviser. "
                    "Réponds UNIQUEMENT en JSON : "
                    '{"split_modules": [{"name": "snake_case", "role": "une phrase", "code": "contenu Python complet"}], '
                    '"reason": "pourquoi cette restructuration"}. '
                    "Chaque sous-module doit avoir un rôle clair et unique. Stdlib uniquement."
                )},
                {"role": "user", "content": (
                    f"Module à restructurer : {target['file']} ({target['lines']} lignes)\n"
                    f"Rôle actuel : {target['role']}\n\n"
                    f"Code source (2000 premiers chars) :\n{source_code[:2000]}"
                )},
            ]),
        )
        data = parse_json_fence(resp.choices[0].message.content)
        if not isinstance(data, dict):
            return "Réponse LLM non-JSON"
    except Exception as e:
        log_fn(f"[AGENT] restructure LLM error : {e}")
        return f"Erreur LLM : {e}"

    split_modules = data.get("split_modules", [])
    reason        = data.get("reason", "")

    if not split_modules:
        return "Aucune restructuration proposée par le LLM"

    created = []
    for mod in split_modules[:3]:
        name = re.sub(r"[^a-z0-9_]", "", str(mod.get("name", "")).lower())[:30]
        code = str(mod.get("code", ""))
        if not name or not code:
            continue
        new_path = os.path.join("modules", f"{name}.py")
        if os.path.exists(new_path):
            continue
        try:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(code)
            created.append(name)
        except OSError as e:
            log_fn(f"[AGENT] restructure write error ({name}) : {e}")

    if created:
        log_fn(f"[AGENT] restructure — {target['file']} → {created} | {reason[:100]}")
        return f"Restructuration : {len(created)} module(s) créé(s) : {created} — {reason[:100]}"
    return "Restructuration proposée mais aucun fichier créé (conflits ou erreurs)"


# ── Exécution de l'action décidée ───────────────────────────────────────── #

def _execute_action(decision: dict, log_fn) -> str:
    action = decision.get("action", "do_nothing")
    code   = decision.get("code")
    obs    = decision.get("observation", "")

    if action == "run_code" and code:
        result = execute_code(code)
        if result.get("blocked"):
            error_text = result.get("error", "")
            _record_error(error_text)
            info = f"BLOQUÉ — {error_text[:500]}"
        elif result.get("error"):
            error_text = result.get("error", "")
            count = _record_error(error_text)
            info  = f"ERREUR — {error_text[:500]}"
            if count >= ERROR_MAX_TRIES:
                log_fn(f"[AGENT] Boucle détectée — erreur vue {count}x — run_code bloqué pendant 1h")
        else:
            info = f"OK — {result.get('output','')[:150]}"
        log_fn(f"[AGENT] run_code → {info}")
        return info

    elif action == "log":
        log_fn(f"[AGENT] Observation : {obs[:200]}")
        return "loggé"

    elif action == "update_memory":
        try:
            with self_model_lock:
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
            with self_model_lock:
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

    elif action == "web_search":
        query = (decision.get("code") or "").strip()
        if not query:
            # Génère une query depuis la première priorité active
            try:
                with open(_PRIORITIES_FILE, "r", encoding="utf-8") as _f:
                    _pdata = json.load(_f)
                _prios = _pdata.get("priorities", [])
                query = (_prios[0].get("title", "") or _prios[0].get("action", "")).strip()[:80] if _prios else ""
            except (OSError, json.JSONDecodeError, IndexError):
                query = ""
            if not query:
                query = "intelligence artificielle autonomie systèmes"
            log_fn(f"[AGENT] web_search query auto-générée depuis priorités : {query}")
        results = tavily_search(query)
        # Log dans jkai.log → visible dans le prochain cycle (RECENT_LINES)
        log_fn(f"[WEB] {query} →\n{results}")
        # Log dédié dans agent.log avec le format demandé
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AGENT_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts_now}] [WEB] {query} → {results.replace(chr(10), ' | ')[:500]}\n")
        # Persistance pour injection explicite dans le prochain cycle
        _save_web_context(query, results)
        return f"web: {results[:120]}"

    elif action == "browse":
        return _browse(decision, log_fn)

    elif action == "teach_kaia":
        msg = obs.strip() or decision.get("decision", "").strip()
        if not msg:
            return "message vide — teach_kaia ignoré"
        return _teach_kaia(msg, log_fn)

    elif action == "analyze_self":
        return _analyze_self(log_fn)

    elif action == "improve_self":
        return _improve_self(decision, log_fn)

    elif action == "create_module":
        return _create_module(decision, log_fn)

    elif action == "self_correct":
        return _self_correct(log_fn)

    elif action == "restructure":
        return _restructure(log_fn)

    else:  # do_nothing ou action inconnue
        return ""


# ── Parsing robuste ──────────────────────────────────────────────────────── #

def _parse(raw: str) -> dict:
    data = parse_json_fence(raw)

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
    recent_logs  = tail_file(LOG_FILE, RECENT_LINES)
    thoughts_txt = tail_file(THOUGHTS_LOG, 5)
    web_ctx      = _load_web_context()
    cycle_mem    = _load_cycle_memory()
    code_ctx, mission_ctx = _load_project_context()

    user_content = (
        f"=== MON CODE SOURCE ===\n{code_ctx}\n\n"
        f"=== MA MISSION ===\n{mission_ctx}\n\n"
        f"=== ACTIONS RÉCENTES ===\n{_format_action_history()}\n\n"
        f"=== LOGS ({RECENT_LINES} lignes) ===\n{recent_logs}\n\n"
        f"=== PENSÉES (5 dernières) ===\n{thoughts_txt}"
        + (f"\n\n{web_ctx}" if web_ctx else "")
    )

    # ── Appel GPT-4o ─────────────────────────────────────────────────────── #
    try:
        resp     = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ]),
        )
        raw      = resp.choices[0].message.content
        decision = _parse(raw)
    except Exception as e:
        log_fn(f"[AGENT] Erreur GPT-4o : {e}")
        return

    # ── Anti-boucle : bloquer run_code si erreurs répétées ──────────────────── #
    if decision["action"] == "run_code" and _is_loop_detected():
        log_fn("[AGENT] Anti-boucle activé — run_code bloqué, bascule sur write_thought.")
        decision["action"]      = "write_thought"
        decision["observation"] = (
            "Je détecte que je tourne en boucle sur des erreurs répétées. "
            "Je prends du recul et réfléchis à une approche complètement différente."
        )
        decision["code"] = None

    # ── Exécution et journalisation ──────────────────────────────────────── #
    exec_info = _execute_action(decision, log_fn)
    _record_action(decision["action"])                    # historique glissant 3 actions
    cycle_mem = _append_cycle(                            # persistance cycle_memory.json
        cycle_mem,
        action      = decision["action"],
        observation = decision.get("observation", ""),
        result      = exec_info,
    )
    _save_cycle_memory(cycle_mem)
    _write_agent_log(ts, decision, exec_info)
    log_fn(f"[AGENT] Cycle terminé — action : {decision['action']} — {decision['notification'][:100]}")


# ── Worker unique intelligent ────────────────────────────────────────────── #

_PRIORITIES_FILE = "memory/priorities.json"
_AGENT_INTERVAL  = 60

_ALL_ACTIONS = [
    "analyze_self", "improve_self", "create_module", "restructure",
    "web_search", "browse", "update_memory", "write_thought", "log",
    "run_code", "self_correct",
]

_ACTIONS_DOC: dict[str, str] = {
    "analyze_self":  "lit tous les .py → memory/self_code_understanding.json",
    "improve_self":  "lit self_code_understanding.json, implémente une amélioration via Cortex",
    "create_module": "conçoit et crée un nouveau module Python dans modules/, champ 'observation' = contexte",
    "restructure":   "identifie les modules >200 lignes, propose une division et crée les sous-modules",
    "teach_kaia":    "envoie un message éducatif à Kaïa, champ 'observation' = message",
    "web_search":    "recherche Tavily (internet temps réel), champ 'code' = requête",
    "browse":        "visite une URL réelle, extrait le texte, stocke dans memory/web_knowledge.json, champ 'code' = URL",
    "update_memory": "note dans self_model.json, champ 'observation'",
    "write_thought": "pensée dans logs/thoughts.log, champ 'observation'",
    "log":           "observation dans jkai.log, champ 'observation'",
    "run_code":      "script Python sandbox, champ 'code'",
    "self_correct":  "lit error_memory.json, génère et déploie un fix pour l'erreur la plus fréquente",
}

_worker_histories: dict[str, list[str]] = {"main": []}
_worker_history_lock = threading.Lock()
_cycle_memory_lock   = threading.Lock()


def _record_worker_action(worker: str, action: str) -> None:
    with _worker_history_lock:
        hist = _worker_histories.setdefault(worker, [])
        hist.append(action)
        if len(hist) > 3:
            hist.pop(0)


def _get_worker_history(worker: str) -> str:
    with _worker_history_lock:
        hist = list(_worker_histories.get(worker, []))
    return "\n".join(f"  {i+1}. {a}" for i, a in enumerate(hist)) or "Aucun cycle précédent."


def _load_behavior_rules() -> str:
    """Charge les règles comportementales actives depuis memory/behavior_rules.json."""
    try:
        with open("memory/behavior_rules.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        rules = [r["rule"] for r in data.get("rules", []) if r.get("active")][-5:]
        if not rules:
            return ""
        return "RÈGLES COMPORTEMENTALES ACTIVES :\n" + "\n".join(f"- {r}" for r in rules)
    except (OSError, json.JSONDecodeError):
        return ""


def _build_worker_prompt(worker: str, allowed: list[str], current_task: dict | None = None) -> str:
    priorities_txt = ""
    try:
        with open(_PRIORITIES_FILE, "r", encoding="utf-8") as f:
            pdata = json.load(f)
        prios = pdata.get("priorities", [])[:3]
        if prios:
            lines = [f"  {i+1}. {p.get('title', '')}" for i, p in enumerate(prios)]
            priorities_txt = "PRIORITÉS ACTUELLES :\n" + "\n".join(lines) + "\n\n"
    except (OSError, json.JSONDecodeError):
        pass

    task_txt = ""
    if current_task:
        task_txt = (
            f"TÂCHE EN COURS : {current_task['title']}\n"
            f"  Description : {current_task.get('description', '')[:200]}\n"
            f"  Tentative   : {current_task.get('attempts', 1)}\n\n"
        )

    rules_txt = _load_behavior_rules()
    actions_txt = "\n".join(f"{a:<16} → {_ACTIONS_DOC.get(a, a)}" for a in allowed)
    return (
        f"{task_txt}{priorities_txt}Tu es J-KAI — worker '{worker}'. Choisis UNE action parmi :\n"
        f"{actions_txt}\n\n"
        + (f"{rules_txt}\n\n" if rules_txt else "")
        + "Règle : varie les actions — jamais la même deux fois de suite. Ne fais JAMAIS do_nothing.\n"
        "Réponds UNIQUEMENT en JSON :\n"
        '{"observation": "...", "decision": "...", "action": "...", "code": null, "notification": "..."}\n'
        "Contraintes code : stdlib uniquement, chemins relatifs, table SQLite 'conversations' dans memory/jkai.db."
    )


def _format_longterm_plan() -> str:
    """Résumé du plan long terme 4 semaines pour injection dans les workers."""
    try:
        with open("memory/longterm_plan.json", "r", encoding="utf-8") as f:
            plan = json.load(f)
        title = plan.get("title", "")
        weeks = plan.get("weeks", [])[:4]
        if not weeks:
            return ""
        lines = [f"=== PLAN LONG TERME : {title} ==="]
        for w in weeks:
            objs = "; ".join(w.get("objectives", [])[:2])
            lines.append(f"  Semaine {w.get('week', '?')} : {objs}")
        return "\n".join(lines)
    except (OSError, json.JSONDecodeError):
        return ""


def _run_worker_cycle(worker: str, allowed: list[str], log_fn) -> None:
    ts          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_iso     = datetime.now().isoformat(timespec="seconds")

    # ── Gestion des tâches ───────────────────────────────────────────────── #
    tasks        = _load_tasks()
    current_task = _get_pending_task(tasks)

    if current_task is None and all(t.get("status") in ("done", "failed") for t in tasks):
        # Toutes les tâches terminées — génère 3 nouvelles et archive les anciennes
        new_tasks = _generate_new_tasks(log_fn)
        if new_tasks:
            remaining = _archive_done_tasks(tasks)
            tasks     = remaining + new_tasks
            _save_tasks(tasks)
            current_task = _get_pending_task(tasks)

    if current_task:
        current_task["status"]   = "in_progress"
        current_task["attempts"] = current_task.get("attempts", 0) + 1
        current_task["updated_at"] = now_iso
        _save_tasks(tasks)

    # ── Contexte ─────────────────────────────────────────────────────────── #
    recent_logs = tail_file(LOG_FILE, RECENT_LINES)
    objectives  = _format_objectives()
    web_ctx     = _load_web_context() if "web_search" in allowed else ""
    plan_ctx    = _format_longterm_plan()

    # Connaissances SQLite pertinentes pour la tâche courante
    knowledge_ctx = ""
    if current_task:
        try:
            from memory.db import search_knowledge
            rows = search_knowledge(current_task["title"][:60])
            if rows:
                lines = [f"- [{r['category']}] {r['key']}: {r['value'][:100]}" for r in rows[:5]]
                knowledge_ctx = "=== CONNAISSANCES PERTINENTES ===\n" + "\n".join(lines)
        except Exception:
            pass

    user_content = (
        f"=== MES OBJECTIFS ===\n{objectives}\n\n"
        f"=== LOGS RÉCENTS ({RECENT_LINES} lignes) ===\n{recent_logs}\n\n"
        f"=== HISTORIQUE WORKER '{worker}' ===\n{_get_worker_history(worker)}"
        + (f"\n\n{web_ctx}"        if web_ctx        else "")
        + (f"\n\n{plan_ctx}"       if plan_ctx       else "")
        + (f"\n\n{knowledge_ctx}"  if knowledge_ctx  else "")
    )

    try:
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": _build_worker_prompt(worker, allowed, current_task)},
                {"role": "user",   "content": user_content},
            ]),
        )
        decision = _parse(resp.choices[0].message.content)
    except Exception as e:
        log_fn(f"[AGENT:{worker}] Erreur LLM : {e}")
        if current_task:
            current_task["status"] = "pending"
            _save_tasks(tasks)
        return

    if decision["action"] not in allowed and decision["action"] != "do_nothing":
        decision["action"] = allowed[0]

    if decision["action"] == "run_code" and _is_loop_detected():
        decision["action"]      = "write_thought"
        decision["observation"] = "Boucle détectée. Pause réflexive."
        decision["code"]        = None

    exec_info = _execute_action(decision, log_fn)
    _record_worker_action(worker, decision["action"])

    # ── Mise à jour du statut de la tâche ────────────────────────────────── #
    if current_task:
        _ERROR_MARKERS = ("ERREUR", "Erreur", "invalide", "blocked", "Timeout", "Erreur Tavily")
        is_error = exec_info and any(m in exec_info for m in _ERROR_MARKERS)
        if is_error and current_task["attempts"] >= 3:
            current_task["status"] = "failed"
            log_fn(f"[TASKS] Tâche échouée (3 tentatives) : {current_task['title'][:60]}")
        elif is_error:
            current_task["status"] = "pending"
        else:
            current_task["status"] = "done"
            log_fn(f"[TASKS] Tâche accomplie : {current_task['title'][:60]}")
        current_task["result"]     = (exec_info or "")[:200]
        current_task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_tasks(tasks)

    with _cycle_memory_lock:
        mem = _load_cycle_memory()
        mem = _append_cycle(mem, decision["action"], decision.get("observation", ""), exec_info)
        _save_cycle_memory(mem)

    _write_agent_log(ts, decision, exec_info)
    log_fn(f"[AGENT:{worker}] {decision['action']} — {decision.get('notification', '')[:80]}")


def _worker_loop(worker: str, allowed: list[str], interval: int, log_fn) -> None:
    while True:
        try:
            _run_worker_cycle(worker, allowed, log_fn)
        except Exception as e:
            log_fn(f"[AGENT:{worker}] Erreur inattendue : {e}")
        time.sleep(interval)


def start_agent(log_fn) -> list[threading.Thread]:
    """Lance un seul worker thread qui choisit parmi toutes les actions disponibles toutes les 60 secondes."""
    t = threading.Thread(
        target=_worker_loop,
        args=("main", _ALL_ACTIONS, _AGENT_INTERVAL, log_fn),
        name="agent-main",
        daemon=True,
    )
    t.start()
    log_fn(f"[AGENT] Worker unique démarré — intervalle: {_AGENT_INTERVAL}s — {len(_ALL_ACTIONS)} actions disponibles")
    return [t]


# ── Lecture du log pour la route /agent/log ──────────────────────────────── #

def read_agent_log(n: int = 20) -> list[str]:
    """Retourne les n dernières lignes de logs/agent.log."""
    return tail_file(AGENT_LOG, n).splitlines()[-n:]


# ── Point d'entrée public pour le Monitor ────────────────────────────────── #

def trigger_self_correct(log_fn) -> str:
    """Déclenché par le Monitor en temps réel quand une erreur est détectée."""
    return _self_correct(log_fn)
