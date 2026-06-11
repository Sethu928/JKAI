import os
import re
import sys
import socket
import subprocess
import tempfile
from datetime import datetime, timedelta

# ── Sécurité des modifications — double logique allowlist + blocklist ─────── #
#
# ALLOWLIST : seuls ces patterns sont modifiables/supprimables par l'agent.
#   • modules/<name>.py   — tous les modules sauf exceptions ci-dessous
#   • memory/<name>.json  — fichiers d'état persistant
#
# BLOCKLIST absolue (vérifiée même si le chemin est dans l'allowlist) :
#   • server.py, killswitch.py, .env  — infrastructure critique
#   • modules/state.py                — singleton partagé entre tous les modules
#   • modules/self_update.py          — l'agent ne peut pas modifier son propre garde
#   • watchdog.py, conftest.py        — sécurité système / CI

_HARD_BLOCKED_NAMES = frozenset({
    "server.py",
    "killswitch.py",
    ".env",
    "watchdog.py",
    "conftest.py",
})

_HARD_BLOCKED_REL = frozenset({
    "modules/state.py",
    "modules/self_update.py",
})


def _is_in_allowlist(path: str) -> bool:
    """
    Retourne True uniquement si le chemin est dans la liste blanche ET absent
    de la blocklist absolue.  Chemin normalisé en '/' relatif à la racine projet.
    """
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath("."))
    rel = rel.replace("\\", "/")

    # Blocklist absolue — niveau basename
    if os.path.basename(rel) in _HARD_BLOCKED_NAMES:
        return False
    # Blocklist absolue — niveau chemin relatif
    if rel in _HARD_BLOCKED_REL:
        return False

    # Allowlist : modules/*.py  ou  memory/*.json
    parts = rel.split("/")
    if len(parts) == 2:
        folder, filename = parts
        if folder == "modules" and filename.endswith(".py"):
            return True
        if folder == "memory" and filename.endswith(".json"):
            return True

    return False

PI_HOST = "192.168.1.122"
PI_USER = "pi"
PI_CMD  = "cd ~/jkai && git pull && sudo systemctl restart jkai"


# ── Lecture ──────────────────────────────────────────────────────────────── #

def read_file(path: str) -> str:
    """
    Lit un fichier Python du projet.
    Lève ValueError si le chemin sort du répertoire projet.
    """
    abs_path    = os.path.abspath(path)
    project_root = os.path.abspath(".")
    if not abs_path.startswith(project_root + os.sep) and abs_path != project_root:
        raise ValueError(f"Chemin hors du projet : {path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


# ── Écriture + vérification syntaxe ─────────────────────────────────────── #

def write_and_test(path: str, new_code: str, log_fn) -> bool:
    """
    1. Écrit new_code dans un fichier temporaire.
    2. Vérifie la syntaxe via `python -m py_compile`.
    3. Si OK → écrit le vrai fichier et retourne True.
    4. Si erreur → annule et log, retourne False.
    """
    abs_path     = os.path.abspath(path)
    project_root = os.path.abspath(".")

    # Vérification chemin
    if not abs_path.startswith(project_root + os.sep) and abs_path != project_root:
        log_fn(f"[SELF_UPDATE] Chemin refusé (hors projet) : {path}")
        return False

    if not _is_in_allowlist(path):
        log_fn(f"[SELF_UPDATE] Fichier hors liste blanche — refusé : {path}")
        return False

    # Test syntaxe + import dynamique dans un fichier temporaire
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(new_code)
        tmp.close()

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        # Étape 1 : vérification syntaxe via py_compile
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", tmp.name],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=flags,
        )
        if proc.returncode != 0:
            err = (proc.stdout + proc.stderr).strip()
            log_fn(f"[SELF_UPDATE] Syntaxe invalide dans {path} :\n{err[:500]}")
            return False

        # Étape 2 : import dynamique dans un subprocess isolé (fichiers .py uniquement)
        if path.endswith(".py"):
            import_env = os.environ.copy()
            import_env["_SELF_UPDATE_TEST_FILE"] = tmp.name
            import_cmd = (
                "import sys, os; sys.path.insert(0, '.'); "
                "import importlib.util; "
                "f = os.environ['_SELF_UPDATE_TEST_FILE']; "
                "spec = importlib.util.spec_from_file_location('_test_mod', f); "
                "mod = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(mod)"
            )
            import_proc = subprocess.run(
                [sys.executable, "-c", import_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.path.abspath("."),
                env=import_env,
                creationflags=flags,
            )
            if import_proc.returncode != 0:
                err = (import_proc.stdout + import_proc.stderr).strip()
                log_fn(f"[SELF_UPDATE] Import test échoué pour {path} :\n{err[:500]}")
                return False

    except subprocess.TimeoutExpired:
        log_fn(f"[SELF_UPDATE] Timeout test pour {path}")
        return False
    except Exception as e:
        log_fn(f"[SELF_UPDATE] Erreur test : {e}")
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # Écriture effective
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(new_code)
    log_fn(f"[SELF_UPDATE] ✓ Fichier écrit : {path}")
    return True


# ── Constantes pour la garde post-update ─────────────────────────────────── #

_JKAI_LOG = "logs/jkai.log"
_FATAL_RE = re.compile(
    r"\[ERREUR\]|Traceback \(most recent|^\s*\w+Error:",
    re.IGNORECASE | re.MULTILINE,
)


# ── Tests post-update ─────────────────────────────────────────────────────── #

def _run_tests(log_fn) -> bool:
    """Lance tests/test_smoke.py via subprocess. Retourne True si tous les tests passent."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_smoke.py", "-q", "--tb=no"],
            capture_output=True, text=True, timeout=120, creationflags=flags,
        )
        passed = proc.returncode == 0
        summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "(aucune sortie)"
        log_fn(f"[SELF_UPDATE] Tests post-update : {'OK' if passed else 'ÉCHEC'} | {summary}")
        return passed
    except Exception as e:
        log_fn(f"[SELF_UPDATE] Tests post-update inaccessibles : {e}")
        return False


# ── Git ───────────────────────────────────────────────────────────────────── #

def _run_git(args: list, log_fn) -> tuple[bool, str]:
    """Lance une commande git et retourne (succès, sortie)."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
        )
        out = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, out
    except Exception as e:
        return False, str(e)


def _get_remote_url() -> str:
    """Retourne l'URL actuelle de origin."""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _git_push(log_fn) -> bool:
    """Push avec injection du token GitHub si disponible. Restaure l'URL après. Retourne True si succès."""
    token        = os.getenv("GITHUB_TOKEN", "").strip()
    original_url = _get_remote_url()
    url_patched  = False

    if token and original_url.startswith("https://github.com/"):
        auth_url = original_url.replace("https://github.com/", f"https://{token}@github.com/")
        _run_git(["remote", "set-url", "origin", auth_url], log_fn)
        url_patched = True
        log_fn("[SELF_UPDATE] Token GitHub injecté dans l'URL remote (temporaire)")

    ok, out = _run_git(["push"], log_fn)
    log_fn(f"[SELF_UPDATE] git push → {'OK' if ok else 'ERREUR'} | {out[:200]}")

    if url_patched and original_url:
        _run_git(["remote", "set-url", "origin", original_url], log_fn)
        log_fn("[SELF_UPDATE] URL remote restaurée.")

    return ok


def git_commit_and_push(file_path: str, message: str, log_fn) -> bool:
    """
    git add <file_path> → git commit -m message → git push.
    Seul le fichier ciblé est stagé — évite de commiter des fichiers non liés.
    Retourne True si le push réussit.
    """
    ok, out = _run_git(["add", "--", file_path], log_fn)
    log_fn(f"[SELF_UPDATE] git add {file_path} → {'OK' if ok else 'ERREUR'} | {out[:200]}")
    if not ok:
        return False

    _run_git(["config", "user.email", "jordan.rostaing28@icloud.com"], log_fn)
    _run_git(["config", "user.name", "Sethu928"], log_fn)

    ok, out = _run_git(["commit", "-m", message], log_fn)
    log_fn(f"[SELF_UPDATE] git commit → {'OK' if ok else 'ERREUR'} | {out[:200]}")
    if not ok and "nothing to commit" not in out:
        return False

    return _git_push(log_fn)


# ── Déploiement SSH Pi ────────────────────────────────────────────────────── #

def deploy_to_pi(log_fn) -> bool:
    """
    Déploie sur le Pi selon le contexte d'exécution :
    - Sur le Pi (hostname == 'jkai') : git pull + restart en local via subprocess.
    - Sur le PC : SSH via paramiko vers 192.168.1.122.
    Retourne True si succès.
    """
    on_pi = socket.gethostname() == "jkai"

    if on_pi:
        return _deploy_local(log_fn)
    else:
        return _deploy_via_ssh(log_fn)


def _deploy_local(log_fn) -> bool:
    """Exécution locale sur le Pi — git pull puis redémarrage systemd."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    for cmd in (["git", "pull"], ["sudo", "systemctl", "restart", "jkai"]):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=flags,
            )
            out = (proc.stdout + proc.stderr).strip()
            label = " ".join(cmd)
            log_fn(f"[SELF_UPDATE] {label} → {'OK' if proc.returncode == 0 else 'ERREUR'} | {out[:300]}")
            if proc.returncode != 0:
                return False
        except Exception as e:
            log_fn(f"[SELF_UPDATE] Erreur locale ({' '.join(cmd)}) : {e}")
            return False
    log_fn("[SELF_UPDATE] Déploiement local Pi terminé.")
    return True


def _deploy_via_ssh(log_fn) -> bool:
    """Déploiement depuis le PC via paramiko SSH vers le Pi."""
    key_path = os.path.expanduser("~/.ssh/jkai_key")
    if not os.path.exists(key_path):
        log_fn(f"[SELF_UPDATE] Clé SSH introuvable ({key_path}) — déploiement Pi ignoré.")
        return False

    try:
        import paramiko
    except ImportError:
        log_fn("[SELF_UPDATE] paramiko absent — deploy_to_pi ignoré.")
        return False

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(PI_HOST, username=PI_USER, key_filename=key_path, timeout=15)
        log_fn(f"[SELF_UPDATE] SSH connecté à {PI_HOST} ({PI_USER})")
        _, stdout, stderr = ssh.exec_command(PI_CMD, timeout=30)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if out:
            log_fn(f"[SELF_UPDATE] Pi stdout : {out[:400]}")
        if err:
            log_fn(f"[SELF_UPDATE] Pi stderr : {err[:400]}")
        log_fn("[SELF_UPDATE] Déploiement SSH Pi terminé.")
        return True
    except Exception as e:
        log_fn(f"[SELF_UPDATE] Erreur SSH Pi : {e}")
        return False
    finally:
        ssh.close()


# ── Suppression de fichier ───────────────────────────────────────────────── #

def delete_file(file_path: str, message: str, log_fn) -> dict:
    """
    Supprime un fichier du projet via git rm → commit → push → deploy Pi.
    Refuse les fichiers protégés et les chemins hors projet.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_fn(f"[SELF_DELETE] Cycle démarré — {file_path} — {ts}")

    result = {
        "file_path": file_path,
        "ts":        ts,
        "delete_ok": False,
        "commit_ok": False,
        "deploy_ok": False,
        "error":     "",
    }

    abs_path     = os.path.abspath(file_path)
    project_root = os.path.abspath(".")
    if not abs_path.startswith(project_root + os.sep) and abs_path != project_root:
        result["error"] = f"Chemin hors du projet : {file_path}"
        log_fn(f"[SELF_DELETE] {result['error']}")
        return result

    if not _is_in_allowlist(file_path):
        result["error"] = f"Fichier hors liste blanche — suppression refusée : {file_path}"
        log_fn(f"[SELF_DELETE] {result['error']}")
        return result

    if not os.path.exists(abs_path):
        result["error"] = f"Fichier introuvable : {file_path}"
        log_fn(f"[SELF_DELETE] {result['error']}")
        return result

    ok, out = _run_git(["rm", "--", file_path], log_fn)
    log_fn(f"[SELF_DELETE] git rm {file_path} → {'OK' if ok else 'ERREUR'} | {out[:200]}")
    if not ok:
        result["error"] = f"git rm échoué : {out[:200]}"
        return result
    result["delete_ok"] = True

    _run_git(["config", "user.email", "jordan.rostaing28@icloud.com"], log_fn)
    _run_git(["config", "user.name", "Sethu928"], log_fn)

    ok, out = _run_git(["commit", "-m", message], log_fn)
    log_fn(f"[SELF_DELETE] git commit → {'OK' if ok else 'ERREUR'} | {out[:200]}")
    if not ok and "nothing to commit" not in out:
        result["error"] = "Échec git commit"
        return result

    token        = os.getenv("GITHUB_TOKEN", "").strip()
    original_url = _get_remote_url()
    url_patched  = False
    if token and original_url.startswith("https://github.com/"):
        auth_url = original_url.replace("https://github.com/", f"https://{token}@github.com/")
        _run_git(["remote", "set-url", "origin", auth_url], log_fn)
        url_patched = True
        log_fn("[SELF_DELETE] Token GitHub injecté dans l'URL remote (temporaire)")

    ok, out = _run_git(["push"], log_fn)
    log_fn(f"[SELF_DELETE] git push → {'OK' if ok else 'ERREUR'} | {out[:200]}")

    if url_patched and original_url:
        _run_git(["remote", "set-url", "origin", original_url], log_fn)
        log_fn("[SELF_DELETE] URL remote restaurée.")

    if not ok:
        result["error"] = "Échec git push"
        return result
    result["commit_ok"] = True

    result["deploy_ok"] = deploy_to_pi(log_fn)
    if not result["deploy_ok"]:
        result["error"] = "Échec déploiement Pi (SSH)"

    log_fn(f"[SELF_DELETE] Cycle terminé — delete={result['delete_ok']} "
           f"commit={result['commit_ok']} deploy={result['deploy_ok']}")
    return result


# ── Rollback ─────────────────────────────────────────────────────────────── #

def _run_rollback(log_fn) -> bool:
    """
    Annule le dernier commit (git revert --no-edit HEAD), re-push et re-déploie.
    Retourne True si le rollback complet a réussi.
    """
    ok, out = _run_git(["revert", "--no-edit", "HEAD"], log_fn)
    log_fn(f"[SELF_UPDATE] git revert → {'OK' if ok else 'ERREUR'} | {out[:300]}")
    if not ok:
        log_fn("[SELF_UPDATE] ROLLBACK échoué — revert impossible")
        return False

    push_ok = _git_push(log_fn)
    if not push_ok:
        log_fn("[SELF_UPDATE] ROLLBACK — push échoué après revert")
        return False

    deploy_ok = deploy_to_pi(log_fn)
    log_fn(f"[SELF_UPDATE] ROLLBACK terminé — deploy={deploy_ok}")
    return True


# ── Pipeline complet ──────────────────────────────────────────────────────── #

def self_update_cycle(file_path: str, new_code: str, message: str, log_fn) -> dict:
    """
    Enchaîne : write_and_test → git_commit_and_push → deploy_to_pi.
    S'arrête à la première étape en échec.
    Retourne un dict résumant l'exécution.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_fn(f"[SELF_UPDATE] Cycle démarré — {file_path} — {ts}")

    result = {
        "file_path":   file_path,
        "ts":          ts,
        "write_ok":    False,
        "commit_ok":   False,
        "deploy_ok":   False,
        "rolled_back": False,
        "error":       "",
    }

    if not write_and_test(file_path, new_code, log_fn):
        result["error"] = "Syntaxe invalide ou fichier protégé"
        return result
    result["write_ok"] = True

    if not git_commit_and_push(file_path, message, log_fn):
        result["error"] = "Échec git commit/push"
        return result
    result["commit_ok"] = True

    result["deploy_ok"] = deploy_to_pi(log_fn)
    if not result["deploy_ok"]:
        result["error"] = "Échec déploiement Pi (SSH)"

    # ── Vérification post-déploiement : tests de sécurité ───────────────── #
    if not _run_tests(log_fn):
        log_fn("[SELF_UPDATE] ROLLBACK — tests cassés après update, commit annulé")
        result["rolled_back"] = _run_rollback(log_fn)
        result["error"] = (
            "Rollback effectué — tests échoués après update"
            if result["rolled_back"]
            else "Tests échoués + rollback impossible"
        )

    log_fn(
        f"[SELF_UPDATE] Cycle terminé — write={result['write_ok']} "
        f"commit={result['commit_ok']} deploy={result['deploy_ok']} "
        f"rolled_back={result['rolled_back']}"
    )
    return result


# ── Garde post-update ─────────────────────────────────────────────────────── #

def check_post_update_health(
    log_fn,
    window_minutes: int = 10,
    max_errors: int = 3,
) -> dict:
    """
    Garde de sécurité à appeler périodiquement après un update.
    Lit logs/jkai.log sur les window_minutes dernières minutes et compte
    les erreurs fatales. Si ≥ max_errors, déclenche un rollback automatique.

    Retourne {"error_count": int, "rolled_back": bool}.
    """
    cutoff      = datetime.now() - timedelta(minutes=window_minutes)
    error_count = 0

    try:
        with open(_JKAI_LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return {"error_count": 0, "rolled_back": False}

    for line in lines:
        if len(line) > 21 and line[0] == "[":
            try:
                ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts < cutoff:
                continue
            if _FATAL_RE.search(line):
                error_count += 1

    rolled_back = False
    if error_count >= max_errors:
        log_fn(
            f"[SELF_UPDATE] Garde post-update : {error_count} erreurs fatales "
            f"en {window_minutes} min — rollback déclenché"
        )
        rolled_back = _run_rollback(log_fn)

    return {"error_count": error_count, "rolled_back": rolled_back}
