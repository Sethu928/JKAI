import sys
import os
import json
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from memory.db import init_db, save_message, load_history, clear_history, search_history
from modules.voice import speak, listen
from modules.marc import ask_marc
from modules.cortex import ask_cortex
from killswitch import register_killswitch
from modules.scheduler import create_default_scheduler
from modules.monitor import Monitor
from modules.autonomy import analyze_and_act
from modules.consciousness import reflect, get_self_model

print("Démarrage...", flush=True)
sys.stdout.flush()

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

server = Flask(__name__)
CORS(server)

CORE_MEMORY_FILE = "memory/core_memory.json"
LOG_FILE = "logs/jkai.log"

init_db()

def load_core_memory():
    if os.path.exists(CORE_MEMORY_FILE):
        with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def log(text):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

core = load_core_memory()
SYSTEM_PROMPT = f"""Tu es J-KAI, assistant IA avancé du système Nexus, créé par et pour Jordan (alias SethU).
Tu es sobre, efficace, direct et loyal — comme J.A.R.V.I.S.
Tu parles toujours en français par défaut.
Tu mémorises tout sans jamais effacer sauf ordre explicite.
Quand tu réponds à la voix, sois concis — maximum 2-3 phrases.

Voici ta mémoire permanente :
{json.dumps(core, ensure_ascii=False, indent=2)}"""

def ask_jkai(user_input):
    history = load_history()
    save_message("user", user_input)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_input}]
    )
    reply = response.choices[0].message.content
    save_message("assistant", reply)
    log(f"SethU: {user_input}")
    log(f"J-KAI: {reply}")
    return reply

@server.route("/")
def index():
    return send_from_directory(".", "index.html")

@server.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_input = data.get("message", "")
    reply = ask_jkai(user_input)
    return jsonify({"reply": reply})

@server.route("/history", methods=["GET"])
def history():
    return jsonify(load_history())

@server.route("/clear", methods=["POST"])
def clear():
    clear_history()
    return jsonify({"status": "cleared"})

@server.route("/search", methods=["GET"])
def search():
    keyword = request.args.get("q", "")
    return jsonify(search_history(keyword))

@server.route("/marc", methods=["POST"])
def marc():
    data = request.json
    user_input = data.get("message", "")
    history = load_history()
    reply = ask_marc(user_input, history)
    save_message("user", user_input)
    save_message("assistant", reply)
    log(f"SethU → Marc: {user_input}")
    log(f"Marc: {reply}")
    return jsonify({"reply": reply})

@server.route("/voice", methods=["POST"])
def voice():
    user_input = listen()
    if not user_input:
        return jsonify({"status": "rien_entendu"})
    if "severus" in user_input.lower():
        speak("Lien Nexus rompu. Système gelé. Passage en sommeil sécurisé.")
        return jsonify({"status": "severus", "input": user_input})
    reply = ask_jkai(user_input)
    speak(reply)
    return jsonify({"status": "ok", "input": user_input, "reply": reply})

@server.route("/cortex", methods=["POST"])
def cortex():
    data = request.json
    user_input = data.get("message", "")
    result = ask_cortex(user_input)
    log(f"SethU → Cortex: {user_input}")
    log(f"Cortex output: {result.get('output', '')[:200]}")
    return jsonify(result)

@server.route("/autonomy", methods=["POST"])
def autonomy():
    data = request.json
    context = data.get("context", "")
    result = analyze_and_act(context, log)
    return jsonify(result)

@server.route("/consciousness", methods=["GET"])
def consciousness():
    return jsonify(get_self_model())

@server.route("/severus", methods=["POST"])
def severus():
    return jsonify({"status": "severus"})

register_killswitch(server, log)

scheduler = create_default_scheduler(log)
scheduler.add_task("consciousness_reflect", 3600, lambda: reflect(log))
scheduler.start()

monitor = Monitor()
monitor.start()

if __name__ == "__main__":
    server.run(debug=False, port=5000)