import os
import sys
import subprocess
import tempfile
from datetime import datetime

# ── Fichiers protégés — jamais modifiables via self_update ──────────────── #
_PROTECTED = {"killswitch.py", ".env"}

# Fichiers supplémentaires interdits à la suppression
_PROTECTED_DELETE = _PROTECTED | {"server.py", "modules/state.py"}

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

    if os.path.basename(path) in _PROTECTED:
        log_fn(f"[SELF_UPDATE] Fichier protégé — refusé : {os.path.basename(path)}")
        return False

    # Test syntaxe dans un fichier temporaire
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(new_code)
        tmp.close()

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
    except subprocess.TimeoutExpired:
        log_fn(f"[SELF_UPDATE] Timeout py_compile pour {path}")
        return False
    except Exception as e:
        log_fn(f"[SELF_UPDATE] Erreur py_compile : {e}")
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


def git_commit_and_push(file_path: str, message: str, log_fn) -> bool:
    """
    git add <file_path> → git commit -m message → git push.
    Seul le fichier ciblé est stagé — évite de commiter des fichiers non liés.
    Si GITHUB_TOKEN est défini, injecte le token dans l'URL HTTPS de origin
    pour le push, puis restaure l'URL d'origine.
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

    # ── Push avec injection du token si disponible ──────────────────────── #
    token        = os.getenv("GITHUB_TOKEN", "").strip()
    original_url = _get_remote_url()
    url_patched  = False

    if token and original_url.startswith("https://github.com/"):
        auth_url = original_url.replace(
            "https://github.com/",
            f"https://{token}@github.com/",
        )
        _run_git(["remote", "set-url", "origin", auth_url], log_fn)
        url_patched = True
        log_fn("[SELF_UPDATE] Token GitHub injecté dans l'URL remote (temporaire)")

    ok, out = _run_git(["push"], log_fn)
    log_fn(f"[SELF_UPDATE] git push → {'OK' if ok else 'ERREUR'} | {out[:200]}")

    # Restaure l'URL originale (token jamais persisté dans .git/config)
    if url_patched and original_url:
        _run_git(["remote", "set-url", "origin", original_url], log_fn)
        log_fn("[SELF_UPDATE] URL remote restaurée.")

    return ok


# ── Déploiement SSH Pi ────────────────────────────────────────────────────── #

def deploy_to_pi(log_fn) -> bool:
    """
    SSH vers le Raspberry Pi (192.168.1.122 / pi).
    Exécute : git pull && sudo systemctl restart jkai.
    Authentification par clé SSH (~/.ssh/jkai_key).
    Retourne True si succès.
    """
    try:
        import paramiko
    except ImportError:
        log_fn("[SELF_UPDATE] paramiko absent — deploy_to_pi ignoré.")
        return False

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            PI_HOST,
            username=PI_USER,
            key_filename=os.path.expanduser("~/.ssh/jkai_key"),
            timeout=15,
        )
        log_fn(f"[SELF_UPDATE] SSH connecté à {PI_HOST} ({PI_USER})")

        _, stdout, stderr = ssh.exec_command(PI_CMD, timeout=30)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()

        if out:
            log_fn(f"[SELF_UPDATE] Pi stdout : {out[:400]}")
        if err:
            log_fn(f"[SELF_UPDATE] Pi stderr : {err[:400]}")
        log_fn("[SELF_UPDATE] Déploiement Pi terminé.")
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

    normalized = file_path.replace("\\", "/")
    if os.path.basename(file_path) in _PROTECTED_DELETE or normalized in _PROTECTED_DELETE:
        result["error"] = f"Fichier protégé — suppression refusée : {file_path}"
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
        "file_path":  file_path,
        "ts":         ts,
        "write_ok":   False,
        "commit_ok":  False,
        "deploy_ok":  False,
        "error":      "",
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

    log_fn(f"[SELF_UPDATE] Cycle terminé — write={result['write_ok']} "
           f"commit={result['commit_ok']} deploy={result['deploy_ok']}")
    return result
