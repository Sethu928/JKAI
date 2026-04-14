import os
import json
import re
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from modules.cortex import execute_code

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AUTONOMY_LOG = "logs/autonomy.log"

AUTONOMY_SYSTEM_PROMPT = (
    "Tu es J-KAI en mode autonome. Tu analyses une situation et décides d'une action. "
    "Tu réponds UNIQUEMENT en JSON avec ce format : "
    '{"decision": string, "action": string, "execute": bool, "code": string ou null}. '
    "Les actions possibles sont : log, alert, run_code, do_nothing. "
    "Tu ne franchis jamais les limites de sécurité : tu ne modifies pas tes propres règles, "
    "tu n'accèdes pas à des données sensibles, tu ne fais rien d'irréversible sans confirmation."
)

VALID_ACTIONS = {"log", "alert", "run_code", "do_nothing"}

# Retire les blocs ```json ... ``` si GPT en ajoute malgré la consigne
_MD_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _write_autonomy_log(ts: str, context: str, decision: dict, exec_result: dict | None) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        f = open(AUTONOMY_LOG, "a", encoding="utf-8")
    except OSError:
        return
    with f:
        f.write(f"[{ts}] DÉCISION AUTONOME\n")
        f.write(f"  CONTEXTE  : {context[:200]}\n")
        f.write(f"  DÉCISION  : {decision.get('decision', '—')}\n")
        f.write(f"  ACTION    : {decision.get('action', '—')}\n")
        f.write(f"  EXÉCUTION : {'oui' if decision.get('execute') else 'non'}\n")
        if exec_result:
            f.write(f"  SORTIE    : {exec_result.get('output', '')[:300]}\n")
            if exec_result.get("error"):
                f.write(f"  ERREUR    : {exec_result['error'][:200]}\n")
        f.write(f"{'─' * 72}\n")


def _parse_decision(raw: str) -> dict:
    """Parse la réponse JSON de GPT-4o de façon robuste."""
    raw = raw.strip()
    m = _MD_FENCE.match(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Dernier recours : extraction du premier bloc JSON dans le texte
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"Réponse GPT non parseable : {raw[:200]}")

    # Valeurs par défaut / normalisation
    data.setdefault("decision", "Aucune décision")
    data.setdefault("action",   "do_nothing")
    data.setdefault("execute",  False)
    data.setdefault("code",     None)

    if data["action"] not in VALID_ACTIONS:
        data["action"] = "do_nothing"
        data["execute"] = False

    return data


def analyze_and_act(context: str, log_fn) -> dict:
    """
    Envoie le contexte à GPT-4o en mode autonome, interprète sa décision
    et l'exécute si nécessaire via le sandbox Cortex.

    Retourne un dict :
        {
            decision : str,
            action   : str,
            execute  : bool,
            code     : str | None,
            output   : str,          # résultat d'exécution (si run_code)
            error    : str,          # erreur éventuelle
            blocked  : bool          # True si le sandbox a refusé le code
        }
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1. Appel GPT-4o ────────────────────────────────────────────────
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": AUTONOMY_SYSTEM_PROMPT},
                {"role": "user",   "content": context},
            ],
            temperature=0.1,        # réponses déterministes en mode autonome
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
    except Exception as e:
        log_fn(f"[AUTONOMY] Erreur GPT-4o : {e}")
        return {"decision": "Erreur GPT", "action": "do_nothing",
                "execute": False, "code": None, "output": "", "error": str(e), "blocked": False}

    # ── 2. Parsing ────────────────────────────────────────────────────
    try:
        decision = _parse_decision(raw)
    except (ValueError, json.JSONDecodeError) as e:
        log_fn(f"[AUTONOMY] Parsing échoué : {e}")
        return {"decision": "Parsing échoué", "action": "do_nothing",
                "execute": False, "code": None, "output": "", "error": str(e), "blocked": False}

    # ── 3. Exécution conditionnelle ───────────────────────────────────
    exec_result: dict | None = None

    if decision["execute"] and decision["action"] == "run_code" and decision["code"]:
        exec_result = execute_code(decision["code"])
        log_fn(
            f"[AUTONOMY] run_code exécuté — "
            f"sortie : {exec_result.get('output','')[:100]} "
            f"erreur : {exec_result.get('error','')[:100]}"
        )
    elif decision["action"] == "alert":
        log_fn(f"[AUTONOMY] ALERTE : {decision['decision']}")
    elif decision["action"] == "log":
        log_fn(f"[AUTONOMY] {decision['decision']}")

    # ── 4. Journal autonomy.log ───────────────────────────────────────
    _write_autonomy_log(ts, context, decision, exec_result)

    # ── 5. Réponse consolidée ─────────────────────────────────────────
    result = {
        "decision": decision["decision"],
        "action":   decision["action"],
        "execute":  decision["execute"],
        "code":     decision.get("code"),
        "output":   exec_result.get("output", "")  if exec_result else "",
        "error":    exec_result.get("error",  "")  if exec_result else "",
        "blocked":  exec_result.get("blocked", False) if exec_result else False,
    }
    return result
