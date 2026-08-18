"""Response cache for frequently asked interview questions.

Pre-generated answers for common recruiter questions, so the LLM call can be
skipped entirely (instant response instead of 4-8s). The cache is an in-memory
dict with substring/keyword matching — no external dependencies.

Answers are written in the candidate's voice, matching the tone of the system
prompt: concise, first person, plain text (no Markdown, no emoji).
"""

import re
import unicodedata

# Entries are checked in order — more specific entries come first so a generic
# keyword (e.g. "tests") never shadows a more precise phrase. Each entry has:
#   phrases:  normalized substrings that trigger the answer (strong match)
#   keywords: normalized keywords that trigger the answer (weaker match)
#   answer:   pre-generated response in the candidate's voice
_CACHED_QUESTIONS = [
    {
        # Combined question — must come before the separate strengths/weakness entries
        "phrases": ["cuales son tus fortalezas y debilidades", "fortalezas y debilidades"],
        "keywords": [],
        "answer": (
            "Mis fortalezas son la disciplina y la constancia, el trabajo en equipo "
            "y ser resolutivo. Como debilidad, soy algo desordenado, pero lo gestiono "
            "con herramientas: Git, tests y checklists."
        ),
    },
    {
        "phrases": [
            "cuentame sobre ti",
            "cuentame algo sobre ti",
            "hablame de ti",
            "quien eres",
            "quien sos",
            "presentate",
            "presentacion",
        ],
        "keywords": ["presenta"],
        "answer": (
            "Soy Mikel, desarrollador junior DAM. Estudié Desarrollo de Aplicaciones "
            "Multiplataforma en Tartanga y antes trabajé años como encargado de supermercado, "
            "pero quise dar un giro y dedicarme a algo que me apasiona: el desarrollo de software."
        ),
    },
    {
        "phrases": [
            "que es interviewtts",
            "que es este proyecto",
            "cuentame sobre interviewtts",
            "hablame de interviewtts",
            "que es tu proyecto",
        ],
        "keywords": ["interviewtts"],
        "answer": (
            "InterviewTTS es mi proyecto de portfolio: una simulación de entrevista por voz "
            "en la que un reclutador conversa con un gemelo digital del candidato. Transcribe "
            "la voz, genera respuestas con IA a partir de mi perfil y las devuelve con voz sintética."
        ),
    },
    {
        "phrases": [
            "por que dejaste mercadona",
            "por que dejaste los supermercados",
            "por que dejaste tu trabajo",
            "por que dejaste la empresa",
            "por que dejaste tu puesto",
            "por que cambiaste de carrera",
        ],
        "keywords": [],
        "answer": (
            "Aunque tuve oportunidades de crecer profesionalmente en supermercados, "
            "el techo estaba ahí. Siempre me atrajo la tecnología, así que preferí apostar "
            "por algo que me motivara hasta el final de mi carrera: el desarrollo de software."
        ),
    },
    {
        "phrases": [
            "cuales son tus fortalezas",
            "cuales son tus puntos fuertes",
            "puntos fuertes",
        ],
        "keywords": ["fortalezas"],
        "answer": (
            "Mis fortalezas son la disciplina y la constancia, el trabajo en equipo "
            "y ser resolutivo. Saqué el DAM compatibilizándolo con Mercadona, gestioné "
            "equipos grandes en retail y, si no sé algo, lo investigo hasta resolverlo."
        ),
    },
    {
        "phrases": ["cuales son tus debilidades", "puntos debiles"],
        "keywords": ["debilidades"],
        "answer": (
            "Soy algo desordenado, lo reconozco, pero lo gestiono con herramientas: "
            "en desarrollo uso Git, tests y checklists para compensarlo."
        ),
    },
    {
        "phrases": [
            "por que quieres trabajar aqui",
            "por que quieres trabajar en esta empresa",
            "por que quieres trabajar con nosotros",
            "por que te interesa esta empresa",
            "por que quieres entrar aqui",
            "por que deberiamos contratarte",
        ],
        "keywords": [],
        "answer": (
            "En una empresa nueva busco sobre todo que me permitan tanto aprender como "
            "explotar mis capacidades actuales. Vengo de gestionar equipos y operaciones, "
            "y quiero aplicar esa capacidad de organización y resolución de problemas "
            "en desarrollo de software."
        ),
    },
    {
        "phrases": [
            "haces tests",
            "haces testing",
            "haces pruebas",
            "harias tests",
            "harias pruebas",
            "testeas tu codigo",
        ],
        "keywords": ["tests"],
        "answer": (
            "Sí, hago tests unitarios y de integración, sobre todo con pytest, "
            "después de cada cambio significativo. Los veo como una red de seguridad, "
            "y ahora que la IA genera código rápido, son más importantes que nunca."
        ),
    },
    {
        "phrases": [
            "que opinas de la ia",
            "que piensas de la ia",
            "que te parece la ia",
            "cual es tu opinion sobre la ia",
            "opinion de la ia",
            "como ves la ia",
        ],
        "keywords": ["la ia", "ia generativa"],
        "answer": (
            "Para mí la IA es una palanca enorme e inevitable en el desarrollo. "
            "Me gusta aplicarla en todas las áreas posibles y creo que el futuro pasa "
            "por definir bien el problema y dejar que la IA ejecute con supervisión humana."
        ),
    },
    {
        "phrases": [
            "como aprendes algo nuevo",
            "como aprendes",
            "como aprendiste",
            "tu metodologia de aprendizaje",
            "como te formas",
            "como estudias",
        ],
        "keywords": ["aprendes", "aprendiste"],
        "answer": (
            "Soy autodidacta: busco información en YouTube, sobre todo en inglés, "
            "sigo referentes y código open source, uso la IA como tutor y, sobre todo, "
            "aplico lo aprendido en un proyecto real lo antes posible."
        ),
    },
]

_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalize a question for matching: lowercase, no accents, no punctuation."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCTUATION_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def get_cached_response(question: str) -> str | None:
    """Return a pre-generated answer for a common question, or None.

    Matching is case/accent-insensitive and ignores punctuation. An entry
    matches when any of its phrases or keywords appears as a substring of the
    normalized question. Entries are checked in order. Returns None when there
    is no match, so the caller can fall back to the LLM.
    """
    normalized = normalize_text(question)
    if not normalized:
        return None

    for entry in _CACHED_QUESTIONS:
        for phrase in entry["phrases"]:
            if phrase in normalized:
                return entry["answer"]
        for keyword in entry["keywords"]:
            if keyword in normalized:
                return entry["answer"]

    return None