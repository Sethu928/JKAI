import sqlite3
import os
import contextlib
from datetime import datetime

DB_FILE = "memory/jkai.db"


def _connect():
    """Retourne un context manager qui ouvre ET ferme la connexion SQLite."""
    return contextlib.closing(sqlite3.connect(DB_FILE))


def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "role TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "timestamp TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "category TEXT NOT NULL, "
            "key TEXT NOT NULL, "
            "value TEXT NOT NULL, "
            "source TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_cat_key "
            "ON knowledge (category, key)"
        )
        conn.commit()


def save_message(role, content):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)",
            (role, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def load_history(limit: int = 100):
    """Retourne les `limit` derniers messages (ordre chronologique).
    Passer limit=0 pour récupérer tout l'historique sans restriction."""
    with _connect() as conn:
        if limit:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id ASC"
            ).fetchall()
    return [{"role": row[0], "content": row[1]} for row in rows]


def count_history() -> int:
    """Retourne le nombre total de messages sans charger leur contenu."""
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def clear_history():
    with _connect() as conn:
        conn.execute("DELETE FROM conversations")
        conn.commit()


def search_history(keyword):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, timestamp FROM conversations "
            "WHERE content LIKE ? ORDER BY id ASC",
            (f"%{keyword}%",),
        ).fetchall()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]


# ── Table knowledge ──────────────────────────────────────────────────────── #

def save_knowledge(category: str, key: str, value: str, source: str = "") -> None:
    """Insère ou met à jour une entrée dans la table knowledge (upsert sur category+key)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO knowledge (category, key, value, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(category, key) DO UPDATE SET "
            "value=excluded.value, source=excluded.source, updated_at=excluded.updated_at",
            (category, key, value, source, now, now),
        )
        conn.commit()


def search_knowledge(query: str, category: str | None = None) -> list[dict]:
    """Recherche dans la table knowledge par LIKE sur key et value.
    Filtre optionnel sur category. Retourne au max 10 résultats."""
    pattern = f"%{query}%"
    with _connect() as conn:
        if category:
            rows = conn.execute(
                "SELECT category, key, value, source, updated_at FROM knowledge "
                "WHERE category = ? AND (key LIKE ? OR value LIKE ?) "
                "ORDER BY updated_at DESC LIMIT 10",
                (category, pattern, pattern),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT category, key, value, source, updated_at FROM knowledge "
                "WHERE key LIKE ? OR value LIKE ? "
                "ORDER BY updated_at DESC LIMIT 10",
                (pattern, pattern),
            ).fetchall()
    return [
        {"category": r[0], "key": r[1], "value": r[2], "source": r[3], "updated_at": r[4]}
        for r in rows
    ]
