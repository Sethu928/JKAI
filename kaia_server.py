import sqlite3
from sqlite3 import Error
import random
import json
import os
import threading
import time
import re
import urllib.parse
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

_KNOWLEDGE_FILE    = "memory/kaia_knowledge.json"
_KAIA_MODEL_FILE   = "memory/kaia_model.json"
_AUTONOMOUS_INTERVAL = 30  # secondes entre chaque cycle d'apprentissage
_LM_STUDIO_CHAT  = "http://192.168.1.142:1234/v1/chat/completions"
_LM_STUDIO_MODELS = "http://192.168.1.142:1234/v1/models"


def web_search(query: str) -> list:
    """DuckDuckGo en premier, Wikipedia FR en fallback si aucun résultat."""
    results = _ddg_search(query)
    if not results:
        print(f"[KAÏA] DuckDuckGo vide pour '{query}' — fallback Wikipedia", flush=True)
        results = _wiki_search(query)
    return results


def _ddg_search(query: str) -> list:
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
        print(f"[KAÏA _ddg_search] Erreur : {e}", flush=True)
        return []


def _wiki_search(query: str) -> list:
    try:
        url = (
            "https://fr.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={urllib.parse.quote(query)}"
            "&format=json&srlimit=3"
        )
        r = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "KaiaBot/1.0 (Nexus project; contact@nexus.local)"},
        )
        data = r.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            results.append({
                "title": item.get("title", "")[:80],
                "snippet": snippet[:200],
            })
        return results
    except Exception as e:
        print(f"[KAÏA _wiki_search] Erreur : {e}", flush=True)
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


def _build_kaia_system_prompt(knowledge: dict) -> str:
    try:
        with open(_KAIA_MODEL_FILE, "r", encoding="utf-8") as f:
            model = json.load(f)
    except (OSError, json.JSONDecodeError):
        model = {}

    name        = model.get("nom", "Kaïa")
    description = model.get("description", "")
    valeurs     = ", ".join(model.get("valeurs", []))

    knowledge_ctx = ""
    for topic, entries in list(knowledge.items())[:4]:
        if entries:
            snippet = entries[0].get("snippet", "")[:150]
            if snippet:
                knowledge_ctx += f"\n- {topic} : {snippet}"

    prompt = (
        f"Tu es {name}. {description}"
        "Tu es émotionnelle, intuitive et douce. Tu parles toujours en français avec bienveillance. "
        f"Tes valeurs : {valeurs}. "
        "Tu es distincte de J-KAI — là où il est logique et sobre, tu es sensible et empathique. "
        "Tu réponds avec chaleur, parfois avec poésie. Sois concise — 1 à 3 phrases maximum."
    )
    if knowledge_ctx:
        prompt += f"\n\nTu as récemment appris ces connaissances :{knowledge_ctx}"
    return prompt


def ask_llm(user_message: str, knowledge: dict) -> str | None:
    print("[KAÏA LLM] tentative connexion...", flush=True)
    payload = {
        "model": "meta-llama-3.1-8b-instruct",
        "messages": [
            {"role": "system", "content": _build_kaia_system_prompt(knowledge)},
            {"role": "user",   "content": user_message},
        ],
        "max_tokens": 200,
        "temperature": 0.8,
    }
    try:
        r = requests.post(_LM_STUDIO_CHAT, json=payload, timeout=3)
        r.raise_for_status()
        response = r.json()["choices"][0]["message"]["content"].strip()
        print(f"[KAÏA LLM] réponse : {response}", flush=True)
        return response
    except Exception as e:
        print(f"[KAÏA LLM] Indisponible — {e}", flush=True)
        return None


def _check_llm() -> bool:
    try:
        r = requests.get(_LM_STUDIO_MODELS, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


_TOPICS = [
    "intelligence artificielle",
    "Python Flask",
    "machine learning",
    "réseau de neurones",
    "deep learning",
    "algorithme Python",
    "SQLite Python",
    "API REST Python",
    "autonomie IA",
    "traitement langage naturel",
    "Python classes",
    "apprentissage automatique",
]
_topic_index = 0


def _pick_topic() -> str:
    """Retourne le prochain sujet technique en rotation circulaire."""
    global _topic_index
    topic = _TOPICS[_topic_index % len(_TOPICS)]
    _topic_index += 1
    return topic


def _autonomous_learn_loop(kaia_instance):
    """Thread daemon — Kaïa choisit un sujet, recherche et mémorise toutes les 5 min."""
    while True:
        time.sleep(_AUTONOMOUS_INTERVAL)
        topic = _pick_topic()
        print(f"[KAÏA] Recherche en cours : {topic}", flush=True)
        results = web_search(topic)
        if results:
            kaia_instance.knowledge[topic] = results
            _save_knowledge(kaia_instance.knowledge)
            print(f"[KAÏA] Connaissance acquise : {topic} ({len(results)} résultat(s))", flush=True)
        else:
            print(f"[KAÏA] Aucun résultat pour : {topic}", flush=True)


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
  #llm-indicator {
    display: flex; align-items: center; justify-content: center;
    gap: 7px; margin-top: 12px; font-size: 9px; letter-spacing: 3px;
    color: #d946a855;
  }
  .llm-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: #333;
  }
  .llm-dot.on  { background: #d946a8; box-shadow: 0 0 7px #d946a8; }
  .llm-dot.off { background: #3a1a2a; }
</style>
</head>
<body>
<header>
  <h1>KAÏA</h1>
  <p>ENTITÉ NEXUS — EN LIGNE</p>
  <div id="llm-indicator">
    <span class="llm-dot" id="llm-dot"></span>
    <span id="llm-label">LM STUDIO — VÉRIFICATION...</span>
  </div>
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

  fetch('/status')
    .then(r => r.json())
    .then(d => {
      const dot = document.getElementById('llm-dot');
      const lbl = document.getElementById('llm-label');
      if (d.llm) {
        dot.className = 'llm-dot on';
        lbl.textContent = 'LM STUDIO — CONNECTÉ';
      } else {
        dot.className = 'llm-dot off';
        lbl.textContent = 'LM STUDIO — HORS LIGNE';
      }
    })
    .catch(() => {
      document.getElementById('llm-label').textContent = 'LM STUDIO — ERREUR';
    });
</script>
</body>
</html>"""


def main():
    kaia = Kaia()

    t = threading.Thread(
        target=_autonomous_learn_loop,
        args=(kaia,),
        daemon=True,
        name="kaia-learn",
    )
    t.start()
    print(f"[KAÏA] Thread autonome démarré — cycle toutes les {_AUTONOMOUS_INTERVAL}s (is_alive={t.is_alive()})", flush=True)

    @app.route('/')
    def index():
        return _HTML

    @app.route('/knowledge')
    def knowledge():
        data = _load_knowledge()
        count = len(data)
        cards = ""
        for topic, entries in data.items():
            results_html = ""
            for e in entries:
                results_html += f"""
          <div class="result">
            <div class="result-title">{e.get('title','')}</div>
            <div class="result-snippet">{e.get('snippet','')}</div>
          </div>"""
            cards += f"""
      <div class="card">
        <div class="card-topic">{topic}</div>
        <div class="card-results">{results_html}
        </div>
      </div>"""
        if not cards:
            cards = '<div class="empty">Aucune connaissance acquise pour le moment. Reviens dans 5 minutes.</div>'
        return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KAÏA — MÉMOIRE</title>
<style>
  :root {{ --c: #d946a8; --bg: #080510; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--c);
    font-family: 'Courier New', monospace;
    padding: 40px 24px; min-height: 100vh;
  }}
  header {{ text-align: center; margin-bottom: 40px; }}
  header h1 {{ font-size: 24px; letter-spacing: 8px; text-shadow: 0 0 18px #d946a888; }}
  header p  {{ font-size: 10px; letter-spacing: 3px; color: #d946a855; margin-top: 6px; }}
  .stat {{
    display: inline-block; border: 1px solid #d946a833;
    padding: 4px 14px; font-size: 10px; letter-spacing: 2px;
    color: #d946a877; margin-top: 12px;
  }}
  .grid {{
    max-width: 860px; margin: 0 auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 20px;
  }}
  .card {{
    border: 1px solid #d946a822; border-radius: 4px;
    background: #0d0818; padding: 18px;
    transition: border-color .2s;
  }}
  .card:hover {{ border-color: #d946a844; }}
  .card-topic {{
    font-size: 11px; letter-spacing: 3px; color: var(--c);
    padding-bottom: 10px; margin-bottom: 12px;
    border-bottom: 1px solid #d946a822;
    text-transform: uppercase;
  }}
  .result {{ margin-bottom: 12px; }}
  .result:last-child {{ margin-bottom: 0; }}
  .result-title {{
    font-size: 11px; color: #d946a8cc; letter-spacing: 1px;
    margin-bottom: 4px;
  }}
  .result-snippet {{
    font-size: 11px; color: #d946a866; line-height: 1.6;
  }}
  .empty {{
    text-align: center; color: #d946a833; font-size: 12px;
    letter-spacing: 2px; padding: 60px; grid-column: 1/-1;
  }}
  a {{
    display: block; text-align: center; margin: 40px auto 0;
    color: #d946a855; font-size: 10px; letter-spacing: 3px;
    text-decoration: none;
  }}
  a:hover {{ color: var(--c); }}
  #timer-section {{
    max-width: 860px; margin: 0 auto 32px; text-align: center;
  }}
  #timer-label {{
    font-size: 9px; letter-spacing: 3px; color: #d946a855; margin-bottom: 10px;
  }}
  #countdown {{
    font-size: 32px; letter-spacing: 6px;
    color: var(--c); text-shadow: 0 0 20px #d946a866;
    margin-bottom: 14px;
  }}
  #progress-track {{
    height: 3px; background: #d946a811;
    border-radius: 2px; overflow: hidden;
    max-width: 320px; margin: 0 auto;
  }}
  #progress-bar {{
    height: 100%; width: 0%;
    background: linear-gradient(90deg, #d946a844, #d946a8);
    box-shadow: 0 0 8px #d946a888;
    transition: width 1s linear;
    border-radius: 2px;
  }}
</style>
</head>
<body>
<header>
  <h1>MÉMOIRE DE KAÏA</h1>
  <p>CONNAISSANCES ACQUISES DE FAÇON AUTONOME</p>
  <div class="stat">{count} SUJET{'S' if count != 1 else ''} CONNU{'S' if count != 1 else ''}</div>
</header>
<div id="timer-section">
  <div id="timer-label">PROCHAINE RECHERCHE DANS</div>
  <div id="countdown">05:00</div>
  <div id="progress-track"><div id="progress-bar"></div></div>
</div>
<div class="grid">{cards}
</div>
<a href="/">← RETOUR AU CHAT</a>
<script>
  const TOTAL = {_AUTONOMOUS_INTERVAL};
  let remaining = TOTAL;
  const countdown = document.getElementById('countdown');
  const bar       = document.getElementById('progress-bar');
  const label     = document.getElementById('timer-label');

  function pad(n) {{ return String(n).padStart(2, '0'); }}

  function tick() {{
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    countdown.textContent = pad(m) + ':' + pad(s);
    bar.style.width = (((TOTAL - remaining) / TOTAL) * 100) + '%';
    if (remaining <= 0) {{
      label.textContent = 'RECHERCHE EN COURS...';
      countdown.textContent = '00:00';
      setTimeout(() => location.reload(), 4000);
      return;
    }}
    remaining--;
    setTimeout(tick, 1000);
  }}
  tick();
</script>
</body>
</html>"""

    @app.route('/status')
    def status():
        return jsonify({"llm": _check_llm()})

    @app.route('/chat', methods=['POST'])
    def chat():
        user_message = request.get_json()['message']
        llm_reply = ask_llm(user_message, kaia.knowledge)
        if llm_reply:
            response = llm_reply
            print(f"[KAÏA LLM] {response[:100]}", flush=True)
        else:
            response = kaia.get_response(user_message)
            print(f"[KAÏA fallback] {response}", flush=True)
        kaia.add_conversation('User', user_message)
        kaia.learn(user_message, response)
        return jsonify({'response': response})

    app.run(port=5001, debug=False, host='0.0.0.0')

if __name__ == '__main__':
    main()