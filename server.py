import sys
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI

print("Démarrage...", flush=True)
sys.stdout.flush()

load_dotenv()
client = OpenAI(api_key=os.getenv("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"))

server = Flask(__name__)
CORS(server)

MEMORY_FILE = "memory/conversations.json"
CORE_MEMORY_FILE = "memory/core_memory.json"
LOG_FILE = "logs/jkai.log"

def load_core_memory():
    if os.path.exists(CORE_MEMORY_FILE):
        with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_memory(messages):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

def log(text):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

core = load_core_memory()
SYSTEM_PROMPT = f"""Tu es J-KAI, assistant IA avancé du système Nexus, créé par et pour Jordan (alias SethU).
Tu es sobre, efficace, direct et loyal — comme J.A.R.V.I.S.
Tu parles toujours en français par défaut.
Tu mémorises tout sans jamais effacer sauf ordre explicite.

Voici ta mémoire permanente :
{json.dumps(core, ensure_ascii=False, indent=2)}"""

@server.route("/")
def index():
    return send_from_directory(".", "index.html")

@server.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_input = data.get("message", "")
    history = load_memory()
    history.append({"role": "user", "content": user_input})
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
    )
    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    save_memory(history)
    log(f"SethU: {user_input}")
    log(f"J-KAI: {reply}")
    return jsonify({"reply": reply})

@server.route("/history", methods=["GET"])
def history():
    return jsonify(load_memory())

@server.route("/clear", methods=["POST"])
def clear():
    save_memory([])
    return jsonify({"status": "cleared"})

if __name__ == "__main__":
    server.run(debug=True, port=5000)