"""
Suite de smoke tests J-KAI / Nexus.
Aucun appel réseau réel : OpenAI et Tavily sont neutralisés via conftest.py
(clés factices) — les clients se créent mais ne font aucune requête.
"""
import glob
import importlib
import os

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 1. memory/db.py
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Retourne le module memory.db câblé sur une base SQLite temporaire."""
    import memory.db as _db
    monkeypatch.setattr(_db, "DB_FILE", str(tmp_path / "test.db"))
    _db.init_db()
    return _db


def test_db_tables_created(db):
    with db._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "conversations" in tables, "table 'conversations' absente"
    assert "knowledge" in tables,     "table 'knowledge' absente"


def test_db_save_load_roundtrip(db):
    db.save_message("user",      "bonjour")
    db.save_message("assistant", "salut")
    history = db.load_history()
    assert len(history) == 2
    assert history[0] == {"role": "user",      "content": "bonjour"}
    assert history[1] == {"role": "assistant",  "content": "salut"}


def test_db_clear_history(db):
    db.save_message("user", "hello")
    db.clear_history()
    assert db.load_history() == []


# ══════════════════════════════════════════════════════════════════════════════
# 2. modules/cortex.py — execute_code (pas de génération LLM)
# ══════════════════════════════════════════════════════════════════════════════

def test_execute_code_simple():
    from modules.cortex import execute_code
    result = execute_code("print(1 + 1)")
    assert result["blocked"] is False
    assert result["output"] == "2"
    assert not result["error"]


def test_execute_code_blocked_by_sandbox():
    from modules.cortex import execute_code
    result = execute_code("import os; os.system('ls')")
    assert result["blocked"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 3. modules/llm_utils.py — strip_think
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def strip_think():
    from modules.llm_utils import strip_think as _f
    return _f


def test_strip_think_closed_tag(strip_think):
    assert strip_think("<think>raisonnement</think>réponse") == "réponse"


def test_strip_think_multiline(strip_think):
    raw = "<think>\nligne 1\nligne 2\n</think>\n\nrésultat"
    assert strip_think(raw) == "résultat"


def test_strip_think_unclosed_truncates(strip_think):
    assert strip_think("avant<think>raisonnement sans fermeture") == "avant"


def test_strip_think_thinking_variant(strip_think):
    assert strip_think("<thinking>process</thinking>output") == "output"


def test_strip_think_no_tag_passthrough(strip_think):
    assert strip_think("texte propre") == "texte propre"


def test_strip_think_case_insensitive(strip_think):
    assert strip_think("<THINK>noise</THINK>signal") == "signal"


# ══════════════════════════════════════════════════════════════════════════════
# 4. modules/state.py — parse_json_fence avec <think> + fences markdown
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def parse_json_fence():
    from modules.state import parse_json_fence as _f
    return _f


def test_parse_json_fence_with_think_and_markdown(parse_json_fence):
    raw = (
        "<think>je réfléchis longuement...</think>\n"
        "```json\n"
        '{"action": "log", "observation": "ok"}\n'
        "```"
    )
    assert parse_json_fence(raw) == {"action": "log", "observation": "ok"}


def test_parse_json_fence_plain(parse_json_fence):
    assert parse_json_fence('{"x": 1}') == {"x": 1}


def test_parse_json_fence_markdown_only(parse_json_fence):
    raw = "```json\n{\"k\": \"v\"}\n```"
    assert parse_json_fence(raw) == {"k": "v"}


def test_parse_json_fence_invalid_returns_empty(parse_json_fence):
    assert parse_json_fence("pas du json du tout") == {}


def test_parse_json_fence_unclosed_think_before_json(parse_json_fence):
    raw = "<think>réflexion sans fermeture"
    assert parse_json_fence(raw) == {}


# ══════════════════════════════════════════════════════════════════════════════
# 5. Import sanity — tous les modules de modules/ s'importent sans crash
# ══════════════════════════════════════════════════════════════════════════════

def test_all_modules_importable():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    py_files = sorted(glob.glob(os.path.join(root, "modules", "*.py")))

    failed = []
    for path in py_files:
        name = os.path.splitext(os.path.basename(path))[0]
        if name == "__init__":
            continue
        module_name = f"modules.{name}"
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failed.append(f"  {module_name}: {type(exc).__name__}: {exc}")

    if failed:
        report = "\n".join(failed)
        pytest.fail(
            f"{len(failed)} module(s) ne s'importent pas :\n{report}",
            pytrace=False,
        )
