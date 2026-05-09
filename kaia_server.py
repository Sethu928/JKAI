from flask import Flask, jsonify, request
import json
import sqlite3
from sqlite3 import Error
import random

app = Flask(__name__)

class Kaia:
    def __init__(self):
        self.memory = {}
        self.rules = {
            'hello': ['Bonjour, comment ça va?', 'Salut, comment tu vas?'],
            'goodbye': ['Au revoir, à bientôt!', 'À demain!']
        }
        self.conversations = []
        self.create_database()

    def create_database(self):
        try:
            conn = sqlite3.connect('memory/conversations.db')
            c = conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY,
                        user TEXT,
                        message TEXT
                    )""")
            conn.commit()
        except Error as e:
            print(e)

    def add_conversation(self, user, message):
        self.conversations.append({'user': user, 'message': message})
        with open('memory/conversations.db', 'a') as f:
            f.write(f"{user},{message}\n")

    def get_response(self, user_message):
        for rule, responses in self.rules.items():
            if rule in user_message:
                return random.choice(responses)
        return "Je suis désolé, je ne comprends pas."

    def learn(self, conversation):
        message = conversation['message']
        self.add_rule(message, ['Réponse 1', 'Réponse 2'])

    def add_rule(self, message, responses):
        if message not in self.rules:
            self.rules[message] = responses

def main():
    kaia = Kaia()
    @app.route('/chat', methods=['POST'])
    def chat():
        user_message = request.get_json()['message']
        conversation = {'user': 'User', 'message': user_message}
        response = kaia.get_response(user_message)
        print(f"Kaïa: {response}")
        kaia.learn(conversation)
        return jsonify({'response': response})

    app.run(port=5001, debug=True)

if __name__ == '__main__':
    main()