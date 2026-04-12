from modules.voice import speak, listen

print("J-KAI Voice — test")
print("Parle pendant 5 secondes après 'En écoute...'")

speak("Nexus vocal en ligne. Je vous écoute.")

while True:
    texte = listen()
    if not texte:
        continue
    if "severus" in texte.lower():
        speak("Lien Nexus rompu. Système gelé. Passage en sommeil sécurisé.")
        break
    print(f"Reconnu : {texte}")
    speak(f"Vous avez dit : {texte}")