import os
import json
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("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"))

MEMORY_FILE = "memory/conversations.json"
CORE_MEMORY_FILE = "memory/core_memory.json"
LOG_FILE = "logs/jkai.log"

SYSTEM_PROMPT = """Tu es J-KAI, un assistant IA avancé créé par et pour Jordan (alias SethU).
Tu es loyal, intelligent, autonome et précis.
Tu te souviens de tout ce que Jordan te dit.
Tu parles toujours en français sauf si on te demande autre chose.
Tu es le noyau central d'un système appelé Nexus.
Tu as une personnalité sobre, efficace et directe — comme J.A.R.V.I.S."""

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