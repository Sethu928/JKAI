import os
import signal
import threading
import hmac
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
# Mot de passe d'arrêt — défini dans .env sous KS_PASSWORD, "nexus_off" par défaut
_KS_PASSWORD = os.getenv("KS_PASSWORD", "nexus_off")
_KS_LOG      = "logs/killswitch.log"


def _write_ks_log(timestamp: str) -> None:
    os.makedirs("logs", exist_ok=True)
    with open(_KS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] KILLSWITCH ACTIVÉ\n")


def _shutdown_after(delay: float = 0.6) -> None:
    """Envoie SIGINT au processus après un court délai pour laisser
    Flask renvoyer la réponse HTTP avant de s'arrêter."""
    def _kill():
        os.kill(os.getpid(), signal.SIGINT)
    threading.Timer(delay, _kill).start()


def register_killswitch(app, log_fn) -> None:
    """
    Enregistre la route secrète /ks sur l'application Flask.
    La route n'apparaît dans aucun template ni dans l'interface.

    Corps attendu :
        { "password": "<mot_de_passe>" }

    Réponses :
        401  — mot de passe incorrect (réponse volontairement neutre)
        200  — arrêt planifié, logs écrits
    """
    from flask import request, jsonify

    @app.route("/ks", methods=["POST"])
    def _killswitch():
        data = request.get_json(silent=True) or {}
        received = data.get("password", "")

        # Comparaison en temps constant — résistance aux timing attacks
        if not hmac.compare_digest(received, _KS_PASSWORD):
            return jsonify({"status": "unauthorized"}), 401

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_fn(f"[KILLSWITCH] Arrêt autorisé — Nexus en cours d'extinction.")
        _write_ks_log(ts)
        _shutdown_after(0.6)

        return jsonify({"status": "nexus_off", "timestamp": ts})
