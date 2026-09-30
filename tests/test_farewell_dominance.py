"""A farewell phrase must DOMINATE the sentence, not merely appear in it.

THE DEFECT
----------
``detect_farewell`` asked one question -- "does any pattern appear anywhere in
this transcript?" -- and the answer is almost always yes, because a candidate
mentions wrapping up in the middle of an answer all the time. Measured against
the production function, six of these seven ordinary sentences ended the
interview:

    "en cuanto a base de datos no tengo dudas uso postgres"      -> ended
    "no tengo preguntas pero sí te quiero preguntar por DAM"    -> ended
    "vale ya está perfecto nos vemos en la siguiente ronda"      -> ended
    "el equipo ya estamos bien organicé yo las reuniones"       -> ended
    "no tengo más dudas sobre el proyecto de fraude pero..."    -> ended
    "sobre el despliegue ya está todo claro"                    -> ended

The second one contradicts itself inside a single sentence: the candidate says
they have no questions and then asks one. "no tengo dudas uso postgres" is a
statement about one topic, not a sign-off. "ya estamos bien" describes a team,
not the interview.

The cost is not a wrong boolean. ``detect_farewell`` is what makes the pipeline
speak ``FAREWELL_TEXT`` and emit ``interview_end``, so every one of those is a
live interview terminated by the candidate's own words, mid-answer.

THE RULE
--------
A closing phrase has to be the dominant clause of the utterance. Two checks,
both applied to the text that FOLLOWS the match:

  1. No contradicting connector immediately after -- "pero", "aunque", "y",
     or an opening parenthesis. A negative in the forward direction is the
     minimum: the candidate just told us they are continuing.
  2. Nothing substantive after it. "no tengo dudas" followed by "uso postgres"
     is a mention, not a sign-off; "nos vemos" followed by "en la siguiente
     ronda" defers the goodbye rather than saying it.

Patterns that are *designed* to span clauses -- "gracias ... nos vemos" -- are
exempt from (2) and only subject to (1), because spanning is their whole job.
The pattern list says which is which, rather than the two kinds being guessed
at from the text.

WHAT MUST NOT BREAK
-------------------
The clean farewells are the product working: "cuando quieras, nos vemos" and
"eso es todo, gracias" end the interview, and so does the fixture every
farewell test in the suite uses, "Muchas gracias, eso es todo".
"""

import pytest

from backend.farewell import detect_farewell


class TestACandidateMentioningTheEndIsNotEnding:
    """The seven sentences, all of which end the interview today."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "en cuanto a base de datos no tengo dudas uso postgres",
                id="no-dudas-then-keeps-going",
            ),
            pytest.param(
                "no tengo preguntas pero sí te quiero preguntar por el Reto de DAM",
                id="contradicts-itself",
            ),
            pytest.param(
                "vale ya está perfecto nos vemos en la siguiente ronda",
                id="nos vemos-in-the-next-round",
            ),
            pytest.param(
                "el equipo ya estamos bien organicé yo las reuniones",
                id="ya-estamos-about-a-team",
            ),
            pytest.param(
                "gracias espera que se me ha olvidado una cosa sobre los tests",
                id="gracias-then-a-new-question",
            ),
            pytest.param(
                "no tengo más dudas sobre el proyecto de fraude pero tengo otra "
                "pregunta",
                id="no-dudas-then-asks-one",
            ),
            pytest.param(
                "sobre el despliegue ya está todo claro",
                id="ya-esta-about-a-topic",
            ),
        ],
    )
    def test_the_interview_continues(self, text):
        assert detect_farewell(text) is False, (
            f"this ends a live interview: {text!r}. The candidate mentioned "
            "wrapping up, or mentioned a topic that happened to match, and the "
            "pipeline would have said goodbye and emitted interview_end."
        )


class TestAForwardNegativeIsEnough:
    """The minimum fix, isolated: a connector right after the closing phrase."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("nos vemos pero seguimos con el Reto de DAM", id="pero"),
            pytest.param("eso es todo aunque me queda una duda", id="aunque"),
            pytest.param("adiós y espera que no he terminado", id="y"),
            pytest.param("no tengo más preguntas (bueno, una cosa más)", id="paren"),
        ],
    )
    def test_a_contradicting_connector_cancels_the_farewell(self, text):
        assert detect_farewell(text) is False

    def test_it_also_cancels_a_spanning_pattern(self):
        """The rule earns its keep on the patterns dominance cannot reach.

        "muchas gracias por tu tiempo" is a *spanning* pattern: the clause after
        it is what makes it a farewell, so the dominance rule exempts it
        deliberately. That exemption is only safe because the connector check
        still applies -- so this case is what stops the exemption becoming a
        hole. Drop the connector check and this sentence ends the interview.
        """
        text = "muchas gracias por tu tiempo pero me queda una duda sobre el despliegue"
        assert detect_farewell(text) is False


class TestTheClosingPhraseMustDominate:
    """Substantive content after the phrase means it was a mention."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("ya estamos bien con el proyecto", id="ya-estamos"),
            pytest.param("ya está claro el despliegue", id="ya-esta"),
            pytest.param("no tengo dudas de python", id="no-tengo-dudas"),
            pytest.param("nos vemos en la siguiente ronda", id="nos-vemos"),
            pytest.param("eso es todo sobre el Reto de DAM", id="eso-es-todo"),
        ],
    )
    def test_content_after_the_phrase_keeps_the_interview_open(self, text):
        assert detect_farewell(text) is False, (
            f"the closing phrase was only a mention inside a longer sentence: {text!r}"
        )


class TestTheCleanFarewellsStillEndTheInterview:
    """The positive control. Fixing the false positives must not cost these."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("cuando quieras, nos vemos", id="nos-vemos"),
            pytest.param("eso es todo, gracias", id="eso-es-todo-gracias"),
            # The fixture every farewell test in the suite drives. If this stops
            # ending the interview, test_farewell.py goes quiet in a way that
            # looks like a pass.
            pytest.param("Muchas gracias, eso es todo", id="suite-fixture"),
            pytest.param("gracias, eso es todo", id="report-service-fixture"),
            pytest.param("fue un placer", id="fue-un-placer"),
            pytest.param("gracias por tu tiempo", id="gracias-por-tu-tiempo"),
            # "ha sido un placer" on its own is NOT a positive here: the pattern
            # requires a "gracias" antecedent, and it did before this change
            # too. Asserting it would be asking for a new capability inside a
            # class whose whole job is to prove the fix cost nothing.
            pytest.param(
                "muchas gracias, ha sido un placer", id="gracias-ha-sido-placer"
            ),
            pytest.param("terminamos la entrevista por hoy", id="terminamos"),
            pytest.param("adiós", id="adios"),
            pytest.param("hasta luego", id="hasta-luego"),
            pytest.param("nada más", id="nada-mas"),
            pytest.param("no tengo más preguntas", id="no-mas-preguntas"),
        ],
    )
    def test_the_interview_ends(self, text):
        assert detect_farewell(text) is True, (
            f"a clean farewell no longer ends the interview: {text!r}. The fix "
            "for the false positives has eaten the true ones."
        )


class TestOrdinaryAnswersAreUntouched:
    """Nothing that never matched can start matching."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Cuéntame sobre el detector de fraude", id="fraude"),
            pytest.param("How do you architect a system?", id="english"),
            pytest.param("Uso PostgreSQL para el inventario", id="postgres"),
            pytest.param("El Reto de DAM consistía en construir una API", id="reto"),
        ],
    )
    def test_a_plain_answer_is_not_a_farewell(self, text):
        assert detect_farewell(text) is False
