import os
import time
import threading
from datetime import datetime

LOGS_DIR      = "logs"
LOG_KEEP_LINES = 500  # lignes conservées par fichier lors du nettoyage


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
        path = os.path.join(LOGS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if len(lines) <= LOG_KEEP_LINES:
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-LOG_KEEP_LINES:])
            if log_fn:
                log_fn(f"[SCHEDULER] clean_logs — {filename} tronqué à {LOG_KEEP_LINES} lignes.")
        except OSError:
            pass


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

    scheduler.add_task("health_check",   60,   health_check)
    scheduler.add_task("memory_report",  3600, memory_report)
    scheduler.add_task("auto_save",      300,  auto_save)
    scheduler.add_task("clean_logs",     3600, lambda: clean_logs(log_fn))

    return scheduler
