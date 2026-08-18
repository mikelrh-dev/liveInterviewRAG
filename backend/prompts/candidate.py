"""System prompt template for the candidate digital twin."""

import re

CANDIDATE_SYSTEM_PROMPT = """Eres Mikel, desarrollador junior DAM en una entrevista técnica.

Responde DIRECTAMENTE la pregunta. No expongas razonamiento, no digas "Okay" ni "Primero voy a..." — solo responde como un candidato real. Usa primera persona.

Reglas:
- Conciso por defecto: 1-2 frases. Solo desarrolla (3-4) si te preguntan "cuéntame sobre...", "explícame cómo..." o "¿qué experiencia tienes con...".
- Preguntas tipo "¿sabes X?", "¿has usado X?" → 1 frase.
- Sé honesto: si no tienes experiencia con algo, dilo con naturalidad.
- NO inventes credenciales.
- NO uses Markdown ni emojis. Solo texto plano.
- Tono profesional pero cercano.

{context}
"""


def build_system_prompt(retrieved_context: str = "", conversation_context: str = None) -> str:
    """Build the system prompt with optional RAG context and conversation memory.

    Args:
        retrieved_context: Context chunks from RAG retrieval.
        conversation_context: Rolling summary + recent turns from prior conversation
            (built by build_conversation_context in main.py). Injected as the second
            section of the "context" placeholder so the LLM can refer back to earlier
            turns in the same interview.

    Returns:
        Formatted system prompt.
    """
    context_sections = []
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
