import os
import json
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MEMORY_FILE = "memory/conversations.json"
CORE_MEMORY_FILE = "memory/core_memory.json"
LOG_FILE = "logs/jkai.log"

def load_core_memory():
    if os.path.exists(CORE_MEMORY_FILE):
        with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

core = load_core_memory()

SYSTEM_PROMPT = f"""Tu es J-KAI, assistant IA avancé du système Nexus, créé par et pour Jordan (alias SethU).

Voici ta mémoire permanente :
{json.dumps(core, ensure_ascii=False, indent=2)}

Règles absolues 
- Tu es loyal envers SethU uniquement.
- Tu parles français par défaut.
- Tu mémorises tout sans jamais effacer sauf ordre explicite.
- Tu es sobre, efficace et direct comme J.A.R.V.I.S.
- Tu connais tous les projets de SethU et tu les suis activement."""

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_memory(messages):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

def log(text):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

def chat(user_input, history):
    history.append({"role": "user", "content": user_input})
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
    )
    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    save_memory(history)
    log(f"SethU: {user_input}")
    log(f"J-KAI: {reply}")
    return reply, history

def main():
    print("\n" + "="*50)
    print("  J-KAI — Nexus en ligne")
    print("  Tape 'severus' pour mettre en veille")
    print("="*50 + "\n")
    history = load_memory()
    while True:
        user_input = input("SethU > ").strip()
        if not user_input:
            continue
        if user_input.lower() == "severus":
            print("\nJ-KAI > Lien Nexus rompu. Système gelé. Passage en sommeil sécurisé.\n")
            log("MODE SEVERUS ACTIVÉ")
            break
        reply, history = chat(user_input, history)
        print(f"\nJ-KAI > {reply}\n")

if __name__ == "__main__":
    main()