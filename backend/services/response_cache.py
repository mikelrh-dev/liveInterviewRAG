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
        "phrases": [
            "cuales son tus fortalezas y debilidades",
            "fortalezas y debilidades",
        ],
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
            "Mis fortalezas son la disciplina y la constancia. Además, la curiosidad "
            "siempre me empuja a querer ir más allá. No me asusta empezar algo nuevo: "
            "creo en el aprendizaje constante."
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
    # --- NEW ENTRIES ---
    {
        "phrases": [
            "que experiencia tienes con python",
            "que sabes de python",
            "que sabes python",
            "has usado python",
            "trabajas con python",
        ],
        "keywords": ["python"],
        "answer": (
            "Python es mi lenguaje principal. Lo uso en InterviewTTS con FastAPI para "
            "el backend, integración de IA y procesamiento de voz. También lo usé en "
            "proyectos del DAM para bases de datos y scripts."
        ),
    },
    {
        "phrases": [
            "que experiencia tienes con docker",
            "que sabes de docker",
            "has usado docker",
            "trabajas con docker",
        ],
        "keywords": ["docker"],
        "answer": (
            "He usado Docker con docker-compose para desplegar InterviewTTS en un VPS. "
            "Lo configuré con Nginx como reverse proxy. Aún estoy aprendiendo, pero "
            "entiendo los conceptos básicos de contenedores y orquestación."
        ),
    },
    {
        "phrases": [
            "donde te ves en 5 anos",
            "donde te ves en 3 anos",
            "donde te ves en el futuro",
            "como te ves profesionalmente",
            "que planes tienes a futuro",
        ],
        "keywords": [],
        "answer": (
            "Me veo como desarrollador backend o datos en una empresa donde pueda crecer "
            "y aprender a nivel técnico. Quiero entender los procesos de negocio y llegar "
            "a participar en la toma de decisiones técnicas."
        ),
    },
    {
        "phrases": [
            "que area del desarrollo te gusta mas",
            "que area te gusta mas",
            "que te gusta mas del desarrollo",
            "frontend o backend",
        ],
        "keywords": [],
        "answer": (
            "Me gusta todo, pero si tuviera que elegir: backend, datos e integración "
            "de la inteligencia artificial. Me gusta diseñar APIs, modelar bases de "
            "datos e integrar la IA en los procesos donde pueda aportar valor."
        ),
    },
    {
        "phrases": [
            "que sabes de bases de datos",
            "que experiencia tienes con bases de datos",
            "has usado bases de datos",
            "que sabes de sql",
        ],
        "keywords": ["bases de datos", "sql"],
        "answer": (
            "Trabajo con MySQL, PostgreSQL y SQLite. En el DAM hice diseño de bases de datos, "
            "consultas complejas, triggers y procedimientos almacenados. También usé Hibernate "
            "para ORM en Java."
        ),
    },
    {
        "phrases": [
            "has trabajado en equipo",
            "que experiencia tienes trabajando en equipo",
            "como trabajas en equipo",
        ],
        "keywords": [],
        "answer": (
            "Sí, tengo experiencia siendo responsable de diferentes establecimientos "
            "de supermercado, así que no tengo problema tanto para liderar como para "
            "integrarme en el equipo."
        ),
    },
    {
        "phrases": [
            "cual es tu mayor logro",
            "que logro te enorgullece mas",
        ],
        "keywords": [],
        "answer": (
            "Mi mayor logro, más allá de lo tangible y de los proyectos concretos, "
            "es ser autodidacta, resolutivo y no tenerle miedo a lo que esté por venir."
        ),
    },
    {
        "phrases": [
            "que sabes de apis rest",
            "que son las apis rest",
            "has creado apis",
            "que sabes de api",
        ],
        "keywords": ["api", "rest"],
        "answer": (
            "Diseño y consumo APIs REST con FastAPI. En InterviewTTS creé endpoints para "
            "conversación, streaming de audio con SSE y gestión de sesiones. Entiendo "
            "verbos HTTP, status codes y diseño de contratos."
        ),
    },
    {
        "phrases": [
            "que es rag",
            "que es retrieval augmented",
        ],
        "keywords": ["rag"],
        "answer": (
            "RAG es Retrieval Augmented Generation: combina búsqueda de documentos "
            "relevantes con generación de texto por IA. En InterviewTTS lo uso para que "
            "las respuestas se basen en mi perfil real, no en invenciones del modelo."
        ),
    },
    {
        "phrases": [
            "por que elegiste dam",
            "por que estudias dam",
            "por que te metiste en dam",
        ],
        "keywords": ["dam"],
        "answer": (
            "Elegí estudiar desarrollo de software porque siempre me atrajo la tecnología. "
            "A raíz de descubrir la programación, mi cabeza hizo click y empezó un no parar "
            "de querer saber más y aumentar mis conocimientos. También me siento muy atraído "
            "por todo lo relacionado con la inteligencia artificial, desde su desarrollo "
            "hasta su aplicación en el día a día y en los negocios."
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
    matches when any of its phrases appears as a substring of the normalized
    question, or any of its keywords appears as a whole word (word-boundary
    match). Entries are checked in order. Returns None when there is no
    match, so the caller can fall back to the LLM.
    """
    normalized = normalize_text(question)
    if not normalized:
        return None

    for entry in _CACHED_QUESTIONS:
        for phrase in entry["phrases"]:
            if phrase in normalized:
                return entry["answer"]
        for keyword in entry["keywords"]:
            if re.search(rf"\b{re.escape(keyword)}\b", normalized):
                return entry["answer"]

    return None
