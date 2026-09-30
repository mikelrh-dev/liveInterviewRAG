"""Response cache for frequently asked interview questions.

Pre-generated answers for common recruiter questions, so the LLM call can be
skipped entirely (instant response instead of 4-8s). The cache is an in-memory
dict with word-anchored phrase matching and whole-word keyword matching — no
external dependencies.

Answers are written in the candidate's voice, matching the tone of the system
prompt: concise, first person, plain text (no Markdown, no emoji).

A HIT SKIPS RETRIEVAL, WHICH IS WHY PRECISION IS THE WHOLE COST HERE
-------------------------------------------------------------------
``backend/turns/streaming.py`` consults this cache at Step 2 and RETURNS
before Step 3, RAG. So a wrong hit is not merely a stale answer: the grounding
that would have answered the question from the candidate's own corpus never
runs. Being fast at answering a question nobody asked is a worse outcome than
being slow at answering the right one.
"""

import re
import unicodedata

# Entries are checked in order — more specific entries come first so a keyword
# never shadows a more precise phrase. Each entry has:
#   phrases:  normalized whole-word runs that trigger the answer (strong match)
#   keywords: normalized keywords that trigger the answer (weaker match)
#   answer:   pre-generated response in the candidate's voice
#
# PHRASES ARE WORD-ANCHORED, NOT SUBSTRINGS. `if phrase in normalized` was
# satisfied by a longer word that happens to contain the phrase: "¿Qué es
# ragged?" contains "que es rag", and "Háblame de tía" contains "hablame de ti".
# Both were answered with a pre-generated reply. A real question contains its
# phrase on word boundaries, so anchoring both ends costs no hit and stops the
# vocabulary from matching itself.
#
# Keyword vocabulary is deliberately tiny — three terms. A keyword matches as a
# whole word anywhere in the question, so a generic word selects a
# pre-generated answer for questions it does not address. "¿Qué cobertura de
# tests tiene el proyecto de fraude?" is not "¿Haces tests?", and "el sistema
# presenta una arquitectura de tres capas" is not "¿Cuéntame sobre ti?". Six
# keywords that did exactly that (`presenta`, `la ia`, `ia generativa`,
# `aprendiste`, `bases de datos`, `debilidades`) have been removed, and the
# questions they used to serve are now covered by phrases. Only contextually
# specific terms belong in `keywords`; anything broader belongs in `phrases`,
# which require the surrounding words to match.
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
        ],
        # `presenta` and `presentacion` are gone, and this is the reason they
        # were ever here: they are not contextually specific, they are just
        # Spanish words that a sentence about a SYSTEM also contains.
        #   "el proyecto PRESENTA una arquitectura de tres capas"  -> the pitch
        #   "qué PRESENTACIÓN usaste para el pitch"                 -> the pitch
        # Both were answered with "Soy Mikel, desarrollador junior DAM..." by a
        # literal cache that short-circuits RAG (streaming.py:458), so the
        # grounded answer never ran. "presentate" survives because it is a
        # whole word the candidate says about himself, and it is a phrase, so
        # it needs its neighbours to match.
        "keywords": [],
        "answer": (
            "Soy Mikel, desarrollador junior DAM. Estudié Desarrollo de Aplicaciones "
            "Multiplataforma en Tartanga y antes era gerente en Mercadona, liderando un equipo "
            "de unas 50 personas, pero quise dar un giro y dedicarme a algo que me apasiona: "
            "el desarrollo de software."
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
        # `interviewtts` is gone as a keyword, and it is the exact case the rule
        # above is about: the proper noun of the flagship project occurs in
        # almost EVERY question a recruiter asks about that project, so as a
        # keyword it stopped saying which question was being asked. Measured,
        # before this change, over the nine real questions about the project:
        # it matched eight, and the five that ask about a FACET of it were
        # answered with the project's DEFINITION.
        #   "que stack tiene interviewtts en produccion"      -> the definition
        #   "que tecnologias usa interviewtts"                 -> the definition
        #   "el proyecto interviewtts de docker lo dirigiste tu" -> definition
        #   "cuanto cuesta alojar interviewtts en un vps"      -> the definition
        #   "que pruebas tiene interviewtts"                   -> the definition
        # None of those five is the definition of the project, and none is
        # answered by it. Because a hit RETURNS before Step 3, RAG
        # (streaming.py:458, ahead of :463), this was not a stale answer but a
        # SUPPRESSED correct one: the retriever was measured to hold the page
        # for every one of them (projects/interview-tts.md in the top 3 for
        # four, skills/testing.md second for the fifth), and
        # tests/real_wiki.py carries "que tecnologias usa interviewtts" as a
        # hand-authored retrieval label for exactly that page.
        #
        # What keeps the four questions that DO identify the project hitting is
        # the phrases above: they name the project and require their
        # neighbours. A word that occurs in most questions about a topic
        # discriminates nothing; pinned by
        # tests/test_response_cache_precision.py::TestAKeywordHasToDiscriminate.
        "keywords": [],
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
        "phrases": ["cuales son tus debilidades"],
        # `puntos debiles` and the `debilidades` keyword are gone for the same
        # reason they were wrong everywhere else: the answer is the CANDIDATE'S
        # self-assessment, so the question has to be about the candidate.
        # "el sistema tiene dos puntos debiles que describo abajo" asks about
        # the system, and it was answered with "Soy algo desordenado" by a
        # literal cache that short-circuits RAG (streaming.py:458). The phrase
        # that remains names the referent explicitly.
        "keywords": [],
        # This answer was reported as a fabricated self-disclosed weakness. It
        # is not fabricated. wiki/faq/fortalezas-y-debilidades.md states it in
        # full, including the mitigation the answer reproduces:
        #   line 21: "Soy algo desordenado. Lo reconozco y lo gestiono con
        #     herramientas -- en desarrollo uso Git, tests y checklists para
        #     compensarlo."
        #   line 24: "No inventes debilidades falsas: 'perfeccionista' no
        #     convence a nadie. 'Desordenado' es real y demostrable que lo
        #     gestionas."
        # and line 25 gives the rule this answer already follows ("siempre
        # acompaña la debilidad con mitigación"). The page is tracked (git ls-
        # files wiki) and is one of the 37 pages the loader serves, so this is
        # not a page the candidate has only locally.
        #
        # Removing it would have been the defect: a cached answer to "what are
        # your weaknesses" that discloses nothing, on the strength of a
        # word-coverage metric that cannot tell a supported claim from an
        # unsupported one. tests/test_response_cache.py::
        # test_the_claim_scan_accepts_the_disclosure_the_wiki_makes is the
        # control that pins this.
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
        "keywords": [],
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
            "has usado ia generativa",
        ],
        # `la ia` and `ia generativa` are gone, and neither was ever a question:
        # both are nouns that appear inside questions ABOUT the AI, which is
        # the opposite of a question FOR the candidate's opinion of it.
        #   "cual es la IA que usas para recuperar el contexto"  -> the opinion
        #   "la IA generativa genera la respuesta final"         -> the opinion
        # "has usado ia generativa" is added as a PHRASE to keep the one
        # positive those keywords used to serve, which is the trade the module
        # asks for: a question that needs its neighbours, not a bare noun.
        "keywords": [],
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
        # `aprendiste` is gone and `aprendes` stays. The difference is the whole
        # point: this answer is about a METHODOLOGY, so the question has to ask
        # how. "que aprendiste del proyecto de Mercadona" is a question about
        # the content of one project, and it was answered with "Soy
        # autodidacta...". The phrase "como aprendiste" still covers the
        # methodology asked in the past tense.
        "keywords": ["aprendes"],
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
        "keywords": [],
        # The old answer ended "También lo usé en proyectos del DAM para bases de
        # datos y scripts". The corpus CONTRADICTS that clause rather than
        # merely omitting it: wiki/stories/autodidacta-fastapi-docker-async.md:16
        # lists what the FP actually taught -- "Java, JavaScript, PHP, SQL,
        # estructura en capas, patrones de diseño" -- and Python is not in it,
        # while line 28 says "Aprendí Python por mi cuenta". Claiming coursework
        # the candidate's own page assigns to self-study is the one defect class
        # a vocabulary scan cannot catch (every term in both sentences is
        # attributed by the corpus), so it was found by reading the pages.
        "answer": (
            "Python es mi lenguaje principal. Lo uso en InterviewTTS con FastAPI para "
            "el backend, la integración de IA y el procesamiento de voz. Lo aprendí por "
            "mi cuenta, más allá del temario del FP, hasta tener un proyecto funcional "
            "con FastAPI."
        ),
    },
    {
        "phrases": [
            "que experiencia tienes con docker",
            "que sabes de docker",
            "has usado docker",
            "trabajas con docker",
        ],
        "keywords": [],
        # This answer used to claim "He usado Docker con docker-compose para
        # desplegar InterviewTTS en un VPS". There is no Dockerfile and no
        # compose file anywhere in this repository, so that was a fabricated
        # credential delivered to a recruiter as the candidate's own
        # professional experience -- and because it is a cache entry it never
        # reaches RAG, so the grounding work cannot catch it.
        #
        # What replaces it asserts only what the deploy path proves: a
        # systemd unit (deployment/interviewtts.service) running uvicorn from a
        # venv bound to 127.0.0.1, behind nginx terminating TLS
        # (nginx/interview.conf:69,73-74,108). It claims nothing about the
        # candidate's history with Docker, because that is a biographical
        # claim this repository cannot support either way, and a plausible
        # lie is worse than saying what the project really does.
        "answer": (
            "En InterviewTTS no hay contenedores. Lo despliego en un VPS: un servicio "
            "systemd arranca uvicorn desde un venv de Python escuchando solo en localhost, "
            "y nginx delante termina el TLS y hace de proxy inverso. Docker no lo uso "
            "en este proyecto."
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
        # Two areas, because that is what the wiki attributes. The previous
        # answer added a third -- "integración de la inteligencia artificial" --
        # which no page supports: wiki/faq/area-preferida.md names backend and
        # data, twice, and enumerates frontend, DevOps and backend as the areas
        # it considered. A cached answer is spoken verbatim to an interviewer,
        # so an unsupported area here is not a documentation nit; it is the
        # candidate claiming something they have not said. Guarded by
        # tests/test_response_cache.py::test_the_preferred_area_answer_is_not_
        # left_claiming_more_than_its_page.
        "phrases": [
            "que area del desarrollo te gusta mas",
            "que area te gusta mas",
            "que te gusta mas del desarrollo",
            "frontend o backend",
        ],
        "keywords": [],
        "answer": (
            "Me gusta todo, pero si tuviera que elegir: backend o datos. Me gusta "
            "diseñar APIs y modelar bases de datos."
        ),
    },
    {
        "phrases": [
            "que sabes de bases de datos",
            "que experiencia tienes con bases de datos",
            "has usado bases de datos",
            "bases de datos has usado",
            "que sabes de sql",
        ],
        # `bases de datos` is gone as a keyword. It is a topic name, and this
        # answer is the candidate's EXPERIENCE with it, so the two have to be
        # told apart by the words around them:
        #   "como modelarias las BASES DE DATOS de un inventario" -> the stacks
        # That question asks for a schema design, and a literal list of the
        # tools he has touched is not an answer to it -- but the positive
        # "que bases de datos has usado" has to keep hitting, so it is added
        # above as a phrase. That is the whole trade this module documents:
        # broad words belong in phrases, never in keywords.
        "keywords": [],
        # "Triggers y procedimientos almacenados" and "Hibernate para ORM en
        # Java" were both reported as unsupported. Both are attributed:
        #   triggers / stored procedures -> wiki/skills/backend.md:33 ("SQL
        #     across MySQL, PostgreSQL, and PL-SQL -- comfortable with complex
        #     queries, triggers, stored procedures"); "procedimientos
        #     almacenados" is the Spanish for the same claim on a page written
        #     in English, not a different one
        #   Hibernate ORM in Java       -> wiki/skills/backend.md:20,32
        #     ("Hibernate | working | DAM Java persistence exercises",
        #     "comfortable with ORM mapping, JPQL, entity relationships")
        #   MySQL, PostgreSQL, MongoDB   -> wiki/skills/data.md:16-18 and
        #     wiki/profile/mikel.md:37
        #   database design, complex queries -> wiki/skills/backend.md:19,33
        # Kept, with the citations attached so the next reader does not have to
        # re-derive that these are grounded.
        "answer": (
            "Trabajo con MySQL, PostgreSQL y MongoDB. En el DAM hice diseño de bases de datos, "
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
        # The old answer ended "y no tenerle miedo a lo que esté por venir". The
        # word does not occur anywhere in the corpus -- `rg -i miedo wiki/`
        # returns nothing -- so it was a self-assessment invented and then
        # volunteered to a recruiter. The rest is grounded and is kept:
        # wiki/faq/fortalezas-y-debilidades.md:17 ("Resolutivo: cuando surge un
        # problema, busco soluciones en vez de quedarme parado. Si no sé algo, lo
        # investigo hasta resolverlo") and
        # wiki/stories/aprendizaje-autodidacta.md:21 ("aplico lo aprendido en un
        # proyecto real lo antes posible"), both of which are now cited in the
        # answer rather than paraphrased past.
        "answer": (
            "Mi mayor logro es ser autodidacta: aprender por mi cuenta lo que me "
            "hace falta y aplicarlo en un proyecto real lo antes posible. Y ser "
            "resolutivo: cuando no sé algo, lo investigo hasta resolverlo."
        ),
    },
    {
        "phrases": [
            "que sabes de apis rest",
            "que son las apis rest",
            "has creado apis",
            "que sabes de api",
        ],
        "keywords": [],
        # Three clauses removed, all of them ungrounded and none of them
        # replaced: "y consumo" (no page attributes consuming a third-party
        # API; `rg -i "consumo|consumir" wiki/` returns nothing),
        # "gestión de sesiones" (`rg -i "sesion|sesión" wiki/` returns nothing)
        # and "verbos HTTP ... diseño de contratos" (`rg -i verbo` and
        # `rg -i contrato` both return nothing).
        #
        # "status codes" survives because wiki/skills/testing.md:27 attributes
        # it -- as something the candidate TESTS ("Endpoints API (integración):
        # request/response, status codes, errores"). So the answer now says what
        # he actually does with them instead of claiming a grasp of HTTP verbs
        # no page records. What is kept: wiki/skills/backend.md:17,23 (FastAPI,
        # REST API design, chat endpoints, SSE audio streaming) and
        # wiki/projects/interview-tts.md:32.
        "answer": (
            "Diseño APIs REST con FastAPI. En InterviewTTS creé los endpoints de "
            "conversación y el streaming de audio con SSE, y los testeo a nivel de "
            "integración: request, response, status codes y errores."
        ),
    },
    {
        "phrases": [
            "que es rag",
            "que es retrieval augmented",
        ],
        "keywords": [],
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
        "keywords": [],
        # Keyed on WHY THIS PROGRAMME, and the old answer never named it -- it
        # talked about an attraction to software and to AI and stopped there, so
        # a recruiter asking "why did you choose DAM" got an answer that could
        # have been given by anyone who ever liked computers.
        #
        # Removed, ungrounded: "A raíz de descubrir la programación, mi cabeza
        # hizo click y empezó un no parar de querer saber más y aumentar mis
        # conocimientos" (`rg -i "click|hizo clic" wiki/` returns nothing --
        # an invented cognitive event, told to an interviewer as a memory), and
        # "hasta su aplicación en el día a día y en los negocios" (the corpus
        # attributes AI to *development*, never to business as a field of
        # application: wiki/stories/autodidacta-fastapi-docker-async.md:18 says
        # "el mundo de la inteligencia artificial aplicada al desarrollo"; the
        # only business/AI lines in the corpus,
        # wiki/projects/pagina-web-practicas.md:24,34, describe a feature built
        # at the Ceesa internship, not an attraction to the subject).
        #
        # What replaces it, every clause cited:
        #   the programme and the school  -> wiki/profile/mikel.md:24 ("FP
        #       Superior Desarrollo de Aplicaciones Multiplataforma (DAM) at
        #       TCIFP TARTANGA LHII (Erandio, presencial) ... finished 2026"),
        #       and wiki/faq/presentacion-30-segundos.md:15
        #   "siempre me atrajo la tecnología" -> wiki/faq/por-que-dejar-
        #       supermercados.md:15,18, which is the candidate's own page for
        #       exactly this motivation
        #   what the FP taught               -> wiki/stories/autodidacta-
        #       fastapi-docker-async.md:16 ("Java, JavaScript, PHP, SQL,
        #       estructura en capas, patrones de diseño")
        #   the AI and "de lleno"            -> same page, :18 and :29
        "answer": (
            "Elegí DAM, el FP Superior de Desarrollo de Aplicaciones Multiplataforma que "
            "estudié en TCIFP Tartanga LHII, porque siempre me atrajo la tecnología. Es la "
            "base que me da el cambio de carrera: Java, SQL, estructura en capas y "
            "patrones de diseño. Y al salir del temario me topé con el mundo de la "
            "inteligencia artificial aplicada al desarrollo, en el que me he metido de lleno."
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
    matches when any of its phrases appears in the normalized question as a run
    of WHOLE WORDS, or any of its keywords appears as a whole word. Entries are
    checked in order. Returns None when there is no match, so the caller can
    fall back to the LLM.

    Phrases are word-anchored, and that is not a detail. As a bare substring,
    "que es rag" was satisfied by "¿Qué es ragged?" and "hablame de ti" by
    "Háblame de tía": the cache answered a question built around a longer word
    that happens to contain the phrase. Anchoring both ends costs nothing --
    a real question contains its phrase on whole-word boundaries -- and stops
    the vocabulary from matching itself.
    """
    normalized = normalize_text(question)
    if not normalized:
        return None

    for entry in _CACHED_QUESTIONS:
        for phrase in entry["phrases"]:
            if re.search(rf"\b{re.escape(phrase)}\b", normalized):
                return entry["answer"]
        for keyword in entry["keywords"]:
            if re.search(rf"\b{re.escape(keyword)}\b", normalized):
                return entry["answer"]

    return None
