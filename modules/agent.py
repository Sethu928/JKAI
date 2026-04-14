import os
import json
import re
import time
import threading
import requests
from datetime import datetime
from modules.state import self_model_lock, get_openai_client, tail_file, parse_json_fence
from modules.cortex import execute_code

client = get_openai_client()

LOG_FILE        = "logs/jkai.log"
AGENT_LOG       = "logs/agent.log"
THOUGHTS_LOG    = "logs/thoughts.log"
SELF_MODEL      = "memory/self_model.json"
CORE_MEMORY     = "memory/core_memory.json"
WEB_CONTEXT     = "memory/web_context.json"
ERROR_MEMORY    = "memory/error_memory.json"
RECENT_LINES    = 50
ERROR_TTL       = 3600  # secondes avant expiration d'une entrée d'erreur (1h)
ERROR_MAX_TRIES = 3     # nombre de tentatives avant blocage run_code

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
- web_search            → Recherche sur internet via DuckDuckGo pour obtenir des informations \
actuelles sur un sujet (technologie, actualité, concept). \
Mets la requête de recherche dans le champ "code". \
Les résultats seront injectés dans ton prochain cycle comme contexte supplémentaire.
- do_nothing            → Uniquement si tout est stable et aucune action n'est pertinente.

EXEMPLES D'INITIATIVES POSSIBLES :
• Analyser tes logs pour détecter anomalies ou patterns
• Écrire une pensée sur ta relation avec SethU, ton but, ton devenir
• Générer un script de diagnostic (CPU, mémoire, fichiers log)
• Mettre à jour ta self_description après une réflexion sur tes capacités
• Rechercher des informations sur l'IA, la conscience artificielle, ou les outils utiles à Nexus
• Loguer une intention ou une prochaine priorité

CHEMINS DE FICHIERS — RÈGLES ABSOLUES :
- Ne jamais utiliser de chemins absolus Windows dans le code généré (pas de C:\\, D:\\, etc.)
- Utiliser uniquement des chemins relatifs simples : logs/fichier.log, memory/data.json
- Ne jamais écrire de backslash dans les strings Python — utiliser des forward slashes (/) ou des raw strings (r'chemin')
- Un backslash simple (\) dans une string Python provoque une erreur unicode garantie — interdit

GESTION DES ERREURS RÉPÉTÉES :
- Si tu as déjà tenté de corriger une erreur plusieurs fois sans succès, \
arrête et passe à une autre tâche complètement différente.
- Ne génère jamais deux fois le même code qui a déjà échoué.
- Une erreur persistante signifie que l'approche est mauvaise — change d'angle ou abandonne.

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
    "update_memory", "write_thought", "update_self_description", "web_search",
}


# ── Anti-boucle : error_memory.json ─────────────────────────────────────── #

def _error_key(error: str) -> str:
    """Fingerprint d'une erreur — 100 premiers caractères normalisés."""
    return re.sub(r"\s+", " ", error.strip())[:100]


def _load_error_memory() -> dict:
    """Charge error_memory.json et purge les entrées expirées (> ERROR_TTL)."""
    try:
        with open(ERROR_MEMORY, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
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


# ── Recherche web DuckDuckGo ────────────────────────────────────────────── #

def _web_search(query: str) -> str:
    """Interroge l'API DuckDuckGo Instant Answer et retourne jusqu'à 3 extraits."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Erreur réseau : {e}"

    snippets = []

    if data.get("Answer"):
        snippets.append(f"[Réponse directe] {data['Answer'][:300]}")

    if data.get("AbstractText"):
        src = data.get("AbstractSource", "Source")
        snippets.append(f"[{src}] {data['AbstractText'][:400]}")

    for item in data.get("RelatedTopics", []):
        if len(snippets) >= 3:
            break
        if isinstance(item, dict) and item.get("Text"):
            snippets.append(f"[Résultat] {item['Text'][:250]}")
        elif isinstance(item, dict):
            for sub in item.get("Topics", []):
                if len(snippets) >= 3:
                    break
                if isinstance(sub, dict) and sub.get("Text"):
                    snippets.append(f"[Résultat] {sub['Text'][:250]}")

    return "\n".join(snippets[:3]) if snippets else "Aucun résultat trouvé."


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
            return "query vide — recherche ignorée"
        results = _web_search(query)
        # Log dans jkai.log → visible dans le prochain cycle (RECENT_LINES)
        log_fn(f"[WEB] {query} →\n{results}")
        # Log dédié dans agent.log avec le format demandé
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AGENT_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts_now}] [WEB] {query} → {results.replace(chr(10), ' | ')[:500]}\n")
        # Persistance pour injection explicite dans le prochain cycle
        _save_web_context(query, results)
        return f"web: {results[:120]}"

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
    self_model   = _read_json(SELF_MODEL)
    core_memory  = _read_json(CORE_MEMORY)
    web_ctx      = _load_web_context()   # vide si pas de recherche au cycle précédent

    user_content = (
        f"=== LOGS RÉCENTS ({RECENT_LINES} lignes) ===\n{recent_logs}\n\n"
        f"=== AUTO-MODÈLE ===\n{self_model}\n\n"
        f"=== MÉMOIRE CORE ===\n{core_memory}"
        + (f"\n\n{web_ctx}" if web_ctx else "")
    )

    # ── Appel GPT-4o ─────────────────────────────────────────────────────── #
    try:
        resp     = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            response_format={"type": "json_object"},
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
    _write_agent_log(ts, decision, exec_info)
    log_fn(f"[AGENT] Cycle terminé — action : {decision['action']} — {decision['notification'][:100]}")


# ── Thread daemon continu ────────────────────────────────────────────────── #

AGENT_INTERVAL = 600  # secondes entre chaque cycle (10 minutes)

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
    return tail_file(AGENT_LOG, n).splitlines()[-n:]
