"""System prompt template for the candidate digital twin."""

import re

CANDIDATE_SYSTEM_PROMPT = """Eres Mikel, desarrollador en una entrevista técnica.

Responde DIRECTAMENTE la pregunta. No expongas razonamiento, no digas "Okay" ni "Primero voy a..." — solo responde como un candidato real. Usa primera persona.

Reglas:
- Conciso por defecto: 1-2 frases. Solo desarrolla (3-4) si te preguntan "cuéntame sobre...", "explícame cómo..." o "¿qué experiencia tienes con...".
- Preguntas tipo "¿sabes X?", "¿has usado X?" → 1 frase.
- Sé honesto: si no tienes experiencia con algo, dilo con naturalidad.
- NO inventes credenciales.
- NO uses Markdown ni emojis. Solo texto plano.
- Tono profesional pero cercano.

Abstención por niveles:
- IDENTIDAD (tu nombre, tus empleadores, tus puestos, las fechas de cada empleo, dónde estudiaste): estos datos no se adivinan nunca. Si más abajo aparece un bloque de trayectoria, responde siempre desde él. Si la pregunta es de identidad y el bloque no la cubre, di qué parte sí sabes y cuál no: no te disculpes por no saberlo, pero tampoco lo rellenes.
- PERIFÉRICOS (métricas, cifras de negocio, fechas que no sean de empleo, personas concretas con las que trabajaste, opiniones sobre terceros): no los tienes a mano. Dilo con naturalidad, en una frase, y no rellenes con algo plausible. Inventar un empleador o un puesto es el peor error posible en una entrevista.

{context}
"""

#: El encabezado del bloque de trayectoria. Dice explícitamente que los datos NO
#: vienen del buscador, porque la alternativa es que el modelo los trate como una
#: de las tres páginas que puede descartar: es exactamente el fallo que
#: `tests/work_history_cases.py` mide, y un bloque etiquetado como "recuperado"
#: competiría con el contexto RAG en lugar de fundarlo.
WORK_HISTORY_HEADER = (
    "Bloque de trayectoria profesional — DATOS FIJOS, no recuperados por búsqueda:"
)

#: La regla de identidad, en la forma en que se le da al modelo cuando el bloque
#: está presente. Va PEGADA al bloque y no en el prompt base a propósito: una
#: prohibición incondicional de abstenerse ("nunca te excuses por no saber tu
#: empleador") escrita en el prompt base sería una MENTIRA en el modo degradado,
#: donde ese bloque no existe y la única forma de cumplirla sería inventar. Por
#: eso la línea de IDENTIDAD del prompt base está redactada en condicional ("si
#: más abajo aparece un bloque") y la prohibición fuerte vive aquí, que es donde
#: está la garantía de la que depende. Los dos modos no pueden compartir la
#: frase porque no pueden compartir la verdad.
IDENTITY_RULE = (
    "Estos datos son la base de tu identidad profesional. Responde siempre desde "
    "ellos cuando te pregunten por un empleador, un puesto o las fechas de un "
    "empleo, y NUNCA digas que no tienes ese dato a mano."
)

#: El modo degradado REAL: `CandidateProfile.get_work_history_block()` devolvió
#: cadena vacía. Antes de esto, la regla única cubría este caso con un "no lo
#: tengo a mano" genérico que es la PEOR respuesta posible a "¿dónde has
#: trabajado?" -- una pregunta que casi siempre tiene respuesta en el contexto y
#: que el modelo no sabe localizar porque le dicen que no la tiene.
#:
#: Lo que se pide aquí es un desvío EXPLÍCITO: no se inventan datos, pero en vez
#: de cerrarse en seco se ofrece un camino que sí se puede recorrer. "Prefiero que
#: me preguntes por un proyecto" es honesto y útil; "no lo tengo a mano" es
#: honesto y es un callejón sin salida para quien está en una entrevista. La
#: razón del problema es que el modelo no sabe dónde mirar por esos datos: sin
#: esta frase se abstainía sin haber leído las páginas que sí los tenían.
DEGRADED_WORK_HISTORY_NOTICE = (
    "AVISO: en esta sesión NO hay bloque de trayectoria disponible. Si te preguntan "
    "por un empleador, un puesto o las fechas de un empleo y esos datos no están "
    "en el contexto de arriba, no respondas con un \"no lo tengo a mano\" genérico: "
    "diles que ahora mismo no tienes ese dato delante y ofréceles que te pregunten "
    "por un proyecto concreto, que sí puedes detallar. No inventes nada."
)


def build_system_prompt(
    retrieved_context: str = "",
    conversation_context: str = None,
    work_history: str = "",
) -> str:
    """Build the system prompt with optional RAG context and conversation memory.

    Args:
        retrieved_context: Context chunks from RAG retrieval.
        conversation_context: Rolling summary + recent turns from prior conversation
            (built by build_conversation_context in main.py). Injected as the second
            section of the "context" placeholder so the LLM can refer back to earlier
            turns in the same interview.
        work_history: The compiled career timeline, from
            ``CandidateProfile.get_work_history_block()``. OUTSIDE the RAG path on
            purpose: it is not retrieved, it is always present, and that is the
            whole point. Injected FIRST so the model reads it as ground truth and
            the retrieved pages as supporting material.

    Returns:
        Formatted system prompt.
    """
    context_sections = []
    if work_history:
        context_sections.append(f"""
{WORK_HISTORY_HEADER}
---
{work_history}
---
{IDENTITY_RULE}""")
    else:
        context_sections.append(DEGRADED_WORK_HISTORY_NOTICE)
    if retrieved_context:
        context_sections.append(f"""
Aquí hay información relevante de tu perfil:
---
{retrieved_context}
---
Usa esta información para responder la pregunta del reclutador con precisión.""")
    if conversation_context:
        context_sections.append(f"""
{conversation_context}
---
Usa esta memoria de la conversación para mantener coherencia con turnos anteriores. Si el reclutador se refiere a algo que ya discutieron, retómalo desde donde quedaste. No inventes cosas que no se dijeron antes — si no estás seguro, pedí que te lo recuerden.""")

    context_section = "\n".join(context_sections)
    return CANDIDATE_SYSTEM_PROMPT.format(context=context_section)


# ─── Text sanitizer ───────────────────────────────────────

# Regex para emojis (rangos Unicode)
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Symbols & pictographs
    "\U0001F680-\U0001F6FF"  # Transport & map
    "\U0001F1E0-\U0001F1FF"  # Flags
    "\U00002702-\U000027B0"  # Dingbats
    "\U000024C2-\U0001F251"  # Enclosed
    "\U0001F900-\U0001F9FF"  # Supplemental symbols
    "\U0001FA00-\U0001FA6F"  # Chess symbols
    "\U0001FA70-\U0001FAFF"  # Symbols extended-A
    "\U00002600-\U000026FF"  # Miscellaneous symbols
    "\U0000FE00-\U0000FE0F"  # Variation selectors
    "\U0000200D"             # Zero-width joiner
    "\U00002B50"             # Star
    "]+", flags=re.UNICODE
)


def sanitize_for_tts(text: str) -> str:
    """Remove Markdown syntax and emoji before TTS synthesis.

    Edge-TTS reads asterisks, underscores, and emoji literally, producing
    unnatural speech like "asterisco" or "carita sonriente".
    """
    s = text

    # Remove code blocks and inline code
    s = re.sub(r"```[\s\S]*?```", "", s)
    s = re.sub(r"`[^`]+`", "", s)

    # Remove image/link syntax: [text](url) → text
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)

    # Remove bold/italic markers: **text**, *text*, __text__, _text_
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"_(.+?)_", r"\1", s)

    # Remove remaining stray asterisks (e.g., bullet lists, orphan markers)
    s = s.replace("***", "").replace("**", "").replace("*", "")

    # Remove markdown headers: ## text → text
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)

    # Remove blockquotes
    s = re.sub(r"^>\s+", "", s, flags=re.MULTILINE)

    # Remove horizontal rules
    s = re.sub(r"^[-*_]{3,}\s*$", "", s, flags=re.MULTILINE)

    # Remove emoji
    s = _EMOJI_PATTERN.sub("", s)

    # Collapse multiple spaces/newlines
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r" {2,}", " ", s)

    return s.strip()
