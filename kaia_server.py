import json
import sqlite3
from sqlite3 import Error
import random
import numpy as np
from sklearn.naive_bayes import MultinomialNB
from flask import Flask, jsonify, request

app = Flask(__name__)

class Kaia:
    def __init__(self):
        self.memory = {}
        self.rules = {
            'hello': ['Bonjour, comment ça va?', 'Salut, comment tu vas?'],
            'goodbye': ['Au revoir, à bientôt!', 'À demain!']
        }
        self.conversations = []
        self.responses_db = sqlite3.connect('memory/responses.db')
        c = self.responses_db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS responses (
                    id INTEGER PRIMARY KEY,
                    message TEXT,
                    response TEXT
                )""")
        self.model = MultinomialNB()

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
        try:
            conn = sqlite3.connect('memory/conversations.db')
            conn.execute(
                "INSERT INTO conversations (user, message) VALUES (?, ?)",
                (user, message)
            )
            conn.commit()
            conn.close()
        except Error as e:
            print(e)

    def get_response(self, user_message):
        if 'je suis heureux' in user_message:
            return "C'est super, je suis content pour toi !"
        elif 'je suis triste' in user_message:
            return "Désolé à entendre cela. Veux-tu parler de ce qui te dérange ?"
        return "Je t'écoute. Dis-moi en plus."

    def get_features(self, message):
        words = message.split()
        features = []
        for word in words:
            features.append(1)
        return np.array(features)

    def learn(self, conversation, response):
        message = conversation['message']
        cursor = self.responses_db.cursor()
        cursor.execute(
            "INSERT INTO responses (message, response) VALUES (?, ?)",
            (message, response)
        )
        self.responses_db.commit()
        self.model.fit(self.get_features(message), [1])

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
        kaia.learn(conversation, response)
        return jsonify({'response': response})

    app.run(port=5001, debug=True)

if __name__ == '__main__':
    main()