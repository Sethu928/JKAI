import threading

# Lock partagé entre agent.py et consciousness.py pour les accès à self_model.json.
# Les deux modules tournent dans des threads séparés quand l'autonomie est active.
self_model_lock = threading.Lock()
