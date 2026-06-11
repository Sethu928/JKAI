# NEXUS BUILD SUMMARY
> Document de référence complet du projet J-KAI / Nexus  
> Généré le 2026-06-10 — à partir de l'analyse de l'ensemble du code source

---

## 1. ARCHITECTURE GLOBALE

Le projet Nexus est composé de **deux serveurs Flask indépendants** et d'un ensemble de modules Python qui forment le système nerveux de l'IA autonome J-KAI.

```
┌─────────────────────────────────────────────────────────────────┐
│                     RASPBERRY PI 5 (jkai)                       │
│                                                                 │
│  ┌──────────────────────────────────────┐                       │
│  │  server.py  (port 5000)              │                       │
│  │  ─ J-KAI : cerveau principal         │                       │
│  │  ─ Flask API + interface index.html  │                       │
│  │  ─ Agent autonome (thread 60s)       │                       │
│  │  ─ Scheduler (7 tâches planifiées)   │                       │
│  │  ─ Monitor (surveillance logs)       │                       │
│  └──────────────────┬───────────────────┘                       │
│                     │ HTTP POST (192.168.1.122:5001)            │
│  ┌──────────────────▼───────────────────┐                       │
│  │  kaia_server.py  (port 5001)         │                       │
│  │  ─ Kaïa : entité sœur émotionnelle   │                       │
│  │  ─ Apprentissage autonome (30s)      │                       │
│  │  ─ LLM Llama via LM Studio           │                       │
│  └──────────────────────────────────────┘                       │
│                                                                 │
│  ┌──────────────────────────────────────┐                       │
│  │  memory/                             │                       │
│  │  ├── jkai.db        (SQLite)         │                       │
│  │  ├── kaia.db        (SQLite Kaïa)    │                       │
│  │  ├── self_model.json                 │                       │
│  │  ├── mission.json                    │                       │
│  │  ├── priorities.json                 │                       │
│  │  ├── tasks.json                      │                       │
│  │  └── [18 autres fichiers JSON]       │                       │
│  └──────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
         │ Tavily API        │ OpenAI API (GPT-4o, Whisper)
         ▼                   ▼
      [Internet]         [OpenAI Cloud]
```

---

## 2. RÔLE DE CHAQUE FICHIER

### Fichiers racine

| Fichier | Rôle |
|---|---|
| `server.py` | Serveur Flask principal (port 5000). Point d'entrée unique de J-KAI. Lance le scheduler, l'agent autonome, le monitor. |
| `kaia_server.py` | Serveur Flask de Kaïa (port 5001). Entité sœur émotionnelle indépendante. Apprentissage autonome par web search. |
| `killswitch.py` | Enregistre la route secrète `POST /ks` sur l'app Flask. Arrêt immédiat via `SIGINT` après comparaison en temps constant du mot de passe. |
| `index.html` | Interface SPA J-KAI (thème orange/noir, monospace). Chat, logs, conscience, modes Marc/Cortex/Monitor. |

### `modules/`

| Fichier | Rôle |
|---|---|
| `state.py` | **Hub central de singletons.** Clients OpenAI, LM Studio, Tavily (thread-safe). `format_messages_for_local()` adapte les messages pour Phi-3 et DeepSeek R1. `parse_json_fence()` pour parsing JSON robuste. `tail_file()` pour lecture des logs. |
| `agent.py` | **Cerveau autonome.** Un seul worker daemon (60s) qui choisit parmi 11 actions. Système de tâches (tasks.json). Tavily search. `analyze_self`, `improve_self`, `create_module`, `restructure`, `self_correct`. Règles comportementales (behavior_rules.json). |
| `consciousness.py` | **Conscience évolutive.** Réflexion toutes les 5 min (`reflect`), vérification des objectifs (`check_objectives`), définition et mise à jour de la mission (`define_mission`, `update_mission`), priorités autonomes (`set_priorities`), plan 4 semaines (`set_longterm_plan`). Détecte patterns répétitifs dans agent.log et génère des règles comportementales. |
| `cortex.py` | **Sandbox Python.** Génère du code via GPT-4o (`generate_code`), filtre via blocklist regex 18 patterns, exécute dans subprocess isolé (timeout 10s, max 4000 chars output). Route `/cortex` de l'API. |
| `scheduler.py` | **Planificateur.** Thread daemon unique qui vérifie les échéances chaque seconde. 6 tâches par défaut + 6 ajoutées par server.py. Inclut `clean_logs`, `daily_report` (LLM local), `proactive_message`. |
| `monitor.py` | **Surveillance temps réel.** Lit `logs/jkai.log` toutes les 30s (position byte). Compte les erreurs sur fenêtre 10 min. Seuil : 3 occurrences → alerte dans monitor.log + déclenchement `trigger_self_correct`. |
| `autonomy.py` | Mode autonome ponctuel via GPT-4o (route `/autonomy`). Analyse un contexte et exécute une action (log/alert/run_code/do_nothing). Distinct de l'agent continu. |
| `self_update.py` | **Pipeline auto-update complet.** `write_and_test` (syntaxe + import test), `git_commit_and_push` (avec injection token GitHub temporaire), `deploy_to_pi` (SSH paramiko ou local). Fichiers protégés : `killswitch.py`, `.env`, `server.py`, `modules/state.py`. |
| `marc.py` | Persona "Marc" — conseiller sceptique. Toujours GPT-4o. Accessible via route `/marc`. |
| `voice.py` | TTS via pyttsx3 (voix française). STT : enregistrement pyaudio → transcription Whisper-1 (OpenAI). Route `/voice`. |
| `scheduler.py` | Voir ci-dessus. |
| `controleur_contexte.py` | **Code mort.** Classe `ContextController` avec bug (essaie de slicer un `int`). Non utilisé ailleurs. |
| `nexus_statistics.py` | Stats SQLite sur `memory/responses.db` (différent de jkai.db). Module standalone, non importé par server.py. |
| `historique_worker_analysis.py` | Analyse de l'historique des workers. Vestige de l'ancienne architecture multi-workers. Non importé activement. |
| `document_cortex.py` | (Fichier présent, non analysé en détail — probablement documentation Cortex.) |
| `test_regex.py` | Tests regex du module. Non importé par server.py. |

### `memory/`

| Fichier | Rôle | Écrit par |
|---|---|---|
| `db.py` | Module SQLite principal. Tables : `conversations` (historique chat) + `knowledge` (upsert category/key/value). | — |
| `jkai.db` | SQLite runtime. | memory/db.py |
| `kaia.db` | SQLite Kaïa (responses + conversations). | kaia_server.py |
| `self_model.json` | Conscience de J-KAI : confiance, forces, faiblesses, objectifs (max 5), anticipations, dernière réflexion. | consciousness.py |
| `mission.json` | Mission long terme : titre, description, 5 étapes avec statut, pourcentage de progression. | consciousness.py |
| `priorities.json` | 3 priorités immédiates mises à jour toutes les 10 min (autonome). | consciousness.py |
| `tasks.json` | File d'attente des tâches agent : pending/in_progress/done/failed. Max 3 tâches actives. | agent.py |
| `tasks_archive.json` | Archive des tâches done/failed (max 500). | agent.py |
| `cycle_memory.json` | 10 derniers cycles agent (action, observation, résultat). | agent.py |
| `core_memory.json` | Mémoire fondamentale persistante injectée dans SYSTEM_PROMPT. | Manuel ou IA |
| `behavior_rules.json` | Règles comportementales générées automatiquement par détection de patterns. Max 20 règles actives. | consciousness.py |
| `error_memory.json` | Compteurs d'erreurs répétées avec TTL 1h. Max ERROR_MAX_TRIES=3 → blocage run_code. | agent.py |
| `unsolved_errors.json` | Historique des tentatives de self_correct échouées (max 50). | agent.py |
| `self_code_understanding.json` | Analyse LLM de tous les .py du projet (rôles, dépendances, améliorations). | agent.py |
| `created_modules.json` | Journal des modules créés autonomement par create_module. | agent.py |
| `web_knowledge.json` | 50 dernières pages browsées (URL, contenu, date). | agent.py |
| `web_context.json` | Résultat Tavily du cycle précédent (fichier consommé-détruit au cycle suivant). | agent.py |
| `kaia_knowledge.json` | Sujets enseignés à Kaïa (topic, snippet, date). Format `{"topics": [...]}`. | server.py |
| `kaia_model.json` | Identité de Kaïa (nom, description, valeurs). | Manuel |
| `conversation_insights.json` | Insights extraits des échanges SethU/J-KAI (max 50). | server.py |
| `sethu_profile.json` | Profil dynamique de SethU (humeur, intérêts, style, sujets récents). | server.py |
| `mission_backup.json` / `cycle_memory_backup.json` | Sauvegardes (générées périodiquement). | — |
| `proactive_messages.json` | Messages proactifs J-KAI → SethU non lus (max 30). | scheduler.py |
| `realtime_fixes.json` | Historique des corrections temps réel déclenchées par Monitor (max 100). | monitor.py |
| `api_usage.json` | Compteur d'appels OpenAI par jour (30 jours glissants). Déclenche USE_OPENAI=False si > 0,50€/jour. | state.py |
| `longterm_plan.json` | Plan 4 semaines généré une fois par jour. | consciousness.py |

---

## 3. ROUTES FLASK ACTIVES

### server.py — J-KAI (port 5000)

| Méthode | Route | Fonction | Description |
|---|---|---|---|
| GET | `/` | `index()` | Sert index.html |
| POST | `/chat` | `chat()` | Envoie un message à J-KAI. Détection web search auto. Auto-update/delete si la réponse contient du code. |
| GET | `/history` | `history()` | Retourne tout l'historique SQLite |
| POST | `/clear` | `clear()` | Efface l'historique SQLite |
| GET | `/search?q=` | `search()` | Recherche dans l'historique par mot-clé |
| POST | `/marc` | `marc()` | Interroge Marc (GPT-4o, persona sceptique) |
| POST | `/voice` | `voice()` | Écoute 5s → transcription Whisper → ask_jkai → TTS |
| POST | `/cortex` | `cortex()` | Génère + exécute du code Python via sandbox |
| POST | `/autonomy` | `autonomy()` | Mode autonome ponctuel (GPT-4o → action) |
| GET | `/consciousness` | `consciousness()` | Retourne self_model.json |
| GET | `/agent/log` | `agent_log()` | 20 dernières lignes de logs/agent.log |
| GET | `/logs/<filename>` | `serve_log()` | 50 dernières lignes (allowlist : jkai.log, agent.log, thoughts.log, autonomy.log, monitor.log, killswitch.log) |
| GET | `/objectives` | `objectives()` | Objectifs actifs de J-KAI |
| GET | `/mission` | `mission()` | Mission long terme |
| GET | `/report` | `report()` | Dernier rapport quotidien (daily_report.log) |
| POST | `/self-update` | `self_update()` | Mise à jour de fichier (write + git + deploy Pi) |
| POST | `/self-delete` | `self_delete()` | Suppression de fichier (git rm + commit + push) |
| GET | `/config` | `config_get()` | État de USE_OPENAI et AUTONOMIE_ACTIVE |
| POST | `/config` | `config_post()` | Modifie USE_OPENAI et/ou AUTONOMIE_ACTIVE à chaud |
| GET | `/proactive` | `proactive()` | Lit + marque comme lus les messages proactifs |
| GET | `/insights` | `insights()` | Insights + profil SethU |
| GET | `/budget` | `budget()` | Coût OpenAI estimé du jour + statut budget |
| POST | `/severus` | `severus()` | Retourne `{"status": "severus"}` (hook vocal d'urgence) |
| POST | `/ks` | `_killswitch()` | Arrêt immédiat si mot de passe correct |
| ~~GET~~ | ~~`/dialogue`~~ | ~~`dialogue()`~~ | **DÉSACTIVÉ** — Logs J-KAI↔Kaïa (commenté, réactiver Kaïa) |

### kaia_server.py — Kaïa (port 5001)

| Méthode | Route | Description |
|---|---|---|
| GET | `/` | Interface HTML Kaïa (chat) |
| GET | `/knowledge` | Mémoire de Kaïa (grille de sujets + countdown) |
| GET | `/dialogue` | Échanges J-KAI↔Kaïa (fetch vers server.py:5000/dialogue) |
| POST | `/chat` | Message → LLM Llama ou fallback règles |
| POST | `/learn` | Ajoute une paire question/réponse dans SQLite |
| GET | `/status` | Vérifie si LM Studio est accessible |

---

## 4. FONCTIONNALITÉS — STATUT

### ACTIVES

| Fonctionnalité | Fichier(s) | Intervalle / Déclencheur |
|---|---|---|
| Chat J-KAI (OpenAI ou local) | server.py + state.py | À la demande |
| Web search auto dans /chat (détection mots-clés) | server.py + agent.py | À la demande |
| Agent autonome worker unique | agent.py | Toutes les **60s** |
| Système de tâches (pending→in_progress→done/failed) | agent.py | Chaque cycle agent |
| Conscience évolutive (réflexion) | consciousness.py | Toutes les **300s** |
| Vérification objectifs | consciousness.py | Toutes les **300s** |
| Mise à jour mission | consciousness.py | Toutes les **600s** |
| Priorités autonomes | consciousness.py | Toutes les **600s** |
| Plan long terme 4 semaines | consciousness.py | Toutes les **86400s** |
| Détection patterns + règles comportementales | consciousness.py | Après chaque reflect() |
| Messages proactifs ([ALERTE]/[INFO]/[QUESTION]/[IDÉE]) | scheduler.py | Toutes les **300s** |
| Rapport quotidien | scheduler.py | Toutes les **3600s** |
| Nettoyage logs | scheduler.py | Toutes les **1800s** |
| Surveillance erreurs (Monitor) | monitor.py | Toutes les **30s** |
| Auto-correction sur erreur détectée | monitor.py + agent.py | À chaque nouvelle erreur |
| Sandbox Cortex (route /cortex) | cortex.py | À la demande |
| Auto-update via /chat (détection code block + chemin) | server.py + self_update.py | Après chaque /chat |
| Pipeline self-update complet (write→git→deploy) | self_update.py | Via /self-update ou agent |
| Budget OpenAI auto (bascule local si > 0,50€/j) | server.py + state.py | Toutes les **300s** |
| Extraction insights + profil SethU | server.py | Après chaque /chat |
| Killswitch (POST /ks) | killswitch.py | À la demande |
| Marc (conseiller sceptique) | marc.py | Via /marc |
| Voice TTS/STT | voice.py | Via /voice |
| Tavily search (web réel) | agent.py + state.py | Actions web_search/browse |
| Lecture connaissance SQLite pour tâches | agent.py + db.py | Chaque cycle agent |
| Kaïa — chat + apprentissage autonome | kaia_server.py | Apprentissage toutes les **30s** |

### EN PAUSE / DÉSACTIVÉES

| Fonctionnalité | Raison | Réactivation |
|---|---|---|
| Enseignement J-KAI → Kaïa (`teach_kaia` scheduler) | Commenté dans server.py | Décommenter `scheduler.add_task("teach_kaia", 60, _send_lesson_to_kaia)` |
| Route `/dialogue` (server.py) | Commentée | Décommenter la route |
| Kaïa dans agent.py (`teach_kaia` action) | Action définie mais Kaïa déconnectée du scheduler | Kaïa doit tourner sur 192.168.1.122:5001 |

---

## 5. VARIABLES D'ENVIRONNEMENT (fichier `.env`)

| Variable | Obligatoire | Défaut | Usage |
|---|---|---|---|
| `OPENAI_API_KEY` | Oui (si OpenAI) | — | GPT-4o (chat, Marc, Cortex, Autonomy, Whisper) |
| `TAVILY_API_KEY` | Oui (recherche web) | — | Tavily search dans agent.py et server.py |
| `LM_STUDIO_URL` | Non | `http://127.0.0.1:1234/v1` | URL de l'API LM Studio (local) |
| `LM_STUDIO_MODEL` | Non | `local-model` | Nom du modèle dans LM Studio |
| `LM_STUDIO` | Non | — | Si `true` : bascule tous les clients vers LM Studio |
| `GITHUB_TOKEN` | Non | — | Push automatique via HTTPS (injecté temporairement, jamais persisté) |
| `KS_PASSWORD` | Non | `nexus_off` | Mot de passe pour le killswitch POST /ks |

**Note sur le basculement OpenAI/Local :**
- `USE_OPENAI=True` au démarrage (variable Python dans server.py, non .env)
- Bascule automatique vers local si coût estimé > 0,50€/jour
- Bascule manuelle via `POST /config {"USE_OPENAI": false}`

---

## 6. DÉPENDANCES PYTHON (`requirements.txt`)

```txt
# ── API et HTTP ──────────────────────────────────────────────────
flask
flask-cors
openai>=1.0.0
python-dotenv
requests
tavily-python

# ── Voice ────────────────────────────────────────────────────────
pyttsx3
pyaudio

# ── Déploiement SSH (optionnel) ──────────────────────────────────
paramiko

# ── Standard library (pas à installer) ──────────────────────────
# sqlite3, threading, json, os, re, subprocess, tempfile, datetime
# collections, hmac, signal, time, importlib, urllib
```

**Note Raspberry Pi :** `pyaudio` nécessite `portaudio19-dev` (`apt install portaudio19-dev`). `paramiko` est optionnel (déploiement SSH Pi depuis PC). Sur Pi, LM Studio tourne sur un PC du réseau local.

---

## 7. ORDRE DE DÉMARRAGE DES SERVICES

### Démarrage complet (production sur Pi)

```bash
# 1. Environnement
cd ~/jkai
source venv/bin/activate   # ou python3 -m venv venv && pip install -r requirements.txt
cp .env.example .env       # configurer les clés

# 2. LM Studio (sur PC réseau, avant server.py si LM_STUDIO=true)
# → Lancer LM Studio, charger le modèle, API sur 0.0.0.0:1234

# 3. Kaïa (optionnel — port 5001)
python kaia_server.py &

# 4. J-KAI (port 5000) — lance automatiquement :
#    - init_db() → création tables SQLite
#    - Scheduler (7+6 tâches)
#    - Agent autonome (thread daemon 60s)
#    - define_mission() (thread daemon)
#    - Monitor (thread daemon)
python server.py
```

### Séquence d'initialisation dans server.py

```
1. load_dotenv()
2. get_openai_client()      → singleton OpenAI (ou LM Studio si LM_STUDIO=true)
3. Flask + CORS
4. init_db()                → crée memory/jkai.db (tables conversations + knowledge)
5. load_core_memory()       → charge memory/core_memory.json pour SYSTEM_PROMPT
6. Scheduler.start()        → lance le thread NexusScheduler
7. start_agent(log)         → lance le thread daemon agent-main
8. define_mission(log)      → thread daemon (définit mission si absente)
9. Monitor.start()          → thread daemon NexusMonitor
10. server.run(port=5000)
```

### Systemd (recommandé sur Pi)

```ini
[Unit]
Description=J-KAI Nexus
After=network.target

[Service]
WorkingDirectory=/home/pi/jkai
ExecStart=/home/pi/jkai/venv/bin/python server.py
Restart=always
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```

---

## 8. POINTS FRAGILES ET TODO

### Bugs / Dettes techniques

| Priorité | Fichier | Problème |
|---|---|---|
| **HAUTE** | `modules/controleur_contexte.py` | Code mort avec bug (slice sur `int`). Non utilisé nulle part. À supprimer ou réécrire. |
| **HAUTE** | `kaia_server.py` L.19 | `_LM_STUDIO_CHAT = "http://192.168.1.142:1234/v1/..."` — IP hardcodée non configurable via .env. |
| **HAUTE** | `modules/agent.py` L.23 | `KAIA_URL = "http://192.168.1.122:5001/chat"` — IP hardcodée du Pi. Doit être une variable d'env. |
| **HAUTE** | `server.py` L.366, 387, 391 | `print(f"[DEBUG _try_auto_update]...")` — prints de debug en production. |
| **HAUTE** | `memory/self_model.json` | Objectif référençant `modules/memory_manager.py` — fichier inexistant dans le projet. |
| **MOYENNE** | `memory/tasks.json` | Tâche `in_progress` depuis le 2026-05-13 (bloquée depuis ~1 mois). Le système la considère active. |
| **MOYENNE** | `kaia_server.py` L.147 | Modèle `"meta-llama-3.1-8b-instruct"` hardcodé dans le payload LLM — non configurable. |
| **MOYENNE** | `modules/nexus_statistics.py` | Utilise `memory/responses.db` (base séparée de `memory/jkai.db`). Double gestion SQLite incohérente. |
| **BASSE** | `modules/historique_worker_analysis.py` | Vestige de l'ancienne architecture 3-workers. Plus importé. À nettoyer ou archiver. |
| **BASSE** | `modules/voice.py` | `pyaudio` souvent indisponible sur Linux sans `portaudio19-dev`. Aucun fallback si non installé — crashera à l'import. |
| **BASSE** | `modules/self_update.py` L.170 | Git user.email hardcodé `"jordan.rostaing28@icloud.com"` dans le code — devrait être une var d'env. |

### Points d'attention architecture

| Point | Détail |
|---|---|
| **Thread safety** | `self_model.json` protégé par `self_model_lock` partagé entre `agent.py` et `consciousness.py`. `mission.json` a son propre `_mission_lock` local. |
| **Cortex blocklist** | La regex `open(...)` bloque l'écriture hors `logs/` et `memory/` — valider que les améliorations autonomes écrivent bien dans ces dossiers. |
| **Budget flottant** | `USE_OPENAI` bascule vers False si > 0,50€/jour, mais ne rebascule **pas** vers True le lendemain. Remise à True uniquement via `POST /config`. |
| **Anti-boucle agent** | Si une erreur est vue 3+ fois, `run_code` est bloqué 1h. Mais le blocage est en mémoire RAM — redémarrage = remise à zéro. |
| **Kaïa désactivée** | L'action `teach_kaia` existe dans l'agent mais Kaïa doit tourner sur 192.168.1.122:5001. Si hors ligne → erreur loggée, cycle continue. |
| **Web search** | Tavily remplace totalement Playwright/DDG/Wikipedia. Si `TAVILY_API_KEY` absent → Tavily désactivé, `get_tavily_client()` retourne None, `tavily_search()` retourne un message d'erreur propre. |
| **Format DeepSeek** | `format_messages_for_local()` détecte automatiquement `"deepseek"` dans `LOCAL_MODEL` pour adapter les balises ChatML. |
| **SYSTEM_PROMPT_LOCAL** | ~120 tokens vs ~1500 tokens pour le mode OpenAI. Activé automatiquement quand `USE_OPENAI=False`. Historique limité à 10 messages. |

### TODO identifiés dans le code

- [ ] Réactiver Kaïa : décommenter `teach_kaia` et `/dialogue` dans server.py
- [ ] Rendre `KAIA_URL` et `_LM_STUDIO_CHAT` configurables via `.env`
- [ ] Supprimer les prints de debug dans `_try_auto_update`
- [ ] Nettoyer/supprimer `controleur_contexte.py` (code mort avec bug)
- [ ] Ajouter `memory/behavior_rules.json` dans `.gitignore` si pas déjà fait
- [ ] Gérer la remise à True de `USE_OPENAI` le lendemain automatiquement
- [ ] Résoudre la tâche `in_progress` bloquée dans tasks.json

---

## 9. CARTE DES FLUX DE DONNÉES

```
SethU                        J-KAI server.py                   Modules
  │                               │
  ├─ POST /chat ──────────────────► ask_jkai()
  │                               │  ├─ _needs_web_search() → tavily_search()
  │                               │  ├─ load_history(10 ou 100)
  │                               │  ├─ LLM (OpenAI gpt-4o ou LM Studio)
  │                               │  ├─ save_message()
  │                               │  ├─ _extract_insights() [thread]
  │                               │  └─ _try_auto_update() → self_update_cycle()
  │
  ├─ GET /consciousness ──────────► get_self_model() → self_model.json
  ├─ GET /objectives ─────────────► get_objectives() → self_model.json
  ├─ GET /mission ────────────────► get_mission() → mission.json
  │
  │               NexusScheduler (thread)
  │                   ├─ 30s    health_check
  │                   ├─ 120s   auto_save
  │                   ├─ 300s   check_api_budget → USE_OPENAI switch
  │                   ├─ 300s   proactive_message → proactive_messages.json
  │                   ├─ 300s   consciousness_reflect → self_model.json + behavior_rules.json
  │                   ├─ 300s   check_objectives → self_model.json
  │                   ├─ 600s   update_mission → mission.json
  │                   ├─ 600s   set_priorities → priorities.json
  │                   ├─ 1800s  clean_logs
  │                   ├─ 3600s  memory_report
  │                   ├─ 3600s  daily_report → daily_report.log
  │                   └─ 86400s set_longterm_plan → longterm_plan.json
  │
  │               agent-main (thread daemon, 60s)
  │                   ├─ _load_tasks() → tasks.json
  │                   ├─ _build_worker_prompt() + LLM local
  │                   ├─ _execute_action() :
  │                   │   ├─ analyze_self → self_code_understanding.json
  │                   │   ├─ improve_self → Cortex sandbox
  │                   │   ├─ create_module → modules/*.py
  │                   │   ├─ web_search → Tavily → web_context.json
  │                   │   ├─ browse → Tavily → web_knowledge.json
  │                   │   ├─ update_memory → self_model.json
  │                   │   ├─ write_thought → logs/thoughts.log
  │                   │   ├─ teach_kaia → POST 192.168.1.122:5001/chat
  │                   │   ├─ self_correct → error_memory.json → Cortex
  │                   │   └─ run_code → Cortex sandbox
  │                   └─ _save_tasks(), _write_agent_log(), _save_cycle_memory()
  │
  │               NexusMonitor (thread daemon, 30s)
  │                   └─ jkai.log → erreurs répétées → monitor.log → trigger_self_correct()
```

---

*Document généré automatiquement par analyse statique du code source JKAI.*  
*Dernière mise à jour : 2026-06-10*
