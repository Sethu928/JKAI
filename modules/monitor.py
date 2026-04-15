import os
import re
import time
import threading
from collections import defaultdict
from datetime import datetime

LOG_FILE       = "logs/jkai.log"
MONITOR_LOG    = "logs/monitor.log"
CHECK_INTERVAL = 30     # secondes entre chaque lecture
WINDOW         = 600    # fenêtre d'analyse : 10 minutes
THRESHOLD      = 3      # nombre d'occurrences avant alerte

ERROR_KEYWORDS = ("ERROR", "ERREUR", "Exception", "Traceback")

# Retire le préfixe [YYYY-MM-DD HH:MM:SS] pour normaliser la clé d'erreur
_TS_PATTERN = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*')


class Monitor:
    """
    Surveille logs/jkai.log en temps réel.
    Lit les nouvelles lignes toutes les 30 s, détecte les erreurs répétées
    et écrit une alerte dans logs/monitor.log si le seuil est dépassé.
    """

    def __init__(self):
        self._stop_event  = threading.Event()
        self._thread: threading.Thread | None = None
        self._file_pos    = 0                       # position de lecture dans le log
        self._occurrences: dict[str, list[float]] = defaultdict(list)  # {clé: [timestamps]}
        self._alerted: set[str] = set()             # clés déjà alertées (évite le spam)

    # ------------------------------------------------------------------ #
    #  API publique                                                        #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Démarre le thread de surveillance. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        # Commence à lire depuis la fin du fichier courant
        # (ignore l'historique pour ne surveiller que ce qui arrive ensuite)
        self._file_pos = self._current_size()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="NexusMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        """Arrête proprement le thread de surveillance."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    # ------------------------------------------------------------------ #
    #  Lecture du log                                                      #
    # ------------------------------------------------------------------ #

    def _current_size(self) -> int:
        try:
            return os.path.getsize(LOG_FILE)
        except OSError:
            return 0

    def _read_new_lines(self) -> list[str]:
        """Lit uniquement les octets ajoutés depuis la dernière lecture."""
        size = self._current_size()

        # Détection de rotation / troncature du fichier
        if size < self._file_pos:
            self._file_pos = 0

        if size == self._file_pos:
            return []

        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._file_pos)
                content = f.read()
                self._file_pos = f.tell()
            return [l for l in content.splitlines() if l.strip()]
        except OSError:
            return []

    # ------------------------------------------------------------------ #
    #  Analyse des erreurs                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_error(line: str) -> bool:
        return any(kw in line for kw in ERROR_KEYWORDS)

    @staticmethod
    def _normalize(line: str) -> str:
        """Retire le timestamp pour obtenir une clé d'erreur comparable."""
        return _TS_PATTERN.sub("", line).strip()

    def _write_alert(self, key: str, count: int) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs("logs", exist_ok=True)
        with open(MONITOR_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"[{ts}] ALERTE NEXUS — erreur répétée {count} fois en moins de 10 min\n"
                f"  >> {key}\n"
                f"{'─' * 72}\n"
            )

    # ------------------------------------------------------------------ #
    #  Boucle principale                                                   #
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now   = time.time()
            lines = self._read_new_lines()

            # Enregistre les nouvelles occurrences d'erreur
            for line in lines:
                if self._is_error(line):
                    key = self._normalize(line)
                    self._occurrences[key].append(now)

            # Évalue chaque clé connue
            for key in list(self._occurrences):
                # Ne garder que les timestamps dans la fenêtre active
                recent = [t for t in self._occurrences[key] if now - t < WINDOW]
                self._occurrences[key] = recent

                if not recent:
                    del self._occurrences[key]
                    self._alerted.discard(key)
                    continue

                if len(recent) > THRESHOLD and key not in self._alerted:
                    try:
                        self._write_alert(key, len(recent))
                    except OSError:
                        pass
                    self._alerted.add(key)
                elif len(recent) <= THRESHOLD:
                    # L'erreur s'est calmée — on réarme l'alerte
                    self._alerted.discard(key)

            self._stop_event.wait(CHECK_INTERVAL)
