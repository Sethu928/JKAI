import re

_THINK_CLOSED = re.compile(
    r'<(think|thinking)>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN = re.compile(r'<think(?:ing)?\s*>', re.IGNORECASE)


def strip_think(text: str) -> str:
    """
    Supprime les blocs de raisonnement émis par les modèles 'reasoning'
    (ex : DeepSeek R1) qui préfixent leur réponse avec <think>…</think>
    ou <thinking>…</thinking> avant le contenu utile.

    Cas gérés :
    - <think>...</think> et <thinking>...</thinking> fermés
      (multiligne, insensible à la casse)
    - <think> ou <thinking> ouvert sans balise fermante :
      tout ce qui suit le tag est tronqué
    """
    text = _THINK_CLOSED.sub('', text)
    m = _THINK_OPEN.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()
