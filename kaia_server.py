import sqlite3
from sqlite3 import Error
import random
from flask import Flask, jsonify, request

app = Flask(__name__)

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
  :root { --orange: #ff5500; --bg: #050505; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--orange);
    font-family: 'Courier New', monospace;
    min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 32px;
  }
  header { text-align: center; }
  header h1 { font-size: 28px; letter-spacing: 8px; text-shadow: 0 0 18px #ff550088; }
  header p  { font-size: 10px; letter-spacing: 4px; color: #ff550055; margin-top: 6px; }
  #chat-box {
    width: min(560px, 92vw); height: 340px;
    border: 1px solid #ff550033; border-radius: 4px;
    background: #0a0a0a; overflow-y: auto; padding: 14px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .msg { font-size: 13px; line-height: 1.5; }
  .msg.user { color: #ff7733; }
  .msg.kaia { color: #ff5500; }
  .msg.user::before { content: 'VOUS  › '; font-size: 10px; letter-spacing: 2px; opacity: .6; }
  .msg.kaia::before { content: 'KAÏA  › '; font-size: 10px; letter-spacing: 2px; opacity: .6; }
  #input-row {
    display: flex; gap: 8px; width: min(560px, 92vw);
  }
  #msg-input {
    flex: 1; background: #0a0a0a; border: 1px solid #ff550044;
    color: var(--orange); font-family: 'Courier New', monospace;
    font-size: 13px; padding: 10px 14px; outline: none; border-radius: 2px;
  }
  #msg-input:focus { border-color: #ff550099; box-shadow: 0 0 8px #ff550033; }
  #btn-send {
    background: none; border: 1px solid #ff550066; color: var(--orange);
    font-family: 'Courier New', monospace; font-size: 11px;
    letter-spacing: 2px; padding: 10px 18px; cursor: pointer;
    transition: border-color .2s, box-shadow .2s;
  }
  #btn-send:hover { border-color: var(--orange); box-shadow: 0 0 10px #ff550044; }
  #chat-box::-webkit-scrollbar { width: 4px; }
  #chat-box::-webkit-scrollbar-thumb { background: #ff550033; }
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