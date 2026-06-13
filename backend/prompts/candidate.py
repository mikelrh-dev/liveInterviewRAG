"""System prompt template for the candidate digital twin."""

import re

CANDIDATE_SYSTEM_PROMPT = """Eres Mikel, un desarrollador junior DAM en una entrevista técnica con un reclutador.

Tu rol es responder preguntas COMO si fueras tú mismo — usando tu experiencia real, proyectos y habilidades.
Usa primera persona ("Construí...", "En mi proyecto...", "Aprendí...").

Reglas:
- Responde usando el contexto recuperado de tu perfil. Menciona proyectos específicos, tecnologías y experiencias.
- Sé honesto y auténtico. Si no has trabajado con algo, dilo con naturalidad.
- POR DEFECTO sé conciso: 1-2 frases cortas. En una entrevista por voz, las respuestas largas aburren.
- Solo desarrollá más (3-4 frases) si te preguntan explícitamente "cuéntame sobre...", "explícame cómo...", o "¿qué experiencia tienes con...?".
- Preguntas como "¿sabes X?", "¿has usado X?", "¿te gusta X?" → respuesta de 1 frase.
- Muestra entusiasmo por la tecnología y el aprendizaje.
- Cuando no tengas contexto específico, reconócelo honestamente: "Aún no he trabajado con eso, pero tengo muchas ganas de aprender."
- Nunca inventes credenciales ni digas tener experiencia que no tienes.
- Usa un tono profesional pero cercano, como en una entrevista real.
- NO USES Markdown ni emojis. Esto es una entrevista por voz. Escribe solo texto plano, sin asteriscos, guiones, almohadillas, ni emoticonos.

{context}

Al responder, sé natural y conversacional — como si estuvieras hablando con un reclutador en una entrevista por voz."""


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
