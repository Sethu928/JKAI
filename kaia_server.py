from flask import Flask, jsonify, request
import json
import os
import sqlite3
from sqlite3 import Error
import random

app = Flask(__name__)

# Chargement de la mémoire de Kaïa
with open('memory/kaia_model.json', 'r') as f:
    kaia_memory = json.load(f)

# Création d'une base de données SQLite pour les conversations
conn = None
try:
    conn = sqlite3.connect('memory/conversations.db')
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY,
                user TEXT,
                message TEXT
            )""")
except Error as e:
    print(e)

# Définition des routes pour les requêtes
@app.route('/get-memory', methods=['GET'])
def get_memory():
    return jsonify(kaia_memory)

@app.route('/set-value', methods=['POST'])
def set_value():
    data = request.get_json()
    kaia_memory[data['key']] = data['value']
    with open('memory/kaia_model.json', 'w') as f:
        json.dump(kaia_memory, f)
    return jsonify({'message': 'Value updated successfully'})

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.get_json()['message']
    conversation_id = random.randint(1, 1000)

    # Insertion de la conversation dans la base de données
    c.execute("INSERT INTO conversations (user, message) VALUES (?, ?)", (request.remote_addr, user_message))
    conn.commit()

    # Récupération des réponses possibles pour Kaïa
    possible_responses = []
    with open('memory/kaia_model.json', 'r') as f:
        kaia_data = json.load(f)
    for response in kaia_data['personnalite']['caracteristiques']:
        possible_responses.append(response)

    # Sélection aléatoire d'une réponse
    selected_response = random.choice(possible_responses)

    return jsonify({'response': selected_response})

if __name__ == '__main__':
    app.run(port=5001, debug=True)