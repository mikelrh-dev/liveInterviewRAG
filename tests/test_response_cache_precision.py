"""The literal cache must answer a question only when it recognises THAT question.

THE DEFECT
----------
``get_cached_response`` matched on a raw substring:

    for phrase in entry["phrases"]:
        if phrase in normalized:          # <- no word boundary
            return entry["answer"]

A substring test is satisfied by a word that merely CONTAINS the phrase, or by
a phrase that merely OCCURS in a longer sentence about something else. Combined
with keywords that are whole words but far too generic, that produced answers to
questions nobody asked. Measured with the production function, all eight of these
unrelated questions returned a pre-generated answer:

    "el proyecto presenta una arquitectura de tres capas"
        -> "Soy Mikel, desarrollador junior DAM..."        (keyword "presenta")
    "qué presentación usaste para el pitch"
        -> the same                                       (phrase "presentacion")
    "el sistema presenta métricas de recall"
        -> the same                                       (keyword "presenta")
    "cuál es la IA que usas para recuperar el contexto"
        -> "Para mí la IA es una palanca enorme..."      (keyword "la ia")
    "la IA generativa genera la respuesta final"
        -> the same                                       (keywords "la ia",
                                                            "ia generativa")
    "qué aprendiste del proyecto de Mercadona"
        -> "Soy autodidacta..."                           (keyword "aprendiste")
    "cómo modelarías las bases de datos de un inventario"
        -> "Trabajo con MySQL, PostgreSQL y MongoDB..."   (keyword
                                                            "bases de datos")
    "el sistema tiene dos puntos débiles que describo abajo"
        -> "Soy algo desordenado..."                      (phrase "puntos
                                                            debiles")

WHERE THE HITS LAND
-------------------
``backend/turns/streaming.py:481`` consults the cache at Step 2 and RETURNS
before Step 3, RAG. So these are not answers that merely look cached: the
retrieval that would have grounded the reply in the candidate's own corpus
never runs. The module's own header already says the policy that was broken:
generic words belong in ``phrases``, which require the surrounding words, and
never in ``keywords``, which match anywhere.

WHAT IS NOT DONE HERE
---------------------
No generic ``keywords`` are reintroduced to recover the hit rate. The vocabulary
was emptied on purpose; putting the words back is what produced the table above.
The question phrases that must keep hitting are covered by phrases, which is
what the module asked for in the first place.
"""

import re

import pytest

from backend.services.response_cache import (
    _CACHED_QUESTIONS,
    get_cached_response,
    normalize_text,
)


def _matches(term: str, question: str) -> bool:
    """Whole-word match, the same rule ``get_cached_response`` applies."""
    return bool(re.search(rf"\b{re.escape(term)}\b", normalize_text(question)))


class TestUnrelatedQuestionsReachTheRetriever:
    """The eight probes. None of them is the question any entry answers."""

    @pytest.mark.parametrize(
        "question",
        [
            pytest.param(
                "el proyecto presenta una arquitectura de tres capas",
                id="presenta-as-a-verb",
            ),
            pytest.param(
                "qué presentación usaste para el pitch", id="presentacion"
            ),
            pytest.param(
                "el sistema presenta métricas de recall", id="presenta-again"
            ),
            pytest.param(
                "cuál es la IA que usas para recuperar el contexto", id="la-ia"
            ),
            pytest.param(
                "la IA generativa genera la respuesta final", id="ia-generativa"
            ),
            pytest.param(
                "qué aprendiste del proyecto de Mercadona", id="aprendiste"
            ),
            pytest.param(
                "cómo modelarías las bases de datos de un inventario",
                id="bases-de-datos",
            ),
            pytest.param(
                "el sistema tiene dos puntos débiles que describo abajo",
                id="puntos-debiles",
            ),
        ],
    )
    def test_the_cache_stays_out_of_the_way(self, question):
        answer = get_cached_response(question)
        assert answer is None, (
            f"the literal cache answered a question it does not recognise: "
            f"{question!r}\n  -> {answer!r}\n"
            "This short-circuits RAG (streaming.py:481), so the reply the "
            "recruiter hears is a pre-generated answer to a different question."
        )


class TestPhrasesMatchOnWordBoundaries:
    """A phrase is a run of whole words, not a run of characters."""

    def test_a_phrase_does_not_match_inside_a_longer_word(self):
        # "rag" inside "ragged": the substring test was satisfied, the word
        # boundary is not.
        question = "¿Qué es ragged?"
        assert normalize_text(question) == "que es ragged"
        assert "que es rag" in normalize_text(question), (
            "precondition: the raw substring IS present, so this only passes "
            "because the match is anchored on word boundaries"
        )
        assert get_cached_response(question) is None

    def test_a_trailing_phrase_word_does_not_match_a_longer_word(self):
        assert get_cached_response("Háblame de tía") is None, (
            "'hablame de ti' inside 'hablame de tia' is a substring, not the pitch"
        )


class TestTheLegitimateFastPathSurvives:
    """The control. Precision bought with a lost hit rate is not a fix."""

    @pytest.mark.parametrize(
        ("question", "fragment"),
        [
            pytest.param("Cuéntame sobre ti", "Mikel", id="pitch"),
            pytest.param("Háblame de ti", "Mikel", id="pitch-hablame"),
            pytest.param("¿Quién eres?", "Mikel", id="pitch-quien"),
            pytest.param("¿Qué opinas de la IA?", "IA", id="ia-opinion"),
            pytest.param(
                "que opinas de la IA en el desarrollo", "IA", id="ia-opinion-dev"
            ),
            pytest.param(
                "qué bases de datos has usado", "MySQL", id="stacks"
            ),
            pytest.param("¿Qué sabes de bases de datos?", "MySQL", id="stacks-sabes"),
            pytest.param("¿Cómo aprendes algo nuevo?", "autodidacta", id="learning"),
            pytest.param(
                "¿Has usado IA generativa en tus proyectos?", "IA", id="ia-generativa"
            ),
            pytest.param("¿Cuáles son tus debilidades?", "desordenado", id="weakness"),
            pytest.param(
                "¿Cuáles son tus fortalezas?", "disciplina", id="strength"
            ),
            pytest.param(
                "¿Cuáles son tus fortalezas y debilidades?",
                "desordenado",
                id="strength-and-weakness",
            ),
            pytest.param("¿Qué es InterviewTTS?", "InterviewTTS", id="project"),
            pytest.param(
                "¿Por qué dejaste Mercadona?", "supermercado", id="career"
            ),
        ],
    )
    def test_the_question_is_still_answered_without_the_llm(self, question, fragment):
        answer = get_cached_response(question)
        assert answer is not None, (
            f"the fast path is lost for {question!r}; a cache miss here costs "
            "4-8s of LLM latency, which is the whole point of the cache"
        )
        assert fragment in answer


class TestTheGenericVocabularyStaysGone:
    """Pins the removals, so they cannot be reinstated as a latency patch."""

    @pytest.mark.parametrize(
        "keyword",
        [
            pytest.param("presenta", id="presenta"),
            pytest.param("la ia", id="la-ia"),
            pytest.param("ia generativa", id="ia-generativa"),
            pytest.param("aprendiste", id="aprendiste"),
            pytest.param("bases de datos", id="bases-de-datos"),
            pytest.param("debilidades", id="debilidades"),
        ],
    )
    def test_the_keyword_is_not_in_the_vocabulary(self, keyword):
        present = [e["phrases"][0] for e in _CACHED_QUESTIONS if keyword in e["keywords"]]
        assert not present, (
            f"the generic keyword {keyword!r} is back in the vocabulary of "
            f"{present}. Reinstating it to win back latency is what produced "
            "the misroutes above."
        )

    def test_the_ambiguous_weakness_phrase_is_gone(self):
        """The ambiguity of "puntos débiles": the referent has to be named.

        The entry's remaining phrases are all explicitly about the candidate
        ("cuáles son tus debilidades"), which is the only referent the answer
        can speak to. "el sistema tiene dos puntos débiles" asks about the
        system, and the candidate's self-assessment is not an answer to it.
        """
        phrases = [p for e in _CACHED_QUESTIONS for p in e["phrases"]]
        assert "puntos debiles" not in phrases


# ─── The project's own name was a catch-all ──────────────────────────────
#
# ONE DEFECT, MEASURED WITH THE PRODUCTION FUNCTION
# ------------------------------------------------
# The project entry carried ``keywords: ["interviewtts"]`` beside five phrases.
# The proper noun of the candidate's flagship project appears in ALMOST EVERY
# question a recruiter asks about that project, so the keyword stopped
# discriminating between "what IS this project" and "tell me about its stack".
# Measured, before the fix:
#
#     "que stack tiene interviewtts en produccion"  -> the definition  WRONG
#     "que tecnologias usa interviewtts"             -> the definition  WRONG
#     "el proyecto interviewtts de docker lo dirigiste tu" -> definition  WRONG
#     "cuanto costa alojar interviewtts en un vps"  -> the definition  WRONG
#     "que pruebas tiene interviewtts"               -> the definition  WRONG
#
# A cache hit RETURNS before Step 3, RAG (``streaming.py:481`` vs ``:486``),
# so this is not a stale answer -- it is a suppressed correct one. The retriever
# was measured to have the page for every one of them (``projects/interview-
# tts.md`` in the top 3 for four, ``skills/testing.md`` second for the fifth),
# and ``tests/real_wiki.py`` carries "que tecnologias usa interviewtts" as a
# hand-authored retrieval label for exactly that page.
#
# The catch-all is removed. The four questions that genuinely ask WHAT the
# project is still hit, because their identifying words are phrases and phrases
# require the surrounding words to match.

#: Every question a recruiter really asks ABOUT this project. The first four
#: ask what it IS and are the ones the cached definition answers. The rest ask
#: for something else about it -- the stack, the technologies, the hosting
#: bill, the test coverage, the Docker story -- and the corpus answers those.
#: Written the way Whisper delivers a question: lowercase, unpunctuated.
PROJECT_QUESTIONS = (
    "que es interviewtts",
    "cuentame sobre interviewtts",
    "hablame de interviewtts",
    "que es este proyecto",
    "que stack tiene interviewtts en produccion",
    "que tecnologias usa interviewtts",
    "el proyecto interviewtts de docker lo dirigiste tu",
    "cuanto cuesta alojar interviewtts en un vps",
    "que pruebas tiene interviewtts",
)

#: The subset of the above that the project's definition actually answers.
#: Derived from the entry's phrases, not restated by hand, so it cannot drift.
PROJECT_QUESTIONS_THE_DEFINITION_ANSWERS = frozenset(
    q
    for q in PROJECT_QUESTIONS
    if any(
        _matches(phrase, q)
        for entry in _CACHED_QUESTIONS
        for phrase in entry["phrases"]
    )
)


class TestQuestionsAboutTheProjectThatAskSomethingElse:
    """The five measured misroutes. Each asks about a FACET, not the identity."""

    @pytest.mark.parametrize(
        "question",
        [
            pytest.param(
                "que stack tiene interviewtts en produccion", id="stack"
            ),
            pytest.param("que tecnologias usa interviewtts", id="technologies"),
            pytest.param(
                "el proyecto interviewtts de docker lo dirigiste tu", id="docker"
            ),
            pytest.param(
                "cuanto cuesta alojar interviewtts en un vps", id="hosting-cost"
            ),
            pytest.param("que pruebas tiene interviewtts", id="test-coverage"),
        ],
    )
    def test_the_facet_question_reaches_the_retriever(self, question):
        answer = get_cached_response(question)
        assert answer is None, (
            f"the project's own name answered a question about one of its "
            f"facets: {question!r}\n  -> {answer!r}\n"
            "The definition of the project is not an answer about its stack, "
            "its technologies, its hosting bill or its tests. The cache "
            "returns before RAG (streaming.py:481), so the grounded answer "
            "the corpus already holds never runs."
        )


class TestTheQuestionsThatIdentifyTheProjectStillHit:
    """Precision bought with a lost hit rate is not a fix."""

    @pytest.mark.parametrize(
        "question",
        [
            pytest.param("que es interviewtts", id="what-is"),
            pytest.param("cuentame sobre interviewtts", id="tell-me"),
            pytest.param("hablame de interviewtts", id="speak-to-me"),
            pytest.param("que es este proyecto", id="what-is-this"),
        ],
    )
    def test_the_identity_question_still_skips_the_llm(self, question):
        answer = get_cached_response(question)
        assert answer is not None, (
            f"the fast path is lost for {question!r}; a miss here costs 4-8s "
            "of LLM latency, which is the whole point of the cache"
        )
        assert "simulación de entrevista" in answer


class TestAKeywordHasToDiscriminate:
    """The criterion, stated once and applied to every entry and every keyword.

    A keyword is a whole-word match ANYWHERE in the question, so the only thing
    that makes one legitimate is that it separates the questions the entry
    answers from the ones it does not. A word that appears in most of the
    questions about a topic separates nothing: it is a topic label, and topic
    labels belong in ``phrases``, which need their neighbours.
    """

    def test_a_keyword_never_selects_an_answer_for_a_question_it_does_not_address(
        self,
    ):
        offenders = []
        for entry in _CACHED_QUESTIONS:
            addressed = {
                q
                for q in PROJECT_QUESTIONS
                if any(_matches(phrase, q) for phrase in entry["phrases"])
            }
            for keyword in entry["keywords"]:
                selected = {
                    q for q in PROJECT_QUESTIONS if _matches(keyword, q)
                }
                misrouted = selected - addressed
                if misrouted:
                    offenders.append((keyword, sorted(misrouted)))
        assert not offenders, (
            "keywords matching questions their entry does not answer: "
            f"{offenders}\n"
            f"Measured over the {len(PROJECT_QUESTIONS)} questions a recruiter "
            "asks about this project. A keyword may only select questions the "
            "entry's phrases also select; everything else has to reach the "
            "retriever, which has the page."
        )

    def test_a_word_in_almost_every_question_about_a_topic_cannot_be_a_keyword(self):
        """The catch-all, stated as a ratio rather than as a word list.

        ``interviewtts`` matched 8 of the 9 -- everything except "que es este
        proyecto", which names the project with a pronoun instead. A keyword
        that fires on a MAJORITY of the questions about a topic carries no
        information about which of them the answer is for; below the majority
        it is still discriminating, which is why the threshold is a majority
        and not zero.
        """
        majority = [
            (keyword, len(selected), len(PROJECT_QUESTIONS))
            for entry in _CACHED_QUESTIONS
            for keyword in entry["keywords"]
            if len(selected := [q for q in PROJECT_QUESTIONS if _matches(keyword, q)])
            * 2
            > len(PROJECT_QUESTIONS)
        ]
        assert not majority, (
            f"keywords present in a majority of the project questions: {majority}\n"
            "That is a topic label, not a discriminator. Move it into "
            "`phrases`, where the surrounding words have to match too."
        )

