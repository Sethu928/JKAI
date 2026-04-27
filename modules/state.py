import os
import re
import json
import threading

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

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
                    base_url="http://127.0.0.1:1234/v1",
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
