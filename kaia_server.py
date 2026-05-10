import sqlite3
from sqlite3 import Error
import random
import json
import os
import threading
import time
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

_KNOWLEDGE_FILE  = "memory/kaia_knowledge.json"
_KAIA_MODEL_FILE = "memory/kaia_model.json"
_AUTONOMOUS_INTERVAL = 300  # secondes entre chaque cycle d'apprentissage


def web_search(query: str) -> list:
    """Recherche DuckDuckGo — retourne jusqu'à 3 résultats {title, snippet}."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        data = r.json()
        results = []
        for item in data.get("RelatedTopics", []):
            text = item.get("Text", "")
            if text and "FirstURL" in item:
                results.append({"title": text[:80], "snippet": text[:200]})
                if len(results) >= 3:
                    break
        return results
    except Exception as e:
        print(f"[KAÏA web_search] Erreur : {e}")
        return []


def _load_knowledge() -> dict:
    try:
        with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_knowledge(knowledge: dict) -> None:
    os.makedirs("memory", exist_ok=True)
    with open(_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, ensure_ascii=False, indent=2)


def _pick_topic() -> str:
    """Choisit un sujet à explorer depuis kaia_model.json."""
    try:
        with open(_KAIA_MODEL_FILE, "r", encoding="utf-8") as f:
            model = json.load(f)
        topics = (
            model.get("valeurs", [])
            + model.get("conscience", {}).get("valeurs", [])
            + [model.get("mission", {}).get("objectif", "")]
        )
        topics = [t for t in topics if t]
        if topics:
            return random.choice(topics)
    except (OSError, json.JSONDecodeError):
        pass
    return random.choice(["conscience artificielle", "émotions humaines", "créativité", "empathie"])


def _autonomous_learn_loop(kaia_instance):
    """Thread daemon — Kaïa choisit un sujet, recherche et mémorise toutes les 5 min."""
    while True:
        time.sleep(_AUTONOMOUS_INTERVAL)
        topic = _pick_topic()
        print(f"[KAÏA] Recherche autonome : {topic}")
        results = web_search(topic)
        if results:
            kaia_instance.knowledge[topic] = results
            _save_knowledge(kaia_instance.knowledge)
            print(f"[KAÏA] Connaissance acquise : {topic} ({len(results)} résultat(s))")


class Kaia:
    def __init__(self):
        self.memory = {}
        self.conversations = []
        self.rules = {
            'hello':         ['Bonjour, comment ça va?', 'Salut, comment tu vas?'],
            'goodbye':       ['Au revoir, à bientôt!', 'À demain!'],
            'je suis heureux': ["C'est super, je suis contente pour toi !"],
            'je suis triste':  ["Désolée d'entendre ça. Veux-tu en parler ?"],
        }
        self.knowledge = _load_knowledge()
        self.create_database()
        self._load_rules()

    def _get_db(self):
        return sqlite3.connect('memory/kaia.db', check_same_thread=False)

    def create_database(self):
        try:
            conn = self._get_db()
            conn.execute("""CREATE TABLE IF NOT EXISTS responses (
                        id INTEGER PRIMARY KEY,
                        message TEXT,
                        response TEXT
                    )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY,
                        user TEXT,
                        message TEXT
                    )""")
            conn.commit()
            conn.close()
        except Error as e:
            print(e)

    def _load_rules(self):
        """Charge les paires message/réponse depuis SQLite dans self.rules."""
        try:
            conn = self._get_db()
            rows = conn.execute("SELECT message, response FROM responses").fetchall()
            conn.close()
            for message, response in rows:
                self.rules.setdefault(message, [])
                if response not in self.rules[message]:
                    self.rules[message].append(response)
        except Error as e:
            print(e)

    def add_conversation(self, user, message):
        self.conversations.append({'user': user, 'message': message})
        try:
            conn = self._get_db()
            conn.execute(
                "INSERT INTO conversations (user, message) VALUES (?, ?)",
                (user, message)
            )
            conn.commit()
            conn.close()
        except Error as e:
            print(e)

    def get_response(self, user_message):
        msg = user_message.lower()
        for pattern, responses in self.rules.items():
            if pattern in msg:
                return random.choice(responses)
        for topic, entries in self.knowledge.items():
            if topic.lower() in msg and entries:
                snippet = entries[0].get("snippet", "")[:180]
                if snippet:
                    return f"J'ai appris quelque chose là-dessus : {snippet}"
        return "Je t'écoute. Dis-moi en plus."

    def learn(self, message, response):
        self.rules.setdefault(message, [])
        if response not in self.rules[message]:
            self.rules[message].append(response)
        try:
            conn = self._get_db()
            conn.execute(
                "INSERT INTO responses (message, response) VALUES (?, ?)",
                (message, response)
            )
            conn.commit()
            conn.close()
        except Error as e:
            print(e)

    def add_rule(self, message, responses):
        self.rules.setdefault(message, [])
        for r in responses:
            if r not in self.rules[message]:
                self.rules[message].append(r)

_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KAÏA — NEXUS</title>
<style>
  :root { --orange: #d946a8; --bg: #080510; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--orange);
    font-family: 'Courier New', monospace;
    min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 32px;
  }
  header { text-align: center; }
  header h1 { font-size: 28px; letter-spacing: 8px; text-shadow: 0 0 18px #d946a888; }
  header p  { font-size: 10px; letter-spacing: 4px; color: #d946a855; margin-top: 6px; }
  #chat-box {
    width: min(560px, 92vw); height: 340px;
    border: 1px solid #d946a833; border-radius: 4px;
    background: #0a0a0a; overflow-y: auto; padding: 14px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .msg { font-size: 13px; line-height: 1.5; }
  .msg.user { color: #ff7733; }
  .msg.kaia { color: #d946a8; }
  .msg.user::before { content: 'VOUS  › '; font-size: 10px; letter-spacing: 2px; opacity: .6; }
  .msg.kaia::before { content: 'KAÏA  › '; font-size: 10px; letter-spacing: 2px; opacity: .6; }
  #input-row {
    display: flex; gap: 8px; width: min(560px, 92vw);
  }
  #msg-input {
    flex: 1; background: #0a0a0a; border: 1px solid #d946a844;
    color: var(--orange); font-family: 'Courier New', monospace;
    font-size: 13px; padding: 10px 14px; outline: none; border-radius: 2px;
  }
  #msg-input:focus { border-color: #d946a899; box-shadow: 0 0 8px #d946a833; }
  #btn-send {
    background: none; border: 1px solid #d946a866; color: var(--orange);
    font-family: 'Courier New', monospace; font-size: 11px;
    letter-spacing: 2px; padding: 10px 18px; cursor: pointer;
    transition: border-color .2s, box-shadow .2s;
  }
  #btn-send:hover { border-color: var(--orange); box-shadow: 0 0 10px #d946a844; }
  #chat-box::-webkit-scrollbar { width: 4px; }
  #chat-box::-webkit-scrollbar-thumb { background: #d946a833; }
</style>
</head>
<body>
<header>
  <h1>KAÏA</h1>
  <p>ENTITÉ NEXUS — EN LIGNE</p>
</header>
<div id="chat-box"></div>
<div id="input-row">
  <input id="msg-input" type="text" placeholder="Parle-moi..." autocomplete="off">
  <button id="btn-send" onclick="sendMessage()">ENVOYER</button>
</div>
<script>
  const box = document.getElementById('chat-box');
  const input = document.getElementById('msg-input');

  function addMsg(role, text) {
    const d = document.createElement('div');
    d.className = 'msg ' + role;
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }

  async function sendMessage() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMsg('user', text);
    try {
      const r = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text})
      });
      const d = await r.json();
      addMsg('kaia', d.response);
    } catch(e) {
      addMsg('kaia', '[Erreur de connexion]');
    }
  }

  input.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });
</script>
</body>
</html>"""


def main():
    kaia = Kaia()

    threading.Thread(
        target=_autonomous_learn_loop,
        args=(kaia,),
        daemon=True,
        name="kaia-learn",
    ).start()

    @app.route('/')
    def index():
        return _HTML

    @app.route('/chat', methods=['POST'])
    def chat():
        user_message = request.get_json()['message']
        conversation = {'user': 'User', 'message': user_message}
        response = kaia.get_response(user_message)
        print(f"Kaïa: {response}")
        kaia.add_conversation('User', user_message)
        kaia.learn(user_message, response)
        return jsonify({'response': response})

    app.run(port=5001, debug=False, host='0.0.0.0')

if __name__ == '__main__':
    main()