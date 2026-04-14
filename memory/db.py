import sqlite3
import os
from datetime import datetime

DB_FILE = "memory/jkai.db"

def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL)''')
    conn.commit()
    conn.close()

def save_message(role, content):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)", (role, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def load_history(limit: int = 100):
    """Retourne les `limit` derniers messages (ordre chronologique).
    Passer limit=0 pour récupérer tout l'historique sans restriction."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if limit:
        c.execute(
            "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = list(reversed(c.fetchall()))
    else:
        c.execute("SELECT role, content FROM conversations ORDER BY id ASC")
        rows = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in rows]


def count_history() -> int:
    """Retourne le nombre total de messages sans charger leur contenu."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM conversations")
    count = c.fetchone()[0]
    conn.close()
    return count

def clear_history():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()

def search_history(keyword):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role, content, timestamp FROM conversations WHERE content LIKE ? ORDER BY id ASC", (f"%{keyword}%",))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]