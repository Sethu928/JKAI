import os
import re
import json
import threading
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_USAGE_FILE  = "memory/api_usage.json"
_api_usage_lock = threading.Lock()


def _track_api_call() -> None:
    """Incrémente le compteur d'appels OpenAI réels dans memory/api_usage.json."""
    with _api_usage_lock:
        usage: dict = {}
        try:
            with open(API_USAGE_FILE, "r", encoding="utf-8") as f:
                usage = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        today    = datetime.now().strftime("%Y-%m-%d")
        day      = usage.get(today, {"calls": 0, "tokens_est": 0})
        day["calls"]       += 1
        day["tokens_est"]  += 500   # estimation : ~500 tokens/appel
        day["last_call"]    = datetime.now().isoformat(timespec="seconds")
        usage[today] = day
        keys  = sorted(usage.keys())[-30:]
        usage = {k: usage[k] for k in keys}
        try:
            os.makedirs("memory", exist_ok=True)
            with open(API_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(usage, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

# ── Client Tavily ────────────────────────────────────────────────────────── #
_tavily_client = None
_tavily_lock   = threading.Lock()


def get_tavily_client():
    """Retourne un singleton TavilyClient (None si tavily-python non installé ou clé absente)."""
    global _tavily_client
    if _tavily_client is None:
        with _tavily_lock:
            if _tavily_client is None:
                try:
                    from tavily import TavilyClient
                    _tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
                except (ImportError, Exception):
                    pass
    return _tavily_client


# ── Singleton OpenAI — un seul client partagé par tous les modules ──────── #
_openai_client: OpenAI | None = None
_client_init_lock = threading.Lock()

_local_client: OpenAI | None = None
_local_client_lock = threading.Lock()

LOCAL_MODEL = os.getenv("LM_STUDIO_MODEL", "local-model")


def get_local_client() -> OpenAI:
    """Retourne le client LM Studio (singleton thread-safe, base_url 127.0.0.1:1234)."""
    global _local_client
    if _local_client is None:
        with _local_client_lock:
            if _local_client is None:
                _local_client = OpenAI(
                    base_url=os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
                    api_key="local",
                )
    return _local_client


def get_openai_client() -> OpenAI:
    """Retourne le client OpenAI. Si LM_STUDIO=true dans .env, redirige vers LM Studio."""
    if os.getenv("LM_STUDIO", "").lower() == "true":
        return get_local_client()
    global _openai_client
    if _openai_client is None:
        with _client_init_lock:
            if _openai_client is None:
                _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def format_messages_for_local(messages: list) -> list:
    """
    Adapte les messages pour Phi-3 et modèles locaux sans support du rôle 'system'.
    Fusionne le system prompt avec le premier message user suivant :
      <|system|>...<|end|>\\n<|user|>...<|end|>\\n<|assistant|>
    Si pas de message system en tête, retourne la liste inchangée.
    """
    if not messages:
        return [{"role": "user", "content": ""}]
    if messages[0].get("role") != "system":
        return messages
    system_content = messages[0]["content"]
    rest = list(messages[1:])
    for i, msg in enumerate(rest):
        if msg.get("role") == "user":
            rest[i] = {
                "role":    "user",
                "content": (
                    f"<|system|>\n{system_content}<|end|>\n"
                    f"<|user|>\n{msg['content']}<|end|>\n"
                    f"<|assistant|>"
                ),
            }
            break
    if not rest:
        return messages
    return rest


# ── Locks partagés entre modules ─────────────────────────────────────────── #
# Protège memory/self_model.json — utilisé par agent.py ET consciousness.py
# qui tournent dans des threads séparés quand l'autonomie est active.
self_model_lock = threading.Lock()


# ── Lecture de fichiers texte ────────────────────────────────────────────── #

def tail_file(path: str, n: int) -> str:
    """Retourne les n dernières lignes d'un fichier texte (UTF-8, erreurs ignorées)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).strip() or "(vide)"
    except OSError:
        return "(inaccessible)"


# ── Parsing JSON robuste ─────────────────────────────────────────────────── #
_MD_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def parse_json_fence(raw: str) -> dict:
    """
    Parse une réponse JSON brute.
    - Retire les fences markdown ```json ... ``` si présentes.
    - Fallback : extrait le premier bloc { ... } trouvé dans le texte.
    - Retourne un dict vide en cas d'échec total.
    """
    raw = raw.strip()
    m = _MD_FENCE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}
