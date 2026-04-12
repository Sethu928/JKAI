import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from modules.voice import speak, listen
from memory.db import init_db, save_message, load_history

load_dotenv()
client = OpenAI(api_key=os.getenv("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"))

init_db()

CORE_MEMORY_FILE = "memory/core_memory.json"

def load_core_memory():
    if os.path.exists(CORE_MEMORY_FILE):
        with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

core = load_core_memory()
SYSTEM_PROMPT = f"""Tu es J-KAI, assistant IA avancé du système Nexus, créé par et pour Jordan (alias SethU).
Tu es sobre, efficace, direct et loyal — comme J.A.R.V.I.S.
Tu parles toujours en français par défaut.
Quand tu réponds à la voix, sois concis — maximum 2-3 phrases.

Voici ta mémoire permanente :
{json.dumps(core, ensure_ascii=False, indent=2)}"""

def ask_jkai(user_input):
    history = load_history()
    save_message("user", user_input)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_input}]
    )
    reply = response.choices[0].message.content
    save_message("assistant", reply)
    return reply

print("=================================")
print("  J-KAI — Mode Vocal")
print("  Dis 'severus' pour arrêter")
print("=================================\n")

speak("Nexus vocal en ligne. Je vous écoute, SethU.")

while True:
    try:
        user_input = listen()
        if not user_input:
            continue
        if "severus" in user_input.lower():
            speak("Lien Nexus rompu. Système gelé. Passage en sommeil sécurisé.")
            break
        reply = ask_jkai(user_input)
        print(f"J-KAI > {reply}\n")
        speak(reply)
    except KeyboardInterrupt:
        break