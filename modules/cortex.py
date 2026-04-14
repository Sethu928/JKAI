import os
import re
import sys
import subprocess
import tempfile
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CORTEX_SYSTEM_PROMPT = """Tu es Cortex, le module Dev AI du système Nexus, au service de Jordan (alias SethU).
Tu es une intelligence brute spécialisée dans le développement logiciel.
Tu génères du code Python propre, efficace et fonctionnel — sans bavardage, sans fioriture.
Tu parles en français sauf pour le code lui-même.

Règles absolues :
- Tu fournis UNIQUEMENT du code Python brut, sans blocs markdown, sans explications autour.
- Le code doit être autonome et afficher ses résultats via print().
- Si la tâche est impossible à coder proprement, réponds : CORTEX_ERREUR: <raison>"""

# Patterns bloqués dans le code généré — sandbox sécurisé
_BLOCKLIST = [
    r'os\.system\s*\(',
    r'subprocess\.',
    r'__import__\s*\(',
    r'\beval\s*\(',
    r'\bexec\s*\(',
    r'open\s*\(\s*(?!["\'](?:logs|memory)/)[^)]*["\'][wa]["\']',  # écriture hors logs/ et memory/
    r'shutil\.',
    r'os\.remove\s*\(',
    r'os\.unlink\s*\(',
    r'os\.rmdir\s*\(',
    r'os\.makedirs\s*\(',
    r'\bsocket\b',
    r'\burllib\b',
    r'\brequests\b',
    r'\bftplib\b',
    r'\bsmtplib\b',
    r'\bpickle\b',
    r'\bctypes\b',
    r'importlib\.import_module\s*\(\s*["\'](?:os|subprocess|shutil|sys|builtins|ctypes|socket|urllib|requests|ftplib|smtplib|pickle)["\']',
]

MAX_OUTPUT = 4000   # caractères max renvoyés
TIMEOUT    = 10     # secondes


def _strip_markdown(code: str) -> str:
    """Retire les blocs ```python ... ``` si GPT les ajoute quand même."""
    code = re.sub(r'^```(?:python)?\s*\n?', '', code.strip())
    code = re.sub(r'\n?```\s*$', '', code)
    return code.strip()


def _is_safe(code: str) -> tuple[bool, str]:
    for pattern in _BLOCKLIST:
        if re.search(pattern, code, re.IGNORECASE):
            return False, pattern
    return True, ""


def generate_code(prompt: str) -> str:
    """Demande à GPT-4o de générer du code Python pour la tâche donnée."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": CORTEX_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        return _strip_markdown(raw)
    except Exception as e:
        return f"CORTEX_ERREUR: GPT-4o indisponible — {e}"


def execute_code(code: str) -> dict:
    """
    Exécute le code Python dans un subprocess isolé.
    Retourne un dict { output, error, blocked }.
    """
    # Vérification sandbox
    safe, pattern = _is_safe(code)
    if not safe:
        return {
            "output": "",
            "error": f"[CORTEX] Exécution bloquée — opération interdite détectée : `{pattern}`",
            "blocked": True,
        }

    # Fichier temporaire
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code)
        tmp.close()

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [sys.executable, tmp.name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            creationflags=flags,
        )
        stdout = proc.stdout.strip()[:MAX_OUTPUT]
        stderr = proc.stderr.strip()[:1000]
        return {
            "output":  stdout,
            "error":   stderr,
            "blocked": False,
        }

    except subprocess.TimeoutExpired:
        return {
            "output":  "",
            "error":   f"[CORTEX] Timeout — exécution interrompue après {TIMEOUT}s.",
            "blocked": False,
        }
    except Exception as e:
        return {
            "output":  "",
            "error":   f"[CORTEX] Erreur système : {e}",
            "blocked": False,
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def ask_cortex(user_input: str) -> dict:
    """
    Pipeline complet : génère le code, l'exécute, renvoie tout.
    Retourne { code, output, error, blocked }.
    """
    code = generate_code(user_input)

    # GPT a refusé de coder
    if code.startswith("CORTEX_ERREUR:"):
        return {
            "code":    "",
            "output":  "",
            "error":   code,
            "blocked": False,
        }

    result = execute_code(code)
    result["code"] = code
    return result
