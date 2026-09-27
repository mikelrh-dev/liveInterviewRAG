"""Farewell detection and the closing line.

Both turn pipelines can end an interview, but only the streaming one detects
the farewell and speaks it; the two endpoints still diverge here, which is
deliberately left as-is until they are unified. See the module docstring of
``backend.turns.streaming`` for what each path does.
"""

import re

_FAREWELL_PATTERNS = [
    r"\bgracias\b.*\b(eso es todo|terminamos|finalizamos|nos vemos|adiós|chao)\b",
    r"\b(eso es todo|nada más|no tengo más preguntas)\b",
    r"\bno (tengo|hay) (más |ninguna )?(preguntas|dudas|cosas)\b",
    r"\bya (está|terminé|acabé|estamos)\b",
    r"\b(terminamos|finalizamos|cerramos) (la entrevista|por hoy|aquí|acá)\b",
    r"\b(gracias|muchas gracias).*(por tu tiempo|por la entrevista|ha sido un placer)\b",
    r"\bfue un placer\b",
    r"\b(adiós|chao|nos vemos|hasta luego)\b",
]

#: Spoken when the recruiter signs off. Kept here so the copy and the detection
#: that triggers it sit together.
FAREWELL_TEXT = (
    "¡Gracias a ti! Ha sido un placer. Si tenés más preguntas en el futuro, "
    "acá estoy. ¡Éxito en tu búsqueda!"
)


def detect_farewell(text: str) -> bool:
    """Check if the user is indicating the interview should end."""
    lower = text.lower().strip()
    for pattern in _FAREWELL_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False
