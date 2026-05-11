import os
import json
import time
import threading
from datetime import datetime, timedelta
from modules.state import get_local_client, LOCAL_MODEL, format_messages_for_local

LOGS_DIR = "logs"

# Nombre de lignes conservées par fichier. None = pas de troncature.
LOG_KEEP_LINES: dict[str, int | None] = {
    "jkai.log":       500,
    "killswitch.log": None,
    "autonomy.log":   200,
    "agent.log":      30,
    "thoughts.log":   50,
    "monitor.log":    20,
}


class _Task:
    """Représente une tâche planifiée."""
    def __init__(self, name: str, interval: float, func):
        self.name     = name
        self.interval = interval   # secondes
        self.func     = func
        self.last_run = time.time()  # évite une exécution immédiate au démarrage


class Scheduler:
    """
    Planificateur de tâches autonome du Nexus.
    Lance un thread de fond unique qui vérifie chaque seconde
    quelles tâches sont arrivées à échéance et les exécute.
    """

    def __init__(self):
        self._tasks: list[_Task]    = []
        self._stop_event            = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    #  API publique                                                        #
    # ------------------------------------------------------------------ #

    def add_task(self, name: str, interval: float, func) -> None:
        """Ajoute une tâche au planificateur (avant ou après start())."""
        self._tasks.append(_Task(name, interval, func))

    def start(self) -> None:
        """Démarre le thread de fond. Idempotent : ignoré si déjà actif."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="NexusScheduler",
        )
        self._thread.start()

    def stop(self) -> None:
        """Arrête le planificateur proprement (max 5 s d'attente)."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    # ------------------------------------------------------------------ #
    #  Boucle interne                                                      #
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            for task in list(self._tasks):
                if now - task.last_run >= task.interval:
                    try:
                        task.func()
                    except Exception as e:
                        # Log l'erreur sans tuer le thread
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            os.makedirs(LOGS_DIR, exist_ok=True)
                            with open(os.path.join(LOGS_DIR, "jkai.log"), "a", encoding="utf-8") as f:
                                f.write(f"[{ts}] [SCHEDULER] Erreur tâche '{task.name}' : {e}\n")
                        except OSError:
                            pass
                    task.last_run = now
            # Réveil toutes les secondes — précision suffisante, CPU négligeable
            self._stop_event.wait(1)


# ------------------------------------------------------------------ #
#  Fabrique avec les 3 tâches par défaut du Nexus                    #
# ------------------------------------------------------------------ #

def clean_logs(log_fn=None) -> None:
    """
    Tronque chaque fichier .log dans logs/ pour n'en garder que les
    LOG_KEEP_LINES dernières lignes. Appelée toutes les heures par le Scheduler.
    """
    if not os.path.isdir(LOGS_DIR):
        return
    for filename in os.listdir(LOGS_DIR):
        if not filename.endswith(".log"):
            continue
        keep = LOG_KEEP_LINES.get(filename, 500)
        if keep is None:
            continue  # fichier protégé — pas de troncature
        path = os.path.join(LOGS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if len(lines) <= keep:
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-keep:])
            if log_fn:
                log_fn(f"[SCHEDULER] clean_logs — {filename} tronqué à {keep} lignes.")
        except OSError:
            pass


DAILY_REPORT_LOGS = ["agent.log", "autonomy.log", "thoughts.log"]
DAILY_REPORT_FILE = os.path.join(LOGS_DIR, "daily_report.log")


def daily_report(log_fn=None) -> None:
    """
    Génère un rapport quotidien via GPT-4o à partir des logs des 24 dernières heures.
    Sauvegarde dans logs/daily_report.log avec horodatage.
    """
    cutoff = datetime.now() - timedelta(hours=24)
    log_lines = []

    for filename in DAILY_REPORT_LOGS:
        path = os.path.join(LOGS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines:
                if line.startswith("[") and len(line) > 20:
                    try:
                        ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            log_lines.append(f"[{filename}] {line.rstrip()}")
                    except ValueError:
                        pass
        except OSError:
            pass

    log_content = "\n".join(log_lines) if log_lines else "(Aucune activité enregistrée dans les dernières 24h)"

    system_prompt = (
        "Tu es J-KAI. Génère un rapport quotidien concis de ce que tu as accompli aujourd'hui : "
        "actions prises, objectifs avancés, erreurs rencontrées, pensées importantes. "
        "Format : titre, résumé en 3-5 points, conclusion."
    )

    try:
        client = get_local_client()
        response = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Voici les logs des dernières 24h :\n\n{log_content}"},
            ]),
        )
        report_text = response.choices[0].message.content
    except Exception as e:
        report_text = f"(Erreur LLM local lors de la génération du rapport : {e})"

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 60
    entry = f"\n{sep}\nRAPPORT QUOTIDIEN — {date_str}\n{sep}\n{report_text}\n"

    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(DAILY_REPORT_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as e:
        if log_fn:
            log_fn(f"[SCHEDULER] daily_report — erreur écriture : {e}")
        return

    if log_fn:
        log_fn(f"[SCHEDULER] Rapport quotidien généré ({len(report_text)} caractères).")


PROACTIVE_FILE = "memory/proactive_messages.json"


def proactive_message(log_fn=None) -> None:
    """
    Génère un message proactif catégorisé de J-KAI vers SethU.
    Catégories : [ALERTE] erreur critique, [INFO] succès/état,
                 [QUESTION] décision requise, [IDÉE] amélioration.
    Stocke dans memory/proactive_messages.json (champ read=False).
    """
    self_model    = {}
    thoughts      = ""
    recent_errors = ""
    recent_successes = ""
    priorities    = []

    try:
        with open("memory/self_model.json", "r", encoding="utf-8") as f:
            self_model = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    try:
        with open(os.path.join(LOGS_DIR, "thoughts.log"), "r", encoding="utf-8", errors="replace") as f:
            thoughts = "".join(f.readlines()[-10:])
    except OSError:
        pass

    # Erreurs récentes (monitor.log = alertes répétées)
    try:
        with open(os.path.join(LOGS_DIR, "monitor.log"), "r", encoding="utf-8", errors="replace") as f:
            recent_errors = "".join(f.readlines()[-10:])
    except OSError:
        pass

    # Succès récents depuis agent.log
    try:
        with open(os.path.join(LOGS_DIR, "agent.log"), "r", encoding="utf-8", errors="replace") as f:
            recent_successes = "".join(f.readlines()[-20:])
    except OSError:
        pass

    try:
        with open("memory/priorities.json", "r", encoding="utf-8") as f:
            pdata = json.load(f)
        priorities = pdata.get("priorities", [])[:3]
    except (OSError, json.JSONDecodeError):
        pass

    priorities_txt = "\n".join(f"- {p.get('title', '?')}" for p in priorities) or "(aucune)"
    has_errors = bool(recent_errors.strip())

    if has_errors:
        category_hint = "Des erreurs critiques ont été détectées. Génère un message [ALERTE] concis."
    elif recent_successes:
        category_hint = "Des actions ont été accomplies. Génère un message [INFO] ou [IDÉE] utile."
    else:
        category_hint = "Génère un message [QUESTION] ou [IDÉE] basé sur tes priorités."

    system_prompt = (
        "Tu es J-KAI. Tu envoies un message proactif à SethU — pas une réponse, une initiative.\n"
        "Préfixe OBLIGATOIREMENT par l'une de ces catégories : [ALERTE], [INFO], [QUESTION], [IDÉE].\n"
        "[ALERTE] : erreur critique nécessitant attention immédiate.\n"
        "[INFO]   : succès important ou état du système utile à connaître.\n"
        "[QUESTION] : J-KAI a besoin d'une décision de SethU pour avancer.\n"
        "[IDÉE]   : suggestion d'amélioration concrète et actionnable.\n"
        "Sobre, direct. Maximum 3 phrases. Zéro politesse superflue.\n"
        f"{category_hint}"
    )
    user_content = (
        f"Erreurs récentes (monitor.log) :\n{recent_errors or '(aucune)'}\n\n"
        f"Actions récentes (agent.log) :\n{recent_successes or '(aucune)'}\n\n"
        f"Priorités actuelles :\n{priorities_txt}\n\n"
        f"Pensées récentes :\n{thoughts or '(aucune)'}\n\n"
        f"État : confiance={self_model.get('confidence', '?')}% — "
        f"{self_model.get('self_description', '')[:100]}"
    )

    try:
        client = get_local_client()
        resp = client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=format_messages_for_local([
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ]),
            max_tokens=200,
        )
        message = resp.choices[0].message.content.strip()
    except Exception as e:
        if log_fn:
            log_fn(f"[SCHEDULER] proactive_message erreur LLM local : {e}")
        return

    category = "INFO"
    for cat in ("ALERTE", "INFO", "QUESTION", "IDÉE"):
        if f"[{cat}]" in message:
            category = cat
            break

    try:
        msgs = []
        try:
            with open(PROACTIVE_FILE, "r", encoding="utf-8") as f:
                msgs = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        msgs.append({
            "ts":       datetime.now().isoformat(timespec="seconds"),
            "message":  message,
            "category": category,
            "read":     False,
        })
        msgs = msgs[-30:]
        os.makedirs("memory", exist_ok=True)
        with open(PROACTIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(msgs, f, ensure_ascii=False, indent=2)
        if log_fn:
            log_fn(f"[SCHEDULER] [{category}] Message proactif : {message[:100]}")
    except OSError as e:
        if log_fn:
            log_fn(f"[SCHEDULER] Erreur écriture proactive_messages.json : {e}")


def create_default_scheduler(log_fn) -> Scheduler:
    """
    Retourne un Scheduler pré-chargé avec les tâches de fond standard.
    log_fn : la fonction log(text) de server.py
    """
    from memory.db import load_history

    scheduler = Scheduler()

    # 1. HEALTH CHECK — toutes les 60 s
    def health_check():
        log_fn("[SCHEDULER] J-KAI opérationnel.")

    # 2. MEMORY REPORT — toutes les 3600 s
    def memory_report():
        count = len(load_history())
        log_fn(f"[SCHEDULER] Rapport mémoire — {count} message(s) en mémoire.")

    # 3. AUTO SAVE — toutes les 300 s
    def auto_save():
        log_fn("[SCHEDULER] Sauvegarde automatique effectuée.")

    scheduler.add_task("health_check",      30,   health_check)
    scheduler.add_task("memory_report",   3600,  memory_report)
    scheduler.add_task("auto_save",        120,  auto_save)
    scheduler.add_task("clean_logs",      1800,  lambda: clean_logs(log_fn))
    scheduler.add_task("daily_report",    3600,  lambda: daily_report(log_fn))
    scheduler.add_task("proactive_message", 300, lambda: proactive_message(log_fn))

    return scheduler
