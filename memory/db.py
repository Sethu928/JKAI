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
