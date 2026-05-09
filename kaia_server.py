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

def main():
    kaia = Kaia()
    @app.route('/chat', methods=['POST'])
    def chat():
        user_message = request.get_json()['message']
        conversation = {'user': 'User', 'message': user_message}
        response = kaia.get_response(user_message)
        print(f"Kaïa: {response}")
        kaia.add_conversation('User', user_message)
        kaia.learn(user_message, response)
        return jsonify({'response': response})

    app.run(port=5001, debug=True)

if __name__ == '__main__':
    main()