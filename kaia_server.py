from flask import Flask, jsonify
import json
import os

app = Flask(__name__)

# Chargement de la mémoire de Kaïa
with open('memory/kaia_model.json', 'r') as f:
    kaia_memory = json.load(f)

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

if __name__ == '__main__':
    app.run(port=5001, debug=True)