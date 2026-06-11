"""
Configuration pytest globale.
Injecte les variables d'environnement factices AVANT que pytest
importe le moindre module du projet, évitant ainsi les erreurs
de client OpenAI/Tavily lors des tests unitaires.
"""
import os

os.environ.setdefault("OPENAI_API_KEY",  "test-fake-key-smoke")
os.environ.setdefault("TAVILY_API_KEY",  "test-fake-key-smoke")
os.environ.setdefault("LM_STUDIO_URL",   "http://localhost:9999/v1")
os.environ.setdefault("LM_STUDIO_MODEL", "test-model")
