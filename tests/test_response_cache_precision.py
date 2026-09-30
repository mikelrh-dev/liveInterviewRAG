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
``backend/turns/streaming.py:288`` consults the cache at Step 2 and RETURNS
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

import pytest

from backend.services.response_cache import (
    _CACHED_QUESTIONS,
    get_cached_response,
    normalize_text,
)


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
            "This short-circuits RAG (streaming.py:288), so the reply the "
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
        """\"puntos débiles\" answers for the candidate, not for the system.

        The entry's remaining phrases are all explicitly about the candidate
        ("cuáles son tus debilidades"), which is the only referent the answer
        can speak to. "el sistema tiene dos puntos débiles" asks about the
        system, and the candidate's self-assessment is not an answer to it.
        """
        phrases = [p for e in _CACHED_QUESTIONS for p in e["phrases"]]
        assert "puntos debiles" not in phrases
