"""Tests for the response cache service (backend/services/response_cache.py)."""

import fnmatch
import os
import re
import tempfile
from pathlib import Path

import pytest

from tests.real_wiki import (
    COMMENT_FIGURES,
    CORPUS_DIGESTS,
    comment_figures_for,
    corpus_digest,
    resolved_cases,
)

from backend.services.response_cache import (  # noqa: F401  (_CACHED_QUESTIONS: repo precedent tests/test_api.py)
    _CACHED_QUESTIONS,
    get_cached_response,
    normalize_text,
)

# ─── normalize_text ───────────────────────────────────────


def test_normalize_lowercases_and_strips_accents():
    """Normalization removes accents, case and question marks."""
    assert normalize_text("¿Cuáles son tus FORTALEZAS?") == "cuales son tus fortalezas"


def test_normalize_removes_punctuation_and_collapses_spaces():
    """Punctuation becomes spaces and whitespace is collapsed."""
    assert normalize_text("Háblame de ti... ¡por favor!") == "hablame de ti por favor"


def test_normalize_empty_input():
    """Empty input normalizes to an empty string."""
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


# ─── get_cached_response: hit variants ────────────────────


def test_pitch_question_cuentame():
    """'Cuéntame sobre ti' returns the 30-second pitch."""
    answer = get_cached_response("Cuéntame sobre ti")
    assert answer is not None
    assert "Mikel" in answer


def test_pitch_question_hablame_de_ti():
    """'Háblame de ti' returns the pitch too."""
    answer = get_cached_response("Háblame de ti")
    assert answer is not None
    assert "Mikel" in answer


def test_pitch_question_quien_eres():
    """'¿Quién eres?' returns the pitch too."""
    answer = get_cached_response("¿Quién eres?")
    assert answer is not None
    assert "Mikel" in answer


def test_interviewtts_project_question():
    """'¿Qué es InterviewTTS?' returns the project description."""
    answer = get_cached_response("¿Qué es InterviewTTS?")
    assert answer is not None
    assert "InterviewTTS" in answer


def test_mercadona_career_change_question():
    """'¿Por qué dejaste Mercadona?' returns the career-change answer."""
    answer = get_cached_response("¿Por qué dejaste Mercadona?")
    assert answer is not None
    assert "supermercado" in answer.lower()


def test_fortalezas_question():
    """'¿Cuáles son tus fortalezas?' returns strengths."""
    answer = get_cached_response("¿Cuáles son tus fortalezas?")
    assert answer is not None
    assert "disciplina" in answer.lower()


def test_fortalezas_y_debilidades_combined_question():
    """Combined strengths+weaknesses question answers both."""
    answer = get_cached_response("¿Cuáles son tus fortalezas y debilidades?")
    assert answer is not None
    assert "disciplina" in answer.lower()
    assert "desordenado" in answer.lower()


def test_debilidades_question():
    """'¿Cuáles son tus debilidades?' returns the honest weakness."""
    answer = get_cached_response("¿Cuáles son tus debilidades?")
    assert answer is not None
    assert "desordenado" in answer.lower()


def test_por_que_trabajar_aqui_question():
    """'¿Por qué quieres trabajar aquí?' returns the why-company answer."""
    answer = get_cached_response("¿Por qué quieres trabajar aquí?")
    assert answer is not None
    assert "aprender" in answer.lower()


def test_haces_tests_question():
    """'¿Haces tests?' returns the testing approach."""
    answer = get_cached_response("¿Haces tests?")
    assert answer is not None
    assert "test" in answer.lower()


def test_opinion_ia_question():
    """'¿Qué opinas de la IA?' returns the AI opinion."""
    answer = get_cached_response("¿Qué opinas de la IA?")
    assert answer is not None
    assert "ia" in answer.lower()


def test_como_aprendes_question():
    """'¿Cómo aprendes algo nuevo?' returns the learning methodology."""
    answer = get_cached_response("¿Cómo aprendes algo nuevo?")
    assert answer is not None
    assert "autodidacta" in answer.lower()


def test_python_question():
    """'¿Qué experiencia tienes con Python?' returns the Python answer."""
    answer = get_cached_response("¿Qué experiencia tienes con Python?")
    assert answer is not None
    assert "python" in answer.lower()


def test_docker_question():
    """'¿Qué experiencia tienes con Docker?' returns a true answer.

    Asserting only that the word "docker" is present was the shape of the
    defect: it passed while the answer fabricated a docker-compose deployment
    this repository does not have. It asserts the substance — that the answer
    describes the deployment that ships and denies the one that does not.

    WHY THE ANTI-CLAIM MOVED. This used to require the answer to contain "no
    lo uso" and to forbid the string "docker-compose" outright, because when
    it was written this repository had no Dockerfile and no compose file:
    containers were a fabrication, and naming them at all WAS the defect.
    Since 2026-10-03 the root ships both, for local and containerised runs, so
    both assertions described a repository that no longer exists — and the
    honest answer now has to NAME docker-compose to say where containers are
    used at all.

    The guard is re-aimed, not weakened. The claim that is still false is
    "production runs on Docker": containers are local, production is
    systemd + nginx. So it asserts the local/production split is stated, that
    the denial is scoped to PRODUCTION rather than to Docker in general, and
    that docker-compose is never credited to the VPS deployment. Drop the
    split from the answer and this goes red.
    """
    answer = get_cached_response("¿Qué experiencia tienes con Docker?")
    assert answer is not None
    lowered = answer.lower()
    assert "docker" in lowered
    for mechanism in ("systemd", "nginx"):
        assert mechanism in lowered, (
            f"the answer no longer describes the deployment that actually "
            f"ships ({mechanism}): {answer!r}"
        )
    assert "local" in lowered, (
        f"the answer names no local scope for the containers, so it does not "
        f"say where Docker is actually used: {answer!r}"
    )
    assert re.search(r"producci[oó]n no (?:los |lo )?uso", lowered), (
        f"the answer no longer declines the Docker credential for production, "
        f"which is the part it cannot support: {answer!r}"
    )
    # docker-compose may be named for the local run; it may never be named as
    # the VPS deployment, which is systemd + nginx.
    production_clause = _normalised_text(lowered).split("produccion", 1)[-1]
    assert "docker-compose" not in production_clause, (
        f"the answer credits a compose deployment to production, which this "
        f"repository does not have: {answer!r}"
    )


def test_donde_te_ves_question():
    """'¿Dónde te ves en 5 años?' returns the future vision."""
    answer = get_cached_response("¿Dónde te ves en 5 años?")
    assert answer is not None
    assert "backend" in answer.lower() or "desarrollador" in answer.lower()


def test_area_preferida_question():
    """'¿Qué área del desarrollo te gusta más?' returns the preferred area."""
    answer = get_cached_response("¿Qué área del desarrollo te gusta más?")
    assert answer is not None
    assert "backend" in answer.lower() or "datos" in answer.lower()


def test_bases_de_datos_question():
    """'¿Qué sabes de bases de datos?' returns the database answer."""
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None
    assert "sql" in answer.lower() or "mysql" in answer.lower()


def test_trabajo_equipo_question():
    """'¿Has trabajado en equipo?' returns the teamwork answer."""
    answer = get_cached_response("¿Has trabajado en equipo?")
    assert answer is not None
    assert "equipo" in answer.lower()


def test_mayor_logro_question():
    """'¿Cuál es tu mayor logro?' returns the biggest achievement."""
    answer = get_cached_response("¿Cuál es tu mayor logro?")
    assert answer is not None
    assert "autodidacta" in answer.lower()


def test_apis_rest_question():
    """'¿Qué sabes de APIs REST?' returns the REST API answer."""
    answer = get_cached_response("¿Qué sabes de APIs REST?")
    assert answer is not None
    assert "api" in answer.lower()


def test_rag_question():
    """'¿Qué es RAG?' returns the RAG explanation."""
    answer = get_cached_response("¿Qué es RAG?")
    assert answer is not None
    assert "rag" in answer.lower()


def test_dam_question():
    """'¿Por qué elegiste DAM?' returns the DAM answer."""
    answer = get_cached_response("¿Por qué elegiste DAM?")
    assert answer is not None
    assert "tecnología" in answer.lower()


# ─── get_cached_response: miss behavior ───────────────────


def test_unknown_question_returns_none():
    """A non-cached question returns None so the caller falls back to the LLM."""
    assert get_cached_response("¿Qué stack usas en tus proyectos?") is None


def test_empty_question_returns_none():
    """Empty input returns None."""
    assert get_cached_response("") is None


def test_whitespace_only_question_returns_none():
    """Whitespace-only input returns None."""
    assert get_cached_response("   ") is None


def test_punctuation_only_question_returns_none():
    """Punctuation-only input returns None."""
    assert get_cached_response("¿?!...") is None


# ─── get_cached_response: word-boundary keyword matching ──


def test_keyword_api_inside_word_misses():
    """Keyword 'api' inside 'rápidamente' must NOT trigger the APIs answer."""
    assert get_cached_response("Necesitas responder rápidamente") is None


def test_keyword_rest_inside_word_misses():
    """Keyword 'rest' inside 'restaurante' must NOT trigger the APIs answer."""
    assert get_cached_response("¿Conoces un buen restaurante cerca de aquí?") is None


def test_keyword_presenta_inside_word_misses():
    """'representa' must NOT trigger the pitch.

    It no longer could: the `presenta` keyword was removed outright, because
    "el proyecto presenta una arquitectura de tres capas" answered with the
    pitch. So this now passes for a stronger reason than the one it was
    written for, and the word-boundary guarantee it originally demonstrated
    lives on in ``test_response_cache_precision.py`` for phrases, which are
    matched with anchors rather than exclusions. Kept because the sentence is
    worth pinning either way: it is the shape of question that used to
    misroute, and it must keep falling through.
    """
    assert get_cached_response("Este proyecto representa mucho para mí") is None


def test_keyword_exact_word_hits():
    """A kept keyword as a standalone word DOES trigger its answer."""
    answer = get_cached_response("¿Cuáles son tus fortalezas y cómo las demuestras?")
    assert answer is not None
    assert "disciplina" in answer.lower()


def test_multiword_keyword_hits():
    """'has usado ia generativa' still triggers the AI answer.

    This was a keyword match and is now a phrase. The assertion is unchanged
    and deliberately so: the question is one a recruiter really asks and the
    answer is right, so the fast path must survive the cleanup that removed
    `ia generativa` as a bare noun. What the test no longer claims is that a
    multi-word KEYWORD exists -- there are none, which is the point of the
    vocabulary.
    """
    answer = get_cached_response("¿Has usado IA generativa en tus proyectos?")
    assert answer is not None
    assert "IA" in answer


# ─── Keyword strictness: generic technical words must not trigger ──
#
# `keywords` is a weak, whole-word match anywhere in the question. Generic
# technical words ("tests", "python", "docker", "sql", "api", "rest", "rag",
# "dam") occur in recruiter questions the cached answer does not address, so
# they misroute: the cache answered "Have you used Python?" with a Spanish
# InterviewTTS answer. The cache must answer only what it recognises, and
# defer everything else to the LLM.


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("¿Qué cobertura de tests tiene el proyecto de fraude?", id="tests"),
        pytest.param("Have you used Python?", id="python"),
        pytest.param("¿Por qué no usaste Docker en producción?", id="docker-negated"),
        pytest.param("¿Prefieres SQL declarativo o modelos NoSQL?", id="sql"),
        pytest.param("¿Qué API usasteis para el bot de Telegram?", id="api"),
        pytest.param(
            "¿Alguna vez has integrado un servicio REST de terceros?", id="rest"
        ),
        pytest.param("¿Tenéis RAG en producción o es solo una demo?", id="rag"),
        pytest.param("¿Cuánto dura el ciclo formativo de DAM?", id="dam"),
    ],
)
def test_generic_technical_keyword_does_not_trigger_a_cached_answer(question):
    """A generic technical word alone must not select a pre-generated answer."""
    assert get_cached_response(question) is None


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("¿Qué API usasteis para el bot de Telegram?", id="api"),
        pytest.param("¿Por qué no usaste Docker en producción?", id="docker"),
        pytest.param("Have you used Python?", id="python-english"),
        pytest.param("¿Qué framework de Python usas en el detector?", id="python-project"),
    ],
)
def test_demonstrated_misroute_reaches_the_llm(question):
    """The four misroutes whose trigger was a generic keyword now fall through."""
    assert get_cached_response(question) is None


def test_misroute_more_about_interviewtts_now_falls_through():
    """The owner's call has been made, and the keyword is gone.

    This test used to pin the opposite: 'Cuéntame más sobre InterviewTTS'
    matched on the keyword ``interviewtts`` and on no phrase, and the answer it
    returned was the project description — topically correct, but the same text
    the candidate already heard. It was left hitting on purpose because the fix
    was to drop the keyword, and that was the owner's call rather than a silent
    edit. The call has been made and the keyword is removed, so the question
    reaches the retriever, which can be asked for more without repeating itself.

    The questions that genuinely ask WHAT the project is still hit, on their
    phrases; see ``tests/test_response_cache_precision.py``.
    """
    assert get_cached_response("Cuéntame más sobre InterviewTTS") is None, (
        "the keyword is back: any question naming the project would receive the "
        "project's definition, including the ones about its stack, its cost or "
        "its tests"
    )


# ─── Positive control: the legitimate fast path must survive ──
#
# Removing over-broad keywords must not touch `phrases`. These are the exact
# questions a recruiter asks and the answers are correct and wiki-backed, so a
# miss here is a real cost (4-8s of LLM latency) and a real regression.


@pytest.mark.parametrize(
    ("question", "expected_fragment"),
    [
        pytest.param("¿Haces tests unitarios?", "pytest", id="phrase-tests"),
        pytest.param(
            "¿Qué experiencia tienes con Python?", "FastAPI", id="phrase-python"
        ),
        pytest.param(
            "¿Qué experiencia tienes con Docker?", "systemd", id="phrase-docker"
        ),
        pytest.param("¿Qué sabes de SQL?", "Hibernate", id="phrase-sql"),
        pytest.param("¿Qué sabes de APIs REST?", "SSE", id="phrase-rest"),
        pytest.param("¿Qué es RAG?", "Retrieval Augmented", id="phrase-rag"),
        pytest.param("¿Por qué elegiste DAM?", "tecnología", id="phrase-dam"),
        pytest.param("¿Qué sabes de bases de datos?", "MySQL", id="phrase-db"),
        pytest.param("¿Has trabajado en equipo?", "equipo", id="phrase-team"),
        pytest.param("¿Por qué quieres trabajar aquí?", "aprender", id="phrase-company"),
    ],
)
def test_intact_phrase_still_returns_its_answer(question, expected_fragment):
    """An untouched multi-word phrase still selects its pre-generated answer."""
    answer = get_cached_response(question)
    assert answer is not None, f"fast path lost for {question!r}"
    assert expected_fragment in answer


def test_no_phrase_was_removed():
    """Guards the rule that only keywords may be removed, never a phrase.

    Snapshot of the phrase count (20 entries, 85 phrases). Bump it when an
    entry or phrase is added on purpose; never to silence a deletion.

    THE TOTAL IS UNCHANGED AND THAT IS NOW A TRAP, NOT A REASSURANCE. Two
    phrases WERE removed -- `presentacion` and `puntos debiles`, both of which
    matched inside longer sentences about other things -- and two were added
    (`has usado ia generativa`, `bases de datos has usado`) to keep the
    positives those matchers used to serve. The arithmetic cancels, so this
    assertion cannot tell a substitution from a no-op, and it used to. The
    removals are pinned by name in
    ``tests/test_response_cache_precision.py::TestTheGenericVocabularyStaysGone``;
    this count only guards the size of the corpus now.
    """
    assert len(_CACHED_QUESTIONS) == 20
    assert sum(len(entry["phrases"]) for entry in _CACHED_QUESTIONS) == 85


def test_only_contextually_specific_keywords_remain():
    """The keyword vocabulary is exactly the two terms that survive scrutiny.

    This set was NINE, and every one of the other seven has been removed after
    being measured answering questions it does not address -- `presenta` and
    `presentacion` against "el proyecto presenta una arquitectura", `la ia` and
    `ia generativa` against "cuál es la IA que usas", `aprendiste` against
    "qué aprendiste del proyecto de Mercadona", `bases de datos` against "cómo
    modelarías las bases de datos", `debilidades` against "el sistema tiene dos
    puntos débiles", and `interviewtts` against the five questions about the
    stack, the technologies, the Docker story, the hosting bill and the tests
    that all contain the project's name and all received the project's
    DEFINITION.

    What is left is the shortest list that has survived a real objection:

      * ``fortalezas``  -- the strengths answer is about the candidate and
        every use of the word in an interview is a question FOR it.
      * ``aprendes``    -- present tense of "how do you learn", the
        methodology the entry answers. Its past-tense twin did not survive:
        "qué aprendiste del proyecto X" is about a project's content.

    ``interviewtts`` did not survive the same objection the other seven failed,
    which is why it is the one worth stating: a proper noun can be exactly as
    generic as a common noun. It is the name of the flagship project, so it
    occurs in almost every question asked about that project, and a keyword
    that fires on almost every question about a topic discriminates nothing
    between them. It is a topic label, and topic labels belong in `phrases`,
    where the surrounding words have to match too. Pinned by name in
    ``tests/test_response_cache_precision.py::TestAKeywordHasToDiscriminate``.

    These are not lowered to zero on principle. They are what is left after the
    generic ones went, and a keyword list that cannot grow back into a
    misroute is the deliverable.
    """
    remaining = sorted({kw for entry in _CACHED_QUESTIONS for kw in entry["keywords"]})
    assert remaining == [
        "aprendes",
        "fortalezas",
    ]


# ─── Language: English questions must reach the LLM ──
#
# The cache is Spanish-only with no language awareness, and it must stay that
# way — a language detector is scope creep. The contract is instead that the
# cache answers only what it confidently recognises, so every English question
# falls through rather than returning a Spanish answer.


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("Tell me about yourself", id="pitch"),
        pytest.param("What are your strengths", id="strengths"),
        pytest.param("Why did you leave Mercadona", id="career-change"),
        pytest.param("What is RAG", id="rag"),
        pytest.param("Do you use Docker in production", id="docker"),
        pytest.param("Where do you see yourself in 5 years", id="future"),
        pytest.param("How do you learn new things", id="learning"),
        pytest.param("What database experience do you have", id="databases"),
    ],
)
def test_english_question_reaches_the_llm(question):
    """An English question must not be answered with a Spanish cached answer."""
    assert get_cached_response(question) is None


# ─── Negation: no heuristic guard, and none is needed ──
#
# "¿Por qué no usaste Docker?" matched on `docker` and returned a positive
# Docker answer. The keyword is gone, so it falls through on its own. No
# negation detector was added: a fixed token window either misses the
# demonstrated case (window=1: the token before "bases de datos" was "usaste",
# not "no") or rejects legitimate questions (window=2: "¿No puedes diseñar
# bases de datos?"). Resolving scope properly needs parsing, which contradicts
# this module's "no external dependencies" design.
#
# That example is now history: `bases de datos` was itself removed as too
# generic, so the window=1 case it illustrated no longer exists. The reasoning
# for not writing a negation guard is unchanged, and it is what the one
# surviving exposure below has to live with.


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("¿Por qué no usaste Docker en producción?", id="docker"),
        pytest.param("¿Por qué no dejaste Mercadona?", id="mercadona"),
        pytest.param("¿Por qué no elegiste DAM?", id="dam"),
        pytest.param("¿Has usado alguna vez RAG?", id="rag-not-used"),
        # These two were the documented negation exposure, listed as a known
        # limitation because `bases de datos` was considered specific enough to
        # keep. It was not: it answered "cómo modelarías las bases de datos de
        # un inventario" with a list of the tools, and dropping it closed this
        # exposure as a side effect. They belong here now because they are
        # questions the cache must decline, not because a guard was written.
        pytest.param("¿Por qué no usaste bases de datos?", id="db-why-not"),
        pytest.param("¿Nunca has trabajado con bases de datos?", id="db-never"),
        # The third keyword the negation paragraph below used to call out as a
        # live exposure. It left with the `interviewtts` keyword, so the
        # question no longer needs a guard to decline: it falls through on the
        # same footing as every other question that merely names the project.
        pytest.param("Que no es InterviewTTS?", id="project-what-is-not"),
    ],
)
def test_negated_question_never_returns_a_positive_cached_answer(question):
    """A negated question about a removed keyword falls through to the LLM."""
    assert get_cached_response(question) is None


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("Que no tienes fortalezas?", id="no-strengths"),
        pytest.param("No aprendes nada nuevo?", id="no-learning"),
    ],
)
def test_known_residual_negation_exposure_on_a_kept_keyword(question):
    """KNOWN LIMITATION — negation on a kept keyword, and it is still live.

    A negation that scopes over a kept keyword still selects a positive
    answer: the keyword is matched as a whole word with no regard to negation,
    and the entry it belongs to answers affirmatively.

    This is the case a negation guard would fix, and the case a naive guard
    would break: "no" also occurs in legitimate questions, so a keyword-level
    negation check would reject those too. Recorded here so the exposure stays
    visible and deliberate rather than accidental. Asserted as current
    behaviour on purpose — if a guard is ever added, this test is the one that
    should flip.

    It was three probes over three keywords. Two of them, both `bases de
    datos`, left with their keyword and are asserted to return nothing above.
    The third was `interviewtts` on "Que no es InterviewTTS?", and it left for
    a better reason: the keyword is gone, so the question now falls through
    with every other question that merely names the project. What replaced it
    here are the two keywords that survive, so the limitation is still pinned
    against a keyword that actually exists rather than documented in prose
    about one that no longer does.
    """
    assert get_cached_response(question) is not None


def test_negation_interposed_in_a_phrase_breaks_the_substring():
    """Characterisation, not a defect test: 'no' inside a phrase breaks the match.

    This is why the phrase fast path needs no negation guard. Inserting the
    negation between two words of a multi-word phrase breaks contiguity, so
    the phrase cannot match. The residual exposure is limited to the two kept
    keywords, which are contextually specific.
    """
    assert get_cached_response("¿Por qué no dejaste Mercadona?") is None


# ─── Corpus consistency: the cache is a derived store ─────────
#
# The candidate's own corpus is the single source of truth for any factual
# claim; this cache is a derived store that loses every disagreement, so a
# divergence is a wrong answer in a real interview.
#
# THE REAL WIKI IS THE ONLY SUBJECT, AND IT IS PRESENT
# ----------------------------------------------------
# This section used to say the repository's `wiki/` "is excluded by .gitignore,
# is backed up to a private repository, and is therefore absent from a clean
# clone", and ran the checks against `tests/fixtures/retrieval_corpus/`
# instead. All of that was false: 46 wiki files are tracked, in `origin/main`,
# and `actions/checkout` brings them to every CI run. The stand-in was a
# structural clone of the real wiki, so these guards were reading a copy of the
# thing they were supposed to be checking.
#
# A wiki-consistency check with no wiki still cannot run, and "cannot run" is
# not "passed", so the invariant stays extracted into ``_assert_cache_does_not_
# overstate`` / ``_assert_cache_does_not_understate`` and the controls below
# drive it two ways against the ONE corpus:
#
#   * against the REAL wiki, with the production cache — the candidate's own
#     consistency check, unchanged, skipped (not weakened) where there is no
#     wiki to check against;
#   * against a synthetic cache injected into the same corpus — so the CHECKER
#     itself is under test, not just the answers. Without this, a corpus edit
#     that stopped mentioning a database would turn the understated control
#     into a no-op and nobody would find out until an interviewer did.
#
# The stated cost of deleting the stand-in: on a checkout with no wiki at all,
# these controls skip instead of running against a substitute. That is the
# correct trade — a substitute made these guards capable of passing while
# measuring the wrong corpus, which is strictly worse than not running.

WIKI_DIR = Path(__file__).resolve().parents[1] / "wiki"

#: The repository root, for the deploy-mechanism evidence globs below. Separate
#: from WIKI_DIR because they answer different questions: WIKI_DIR is the
#: candidate's own content (tracked, and skipped only if removed) while the
#: DEPLOYMENT ARTIFACTS are a different, also-committed, set whose scan is what
#: makes the mechanism check runnable in CI.
REPO_ROOT = Path(__file__).resolve().parents[1]


#: Counterexamples the corpus-wide scan must REJECT. Recalibrated against the
#: real ``wiki/``.
#:
#: The ``areas`` counterexample changed because the corpus did. The invented
#: stand-in these were originally written against never mentioned AI, so
#: "integracion de la inteligencia artificial" was ungrounded there and made a
#: clean negative control. The real corpus attributes ``ia``, ``inteligencia``
#: and ``artificial`` across many pages, so that answer is now GROUNDED by a
#: union scan and the control proved nothing. It was replaced with a claim on
#: the three area terms the real corpus genuinely never mentions -- ``nube``,
#: ``qa`` and ``movilidad`` -- which keeps the control a control.
#:
#: That substitution is a small illustration of the limit documented above: a
#: union scan catches "this term is nowhere in the corpus", which is a weaker
#: claim than "this answer misstates the page that answers its question". The
#: instrument for the second is
#: ``test_the_preferred_area_answer_is_not_left_claiming_more_than_its_page``,
#: and it is untouched by any of this.
_OVERSTATED_SUBJECTS: dict[str, str] = {
    "databases": "Trabajo con PostgreSQL y SQLite.",
    "areas": "Trabajo en QA y con una nube propia.",
}


# ── The vocabularies a cached answer may not outrun the wiki on ──────────────
#
# Each entry is a SCANNER, not an allowlist of approved claims: a term the
# corpus never mentions fails, and a term the corpus adds passes without
# touching the test. That is what keeps the assertion maintainable, and it is
# why the corpus is scanned rather than quoted.
#
# There are two, and there used to be one. The database scan was added after an
# answer claimed "MySQL, PostgreSQL y SQLite" while the corpus attributed
# MySQL, PostgreSQL and MongoDB. The area scan is here because the preferred-area
# answer claimed "backend, datos e integracion de la inteligencia artificial"
# and no page supports that claim -- the page that answers that question names
# backend and data, twice, and enumerates frontend, DevOps and backend as the
# areas considered.
#
# Deliberately NOT here, and this is the limit of a corpus-wide scan: a term the
# corpus mentions SOMEWHERE ELSE still passes. The shipped claim would pass a
# union scan whenever the candidate's pages mention AI at all, which they do. A
# union scan catches "this area is nowhere in the wiki"; it does not catch "this
# area is not what the wiki says about THIS question". Catching the second needs
# a question-to-page binding, and inventing one here -- declaring by hand which
# page supports which answer, on a machine that cannot read the pages -- would
# manufacture exactly the kind of unsupported claim this file exists to catch.
_VOCABULARIES: dict[str, frozenset[str]] = {
    "databases": frozenset({
        "cassandra", "cockroach", "db2", "dynamodb", "elasticsearch", "firebird",
        "mariadb", "mongo", "mongodb", "mssql", "mysql", "oracle", "postgres",
        "postgresql", "redis", "sqlite", "sqlalchemy", "sqlserver",
    }),
    "areas": frozenset({
        "backend", "frontend", "devops", "datos", "web", "nube", "movil",
        "infraestructura", "redes", "seguridad", "qa", "inteligencia",
        "artificial", "ia", "movilidad",
    }),
}


def _cached_answers() -> list[str]:
    """Every answer currently configured in the service.

    Resolved through the service module rather than the ``from ... import``
    binding at the top of this file, so it reports what the service will
    actually serve. The import-time binding is a snapshot: after a test
    monkeypatches the service's table, a helper reading the snapshot would
    scan the old table and report "clean" about a cache that is not there.
    """
    from backend.services import response_cache

    return [entry["answer"] for entry in response_cache._CACHED_QUESTIONS]


def _normalised_text(text: str) -> str:
    """Lowercased and de-accented, with punctuation left in place.

    Kept separate from :func:`_normalised_tokens` because the two consumers
    need different things: the vocabulary scans want a SET of words, while the
    deploy-mechanism scan splits on sentence punctuation before matching. A
    single tokenising helper would force one of the two to be wrong.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _normalised_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", _normalised_text(text)))


def _terms_in(answer: str, vocabulary) -> set[str]:
    """Every term of ``vocabulary`` a piece of text claims.

    ``vocabulary`` is either one of the registered sets or its NAME. Accepting
    the name is a convenience with a trap on the other side: ``set("areas")`` is
    ``{'a','r','e','s'}``, so passing the name where a set was expected does not
    fail -- it silently degrades the scan into matching single letters and then
    reports the check as clean. An unknown name raises instead.
    """
    if isinstance(vocabulary, str):
        if vocabulary not in _VOCABULARIES:
            raise AssertionError(
                f"unknown vocabulary {vocabulary!r}; known: "
                f"{sorted(_VOCABULARIES)}"
            )
        vocabulary = _VOCABULARIES[vocabulary]
    return _normalised_tokens(answer) & set(vocabulary)


def _corpus_vocabulary(vocabulary: frozenset[str], documents: dict[str, str]) -> set[str]:
    """Every term of ``vocabulary`` the corpus attributes, across ALL its pages.

    The union over every page, not a hand-picked one. Reading a single page is
    how the original check missed a database the corpus did attribute on
    another page: the candidate really does use Redis, it is just not in the
    profile's summary line.
    """
    attributed: set[str] = set()
    for text in documents.values():
        attributed |= _terms_in(text, vocabulary)
    return attributed


def _fixture_documents() -> dict[str, str]:
    """The corpus, loaded through the production loader.

    Loaded rather than ``rglob``-ed so the loader's own skip list applies: an
    index page or a template must not be able to attribute a technology to the
    candidate.

    THE SUBJECT USED TO BE A STAND-IN. This section ran the same invariant
    against two subjects: the real ``wiki/``, skipped where it was absent, and
    ``tests/fixtures/retrieval_corpus/``, which it claimed to run against
    "unconditionally" because the real wiki "is gitignored and private". It was
    neither: 46 wiki files are tracked, in ``origin/main``, and checked out by
    CI. The stand-in was a structural clone of this very wiki, so these controls
    were capable of passing while measuring the wrong pages. It is deleted; the
    controls below run against the real corpus, and skip only where the corpus
    is genuinely absent.
    """
    from tests.real_wiki import load_documents

    return load_documents()


#: Vocabularies for which the corpus-wide overstatement control can be built:
#: those with at least one term the real corpus does not attribute.
#:
#: Derived, not hard-coded, so that widening a vocabulary or editing the corpus
#: RE-OPENS the control instead of leaving it silently vacuous. The first
#: version of this was hard-coded and a corpus edit had already made the
#: ``areas`` counterexample grounded -- which the control correctly reported as
#: "proves nothing". Deriving it means the next such edit is a visible,
#: automatic change rather than a control that quietly stopped testing.
def _corpus_wide_vocabularies() -> tuple[str, ...]:
    return tuple(sorted(
        name
        for name, terms in _VOCABULARIES.items()
        if set(terms) - _corpus_vocabulary(terms, _fixture_documents())
    ))


def _real_wiki_documents() -> dict[str, str]:
    """The candidate's real pages, through the production loader."""
    return _fixture_documents()


def _wiki_text(relative_path: str) -> str:
    return (WIKI_DIR / relative_path).read_text(encoding="utf-8")


def _databases_listed_in(text: str, marker: str) -> set[str]:
    for line in text.splitlines():
        if marker in line:
            listed = line.split(marker, 1)[1]
            return {name.strip().lower() for name in listed.split(",") if name.strip()}
    raise AssertionError(f"no {marker!r} line found")


def _databases_in(answer: str) -> set[str]:
    return _terms_in(answer, _VOCABULARIES["databases"])


def _assert_cache_does_not_overstate(answer: str, corpus_databases: set[str]) -> None:
    """Nothing in the spoken answer that the corpus does not attribute."""
    named = _databases_in(answer)
    assert named, f"no database name detected in the cached answer: {answer!r}"
    assert named <= corpus_databases, (
        f"cache names databases the corpus does not list: "
        f"{sorted(named - corpus_databases)}; corpus lists {sorted(corpus_databases)}"
    )


def _assert_cache_does_not_understate(answer: str, corpus_databases: set[str]) -> None:
    """Every database the corpus attributes is actually spoken.

    Omission is how "MySQL, PostgreSQL y SQLite" hid the fact that MongoDB is
    the third database the corpus actually attributes.
    """
    for database in corpus_databases:
        assert database in answer.lower(), (
            f"{database} is in the corpus but not the answer"
        )


def _real_wiki_databases() -> set[str]:
    """Databases the real wiki attributes, from the profile summary.

    wiki/profile/mikel.md, "## Top skills (summary)" -> "**Databases:** ...".
    """
    return _databases_listed_in(_wiki_text("profile/mikel.md"), "**Databases:**")


def _fixture_databases() -> set[str]:
    """Databases the corpus attributes, across all its pages.

    Measured, not quoted: the corpus names ``postgres`` and ``postgresql`` both,
    and only some of its pages mention Redis -- none of them the profile summary
    the original check read. That gap is what the union read exists to cover.
    """
    databases = _corpus_vocabulary(
        _VOCABULARIES["databases"], _fixture_documents()
    )
    assert databases, "the corpus must attribute at least one database"
    return databases


def _scan_offenders(
    answers: list[str],
    attributed: dict[str, set[str]],
    vocabularies: dict[str, frozenset[str]] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Answers naming, per vocabulary, terms the corpus does not attribute.

    One function, used by every scan in this file. That is deliberate: the
    defect this section corrects was partly that the check lived beside one
    hand-picked answer, so there was exactly one thing to get wrong and exactly
    one place to extend.

    ``vocabularies`` is a parameter rather than a closed-over lookup at
    ``_VOCABULARIES`` because the two are no longer the same set: the claim
    scanner below registers its own vocabularies, and a helper that resolved
    names only against ``_VOCABULARIES`` raised ``KeyError`` on those instead of
    scanning them. A helper whose signature says "per vocabulary" while reading
    a different one is a helper that will quietly under-scan the day a second
    vocabulary is added -- which is the class of failure this file exists to
    prevent, one level down.
    """
    if vocabularies is None:
        vocabularies = _VOCABULARIES
    offenders: dict[str, dict[str, list[str]]] = {}
    for answer in answers:
        for name, terms in attributed.items():
            ungrounded = sorted(_terms_in(answer, vocabularies[name]) - terms)
            if ungrounded:
                offenders.setdefault(answer, {})[name] = ungrounded
    return offenders


# The two real-wiki checks, marked not deleted. The corpus is 46 tracked files,
# so they run here and in CI; they are skipped, not weakened, only where the wiki
# is genuinely absent. ``test_the_consistency_checker_itself_works`` below is
# the reason skipping is safe.
needs_real_wiki = pytest.mark.skipif(
    not WIKI_DIR.is_dir(),
    reason=(
        "the candidate's real wiki/ is absent here, so there is nothing to "
        "check the production response cache against. The 46 wiki files are "
        "tracked, so this only happens on a checkout where the corpus was "
        "removed."
    ),
)


@needs_real_wiki
def test_wiki_lists_the_databases_the_cache_answers_with():
    """The database answer names only databases the real wiki attributes.

    Regression: the answer claimed "MySQL, PostgreSQL y SQLite" while the wiki
    lists MySQL, PostgreSQL and MongoDB (wiki/profile/mikel.md,
    wiki/skills/data.md) and no SQLite anywhere.
    """
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None
    _assert_cache_does_not_overstate(answer, _real_wiki_databases())


@needs_real_wiki
def test_cache_answer_names_every_database_the_wiki_lists():
    """The spoken answer covers the whole wiki claim, so it cannot understate it."""
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None
    _assert_cache_does_not_understate(answer, _real_wiki_databases())


def test_cache_never_claims_sqlite(monkeypatch):
    """SQLite appears nowhere in the corpus, so it must appear nowhere in the cache.

    Checked against the production cache AND against a synthetic one built from
    the same real corpus, so the SCANNER is under test and not only the answers.
    """
    offenders = [
        answer for answer in _cached_answers() if "sqlite" in answer.lower()
    ]
    assert not offenders, f"SQLite claimed by the cache, unsupported by the corpus: {offenders}"

    synthetic = [
        {"answer": "Trabajo con MySQL y SQLite en proyectos pequenos.",
         "keywords": ["sqlite"], "phrases": ["trabajo con mysql"]}
    ]
    monkeypatch.setattr(
        "backend.services.response_cache._CACHED_QUESTIONS", synthetic
    )
    assert get_cached_response("¿Y SQLite?") is not None, (
        "the synthetic cache must be reachable, or the SQLite scan below is "
        "scanning nothing"
    )
    offenders = [
        answer for answer in _cached_answers() if "sqlite" in answer.lower()
    ]
    assert offenders, (
        "a cache entry naming SQLite must be detected by the scan; the scan "
        "is not looking where it thinks it is"
    )


def test_pitch_matches_the_wiki_presentation():
    """The pitch must not describe the pre-DAM job as 'encargado de supermercado'.

    The wiki supersedes it: wiki/faq/presentacion-30-segundos.md (2026-08-28)
    replaced that phrasing with "empecé como frutero, progresé a encargado y
    terminó como gerente en Mercadona liderando equipos de ~50 personas", and
    wiki/profile/mikel.md records Gerente B, Mercadona, 2019-Nov 2025.

    This one is NOT a wiki-consistency test: it asserts against text baked into
    the production cache itself, so it has no external subject and needs no
    wiki. It is left reading the real cache on purpose — a synthetic cache here
    would only prove that a string contains another string.
    """
    answer = get_cached_response("Cuéntame sobre ti")
    assert answer is not None
    assert "encargado de supermercado" not in answer.lower()
    assert "gerente" in answer.lower()


# ─── Coverage: is the guard actually pointed at the answers? ────────────────
#
# The defect this section exists to correct is not only that a claim was wrong.
# It is that 21 of 22 cached answers had NO wiki check at all, and the one that
# did was hand-picked -- so a future answer that overstated something was
# invisible by construction, and the check that existed never ran against the
# corpus it was about.
#
# So the two questions are separated and both are asked here:
#
#   1. DOES THE SCAN REACH THE ANSWERS?  (hermetic, always runs)
#   2. ARE THE CLAIMS WITHIN THE CORPUS?  (against the real wiki)


def test_an_unknown_vocabulary_name_raises_rather_than_matching_letters():
    """The trap on the convenience in ``_terms_in``, pinned.

    ``set("areas")`` is ``{'a','r','e','s'}``. A name passed where a set was
    expected therefore does not fail -- it matches single letters, and the scan
    reports itself clean. That is how the area check below first "passed"
    against the exact answer it was written to reject.
    """
    with pytest.raises(AssertionError) as caught:
        _terms_in("backend o datos", "areas-of-practice")
    assert "areas-of-practice" in str(caught.value)

    assert _terms_in("backend o datos", "areas") == {"backend", "datos"}


def test_the_scan_reaches_the_answers_it_must():
    """Question 1, and the one that runs everywhere.

    The scan is generic over the table and over the vocabularies -- it is not a
    hand-picked entry with a hand-picked vocabulary attached. So this asserts
    reach: every vocabulary reaches at least one shipped answer, and the areas
    vocabulary -- the one the defect was about -- reaches several.

    The databases vocabulary reaching exactly ONE entry is the honest number,
    not a failure: that is the whole databases surface of the cache today. The
    test that protects the NEXT database entry is the generic one below.
    """
    answers = _cached_answers()
    reached = {
        name: [a for a in answers if _terms_in(a, vocabulary)]
        for name, vocabulary in _VOCABULARIES.items()
    }
    for name, hit in reached.items():
        assert hit, (
            f"the {name} scan reaches none of the {len(answers)} cached "
            "answers, so it is pointed at nothing"
        )
    assert len(reached["areas"]) >= 3, (
        "the areas scan reaches only "
        f"{len(reached['areas'])} answers; a scan that reads one hand-picked "
        "entry cannot catch a future entry that overstates something, which is "
        f"exactly how this defect shipped. Reached: {reached['areas']}"
    )


@pytest.mark.parametrize("vocabulary", _corpus_wide_vocabularies())
def test_a_new_entry_that_overstates_something_is_caught_without_being_registered(
    monkeypatch, vocabulary
):
    """The property that would have caught this defect, proved on its own.

    Nobody adds a test when they add a cache entry -- that is the whole shape of
    the failure: 21 of 22 answers had no check because checks were attached to
    entries by hand. So the scan must find an offending entry that was never
    registered anywhere, and this injects exactly that.
    """
    attributed = _corpus_vocabulary(_VOCABULARIES[vocabulary], _fixture_documents())
    ungrounded = _OVERSTATED_SUBJECTS[vocabulary]
    assert _terms_in(ungrounded, _VOCABULARIES[vocabulary]) - attributed, (
        f"the injected answer is grounded in the real corpus, so this control "
        f"proves nothing. The corpus attributes "
        f"{sorted(attributed)}; pick a {_OVERSTATED_SUBJECTS[vocabulary]!r} that "
        f"it does not."
    )

    monkeypatch.setattr(
        "backend.services.response_cache._CACHED_QUESTIONS",
        [{"answer": ungrounded, "phrases": ["x"], "keywords": []}],
    )
    offenders = _scan_offenders(
        _cached_answers(), {vocabulary: attributed}, _VOCABULARIES
    )
    assert offenders, (
        f"a brand new cache entry claiming something the corpus does not "
        f"attribute was not caught by the {vocabulary} scan. The scan is "
        "pointed at named entries rather than at the table."
    )


def test_the_scan_reaches_the_answer_that_shipped_the_unsupported_claim():
    """The specific answer the defect names must be inside the scan's reach.

    Named rather than counted, so that "the scan covers things" cannot be
    satisfied by a scan that happens to cover everything EXCEPT the entry that
    was wrong.
    """
    answer = get_cached_response("¿Qué área del desarrollo te gusta más?")
    assert answer is not None
    claimed = _terms_in(answer, _VOCABULARIES["areas"])
    assert "backend" in claimed, (
        "the preferred-area answer no longer names an area at all, so this "
        f"test is no longer looking at the answer it was written for: {answer!r}"
    )


# ─── The class: an answer may not claim a deploy mechanism this repo lacks ────
#
# THE DEFECT, AND WHY A CHECK BESIDE ONE ANSWER CANNOT CATCH IT
# ----------------------------------------------------------
# This file has already caught one unsupported cached claim (a1ddb08 removed
# "integración de la inteligencia artificial" from the preferred-area answer).
# The same defect then shipped a second time, in the Docker answer, with the
# same shape: a pre-generated answer asserted an infrastructure mechanism that
# no file in this repository supports.
#
# Two properties of that shape defeat the check that existed:
#
#   1. THE CACHE IS CONSULTED BEFORE RETRIEVAL. A cached answer is returned
#      verbatim and never reaches RAG, so no amount of grounding work can see
#      it. The wiki-consistency scans above run against the table, but only for
#      the `databases` and `areas` vocabularies -- and both are hand-registered.
#   2. NOBODY ADDS A TEST WHEN THEY ADD A CACHE ENTRY. Every check here is
#      written after the fact, by hand, for one named answer. An entry added
#      tomorrow is invisible by construction.
#
# So the invariant is a property of the WHOLE TABLE, checked against the
# repository itself rather than against the candidate's wiki: a deploy
# mechanism named by a cached answer must either be demonstrable in this
# repository, or be disclaimed in the same sentence.
#
# WHY THE SENTENCE, AND WHY A DISCLAIMER IS ALLOWED
# -------------------------------------------------
# "I do not use Docker" and "I deploy with Docker" share a vocabulary, so a
# vocabulary scan cannot tell them apart -- and the truthful answer to a
# Docker question has to be allowed to say the word. Hence the sentence scope
# and the disclaimer: the mechanism term must be present in a sentence that
# also disclaims it.
#
# The disclaimed case used to be illustrated by this repository's own Docker
# answer ("En InterviewTTS no hay contenedores", "Docker no lo uso en este
# proyecto"). It cannot be any more, and not because the disclaimer stopped
# working: this repository ships a root Dockerfile and docker-compose.yml
# since 2026-10-03, so denying containers here is no longer true, and the
# container entries are skipped as evidence-backed before the disclaimer is
# consulted. The shipped answer now says containers are for local development
# only, which is what the two artifacts support, and the disclaimer's live
# examples moved to Kubernetes, which the repository still does not ship.
#
# KNOWN LIMITS OF A LEXICAL CHECK, RECORDED RATHER THAN HIDDEN
# ------------------------------------------------------------
#   * An answer that denies and asserts in the SAME sentence passes, e.g. "no
#     uso Docker en local, en producción docker-compose". Sentence granularity
#     is the price of not needing a parser, and the cache has no dependencies.
#   * The disclaimer vocabulary is hand-declared. It is short and each entry
#     earns its place; a form of refusal it does not list reads as an
#     assertion, which fails toward red, not toward a silent pass.
#   * This adjudicates REPOSITORY contradictions only. Whether the candidate
#     has used a tool elsewhere is biography, which no file here can settle --
#     the `needs_real_wiki` checks above are the instrument for that, and they
#     cannot run in CI. What this catches is the category that actually
#     shipped: a claim about how THIS project is deployed.

#: File-name patterns that would demonstrate a container build exists anywhere
#: in the repository. Matched against bare file names by ``_repo_has_artifact``,
#: which prunes as it walks.
#:
#: SINCE 2026-10-03 THIS SET MATCHES. The repository root ships a ``Dockerfile``
#: and a ``docker-compose.yml`` for local, containerised runs, so the container
#: mechanisms in ``_MECHANISM_CLAIMS`` are evidence-backed and naming them in a
#: cached answer is no longer a defect. The globs stay because they are what
#: makes that true: the scan reads the filesystem, so the answer and the
#: repository are checked against each other rather than against a constant
#: somebody has to remember to update.
_CONTAINER_ARTIFACTS = (
    "Dockerfile",
    "Dockerfile.*",
    "*.dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

#: File-name patterns that would demonstrate an orchestrator manifest exists.
_ORCHESTRATOR_ARTIFACTS = (
    "kustomization.yaml",
    "kustomization.yml",
    "Chart.yaml",
    "*-deployment.yaml",
    "*-deployment.yml",
    "deployment.yaml",
    "deployment.yml",
)

_SKIP_DIRS = frozenset({"venv", "node_modules", ".git", ".codegraph", "candidate", "data"})

#: The mechanisms a cached answer may name, each with the pattern that detects
#: it and the artifacts that would prove this repository actually ships it.
#: A scanner, not an allowlist of approved answers: adding an artifact makes
#: the mechanism shippable, and adding an entry that names a mechanism the
#: repository lacks is what turns the suite red.
_MECHANISM_CLAIMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("docker", r"\bdockers?\b", _CONTAINER_ARTIFACTS),
    ("contenedores", r"\bcontenedores?\b", _CONTAINER_ARTIFACTS),
    ("compose", r"\bcompose\b", _CONTAINER_ARTIFACTS),
    ("podman", r"\bpodman\b", _CONTAINER_ARTIFACTS),
    ("kubernetes", r"\bkubernetes\b|\bk8s\b", _ORCHESTRATOR_ARTIFACTS),
)

#: A sentence matching this disclaims the mechanism it names.
#:
#: A regex rather than a list of literal substrings because Spanish inserts a
#: clitic between the negation and the verb: the shipped answer says "Docker
#: NO LO USO", not "no uso". A literal list missed that, which the positive
#: control below caught on its first run. The optional group absorbs the
#: clitic without widening the rule to any sentence containing "no".
_DISCLAIMER_RE = re.compile(
    r"\bno\s+(?:lo\s+|la\s+|le\s+|los\s+|las\s+)?"
    r"(hay|uso|utilizo|tengo|manejo|empleo|llevo)\b"
    r"|\bsin\s+contenedores\b"
)

_SENTENCE_SPLIT_RE = re.compile(r"[.:;!?]+")


def _repo_has_artifact(globs: tuple[str, ...]) -> bool:
    """Whether the repository contains a file matching any of ``globs``.

    An ``os.walk`` with in-place pruning rather than ``Path.glob("**/...")``.
    Two reasons, and the second is the important one: a recursive glob walks
    ``venv/`` and ``node_modules/`` in full before the caller can filter them
    out, which is minutes of I/O on this repository; and pruning at the
    directory level is what actually makes ``_SKIP_DIRS`` mean anything.
    """
    patterns = [re.compile(fnmatch.translate(glob)) for glob in globs]
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if any(p.match(filename) for p in patterns):
                return True
    return False


def _is_disclaimed(sentence: str) -> bool:
    return _DISCLAIMER_RE.search(sentence) is not None


def _mechanism_offenders(answer: str) -> dict[str, list[str]]:
    """Mechanisms this answer claims that the repository does not ship.

    Generic over the whole table and over the vocabulary, so an entry nobody
    registered is still checked. Keys are mechanism names, values the
    sentences that make the claim.
    """
    offenders: dict[str, list[str]] = {}
    for name, pattern, artifacts in _MECHANISM_CLAIMS:
        if _repo_has_artifact(artifacts):
            continue  # the repository ships it, so naming it is not a defect
        claims = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT_RE.split(_normalised_text(answer))
            if re.search(pattern, sentence) and not _is_disclaimed(sentence)
        ]
        if claims:
            offenders[name] = claims
    return offenders


def test_no_cached_answer_claims_a_deploy_mechanism_the_repository_lacks():
    """The shipped defect, as a property of every entry rather than one of them.

    A cached answer is spoken verbatim to an interviewer and never reaches
    RAG, so an infrastructure claim nothing in this repository supports is the
    candidate being asked to defend something that does not exist. The Docker
    answer did exactly that; this fails if any answer -- present or future, and
    named or not -- does it again.
    """
    offenders = {
        answer: found
        for answer in _cached_answers()
        if (found := _mechanism_offenders(answer))
    }
    assert not offenders, (
        f"cached answers claiming a deploy mechanism this repository does not "
        f"ship: {offenders}. Either the answer describes a deployment that does "
        "not exist here, or the answer disclaims the mechanism in the same "
        "sentence -- which is how an answer about Kubernetes has to say it."
    )


def test_the_mechanism_scan_detects_an_unshipped_mechanism_claim(monkeypatch):
    """The negative control, on the exact string that shipped.

    A guard that has never been seen to reject the thing it was written for is
    a guard nobody can trust, and this one is a lexical scan that could quietly
    stop matching -- a renamed vocabulary entry, a stripped disclaimer list.
    So a real sentence is injected and must be rejected by the real scan.

    WHY IT NO LONGER INJECTS THE DOCKER SENTENCE. It used to, and it stopped
    rejecting that sentence for a reason that has nothing to do with the scan:
    the root Dockerfile and docker-compose.yml arrived on 2026-10-03, so
    ``_mechanism_offenders`` now skips the container mechanisms as
    evidence-backed (the ``continue`` at :func:`_mechanism_offenders`). The
    historical Docker claim is therefore ACCEPTED by the scan today, correctly
    -- this repository ships containers. A negative control pointed at a
    mechanism the repository now genuinely ships asserts nothing and fails for
    a reason that has nothing to do with the code under test.

    So the control is repointed at Kubernetes, which the repository still does
    not ship: there is no kustomization.yaml, no Chart.yaml and no
    *-deployment.yaml anywhere in it. The control keeps its teeth for the same
    reason it had them -- a claim the repository cannot back must be rejected.
    Only the example moved, and it moved because the evidence moved.
    """
    shipped = (
        "Despliego InterviewTTS con Kubernetes en un VPS: un Deployment para el "
        "backend, un Service que lo expone y un ConfigMap con la configuración. "
        "Ya lo tengo en un clúster de tres nodos."
    )
    monkeypatch.setattr(
        "backend.services.response_cache._CACHED_QUESTIONS",
        [{"answer": shipped, "phrases": ["x"], "keywords": []}],
    )
    offenders = _mechanism_offenders(_cached_answers()[0])
    assert offenders, (
        "the scan no longer rejects a claim about a mechanism this repository "
        "does not ship, so it is not pointed at the defect it was written for"
    )
    assert "kubernetes" in offenders, (
        f"the scan missed 'kubernetes' in the injected claim; it now reports "
        f"{sorted(offenders)}"
    )


def test_the_mechanism_scan_accepts_a_disclaimed_mechanism():
    """The positive control: denying a mechanism the repo lacks is the fix.

    Without this the guard would be "never say the word Docker", which would
    make the honest answer to "¿Qué experiencia tienes con Docker?" impossible
    to give — the recruiter asked, and silence is its own failure.

    WHY THE EXAMPLES ARE KUBERNETES NOW. The three container examples this
    used to assert ("Docker no lo uso en este proyecto", "En InterviewTTS no
    hay contenedores") still return ``{}``, but they stopped testing the
    DISCLAIMER: since the root Dockerfile and docker-compose.yml arrived on
    2026-10-03, the container entries of ``_MECHANISM_CLAIMS`` are skipped by
    the evidence check in ``_mechanism_offenders`` before the disclaimer is
    ever consulted. The assertions passed for the wrong reason -- a control
    that can no longer fail is not a control -- and one of them is now also
    describing a deployment this repository does not have.

    Kubernetes restores the mechanism under test. The repository ships no
    orchestrator manifest, so ``_mechanism_offenders`` really does reach the
    disclaimer branch for these sentences, and the clitic forms ("no uso",
    "no hay", "no tengo") really are what ``_DISCLAIMER_RE`` has to absorb.
    """
    assert _mechanism_offenders("En este proyecto no uso Kubernetes.") == {}
    assert _mechanism_offenders("No hay Kubernetes en InterviewTTS.") == {}
    assert _mechanism_offenders("No tengo Kubernetes.") == {}

    # And the disclaimed case must still be ACCEPTED for the reason it was
    # always accepted for -- the disclaimer -- rather than merely because the
    # sentence names nothing. Removing the denial has to turn it red, or the
    # three assertions above prove nothing about the disclaimer.
    assert "kubernetes" in _mechanism_offenders("En este proyecto uso Kubernetes.")


def test_the_mechanism_scan_accepts_a_mechanism_the_repository_ships():
    """Naming a mechanism this repository really ships is not a defect.

    The scan is evidence-bound in both directions. This asserts the other
    direction explicitly, and asserts the evidence exists — so if the systemd
    unit were ever deleted, this fails and says the evidence moved, rather than
    the guard quietly becoming narrower.

    WHY THE CONTAINER ASSERTION IS INVERTED. It used to require
    ``_repo_has_artifact(_CONTAINER_ARTIFACTS) is False`` — no Dockerfile, so a
    Docker claim is a fabrication, so the scan must reject it. On 2026-10-03
    this repository gained a root Dockerfile and docker-compose.yml. The
    assertion was not wrong when it was written; its premise expired. Asserting
    ``is False`` now means asserting that a file the user can read at the
    repository root does not exist, and it would fail forever unless somebody
    deleted the Dockerfile.

    The guard is still tied to the evidence, in both directions, which is what
    the original message asked for. What changed is WHICH evidence exists. So
    the container branch now asserts ``is True`` and then proves the scan
    really accepts a Docker claim because of that evidence — not because the
    vocabulary stopped matching.
    """
    for artifacts in (("*.service",), ("*.conf",)):
        assert _repo_has_artifact(artifacts), f"no artifact matches {artifacts}"
    assert _repo_has_artifact(_CONTAINER_ARTIFACTS) is True, (
        "this repository no longer ships a container artifact, so the "
        "evidence-bound branch for containers is untested; recalibrate rather "
        "than let a shipped Docker claim be rejected again"
    )
    offenders = _mechanism_offenders(
        "Lo despliego con systemd y nginx delante haciendo de proxy inverso."
    )
    assert offenders == {}, f"a shipped mechanism was reported as ungrounded: {offenders}"

    docker_claim = "En InterviewTTS uso contenedores para el desarrollo local."
    assert _mechanism_offenders(docker_claim) == {}, (
        "the repository ships a Dockerfile and docker-compose.yml, so a Docker "
        f"claim must be accepted; it was reported as ungrounded: {offenders}"
    )


def test_the_mechanism_scan_is_not_pointed_at_nothing():
    """Guard the guard: the vocabulary must be able to fire.

    A scanner whose patterns match no shipped answer, and whose evidence
    globs match no file, would pass every test above while checking nothing.
    This is the "does the scan reach" question from the section above, asked
    of the deployment vocabulary.

    WHY THE SECOND HALF IS INVERTED. It used to assert that the container globs
    match NOTHING, so that the Docker negative control could not pass for the
    wrong reason. That is no longer the wrong reason to guard against and no
    longer the truth: the root Dockerfile and docker-compose.yml have shipped
    since 2026-10-03, so the container patterns DO match, and the control that
    needed them to come up empty is now the Kubernetes one above.

    Reach is still asserted, in both directions, and the kubernetes half is
    untouched because the repository still ships no orchestrator manifest —
    that half is the one that has to keep failing when a claim is invented.
    """
    offenders = _mechanism_offenders("Despliego con Kubernetes en el VPS.")
    assert "kubernetes" in offenders, (
        f"the vocabulary cannot detect a claim it names: {offenders}"
    )
    # The container globs now match, so the container vocabulary is reachable
    # and its evidence-bound branch is the one under test next door.
    assert _repo_has_artifact(_CONTAINER_ARTIFACTS), (
        "this repository no longer ships a container artifact; the Docker "
        "claim is a fabrication again, so recalibrate the guard rather than "
        "let the evidence-bound branch pass for the wrong reason"
    )
    assert not _repo_has_artifact(_ORCHESTRATOR_ARTIFACTS), (
        "this repository now ships an orchestrator manifest, so the "
        "kubernetes negative control is passing for the wrong reason; "
        "recalibrate the guard rather than let it go toothless"
    )


# The question, and the page in the committed corpus that answers it. A corpus
# union is the wrong instrument for a question-scoped claim -- the candidate's
# pages mention AI all over the place, so "mentioned somewhere" cannot tell an
# AI preference from an AI project. What can is the page that answers THIS
# question.
_AREA_QUESTION = "que area del desarrollo te gusta mas"
_AREA_PAGE = "faq/area-preferida.md"


def _attributed_by(path, vocabulary: str) -> set[str]:
    return _terms_in(path.read_text(encoding="utf-8"), _VOCABULARIES[vocabulary])


def test_the_preferred_area_answer_is_not_left_claiming_more_than_its_page():
    """The shipped defect, as a question-scoped check, hermetically.

    The answer used to claim "backend, datos e integracion de la inteligencia
    artificial". The page that answers this question attributes backend, data
    and frontend -- and no AI at all. An answer adding a fourth area is
    asserting something no page supports, and an interviewer is the last person
    who should find out.

    The subject is the real ``wiki/faq/area-preferida.md`` -- the candidate's own
    page for this question. What is under test is the RULE: an answer may not
    name an area that the page answering its question does not attribute.
    """
    attributed = _attributed_by(WIKI_DIR / _AREA_PAGE, "areas")
    assert attributed, (
        f"{_AREA_PAGE} attributes no areas at all, so this check is pointed at "
        "nothing; re-read the page and re-calibrate"
    )

    answer = get_cached_response("¿Qué área del desarrollo te gusta más?")
    assert answer is not None
    claimed = _terms_in(answer, "areas")
    assert claimed, f"the answer names no area at all: {answer!r}"
    assert claimed <= attributed, (
        f"the cached preferred-area answer claims "
        f"{sorted(claimed - attributed)}, which {_AREA_PAGE} -- the page that "
        f"answers '{_AREA_QUESTION}' -- does not support. It attributes "
        f"{sorted(attributed)}."
    )


@needs_real_wiki
def test_the_preferred_area_answer_matches_the_real_wiki_page():
    """The same rule, against the candidate's own page for this question."""
    answer = get_cached_response("¿Qué área del desarrollo te gusta más?")
    assert answer is not None
    attributed = _attributed_by(WIKI_DIR / "faq" / "area-preferida.md", "areas")
    claimed = _terms_in(answer, "areas")
    assert claimed <= attributed, (
        f"the cached preferred-area answer claims {sorted(claimed - attributed)} "
        "and wiki/faq/area-preferida.md supports only "
        f"{sorted(attributed)}"
    )


@needs_real_wiki
def test_no_cached_answer_names_an_area_the_wiki_does_not_attribute():
    """Question 2 for the areas, over EVERY answer rather than one entry."""
    attributed = _corpus_vocabulary(
        _VOCABULARIES["areas"], _real_wiki_documents()
    )
    assert attributed, "the wiki attributes no areas at all; the scan is inert"

    offenders = _scan_offenders(
        _cached_answers(), {"areas": attributed}, _VOCABULARIES
    )
    assert not offenders, (
        f"cached answers naming areas the wiki never attributes: {offenders}"
    )


# ─── The checker itself, against a corpus CI always has ─────────────────────
#
# Two subjects, both unconditional, both against the committed fixture corpus.
# A consistency guard that only runs where the subject happens to exist is a
# guard nobody can trust, and the failure mode is invisible: delete the wiki and
# the assertions quietly become dead code that still looks like coverage.
#
# The subjects are drawn FROM the corpus rather than invented, so a faithful
# answer exists to be accepted -- and each guarded vocabulary has one, so
# adding a vocabulary without a subject fails here instead of silently never
# being checked.

_FIXTURE_SUBJECTS: dict[str, str] = {
    "databases": "Trabajo con PostgreSQL y Redis en el taller.",
    "areas": "Prefiero backend sobre frontend.",
}


def _assert_within_corpus(answer: str, attributed: set[str], vocabulary: str) -> None:
    claimed = _terms_in(answer, _VOCABULARIES[vocabulary])
    assert claimed <= attributed, (
        f"the answer claims {sorted(claimed - attributed)} and the corpus "
        f"attributes {sorted(attributed)} ({vocabulary})"
    )


def test_the_area_vocabulary_still_has_terms_the_corpus_never_attributes():
    """Why the ``areas`` corpus-wide control is still a control, and not a tautology.

    The counterexample it uses claims ``qa``, ``nube`` and ``movilidad``. If a
    corpus edit ever attributes those, the negative control becomes vacuous and
    ``test_the_consistency_checker_catches_an_overstated_answer[areas]`` would
    pass for the wrong reason. This is the tripwire for that, and it is written
    down rather than left to be rediscovered.

    It also records WHY the counterexample had to change when this file moved
    off the invented stand-in. The original one claimed "integracion de la
    inteligencia artificial" as ungrounded; the real corpus attributes AI all
    over the place, so that answer became grounded and the control reported
    itself worthless -- which was the correct report. The three terms pinned
    here are the ones the real corpus genuinely never mentions.
    """
    attributed = _corpus_vocabulary(_VOCABULARIES["areas"], _fixture_documents())
    unattributed = set(_VOCABULARIES["areas"]) - attributed
    assert unattributed == {"movilidad", "nube", "qa"}, (
        f"the areas vocabulary's unattributed terms changed: {sorted(unattributed)}. "
        f"The corpus-wide overstatement control for areas is calibrated on "
        f"{{'movilidad', 'nube', 'qa'}}; if the corpus now attributes one of "
        f"them, the counterexample in _OVERSTATED_SUBJECTS has to move to a "
        f"term the corpus still does not attribute."
    )


@pytest.mark.parametrize("vocabulary", _corpus_wide_vocabularies())
def test_the_consistency_checker_accepts_a_faithful_answer(vocabulary):
    """The positive control: an answer that agrees with the corpus passes."""
    attributed = _corpus_vocabulary(
        _VOCABULARIES[vocabulary], _fixture_documents()
    )
    assert attributed, (
        f"the fixture corpus attributes no {vocabulary}, so this control has "
        "nothing to accept or reject"
    )
    _assert_within_corpus(_FIXTURE_SUBJECTS[vocabulary], attributed, vocabulary)


@pytest.mark.parametrize("vocabulary", _corpus_wide_vocabularies())
def test_the_consistency_checker_catches_an_overstated_answer(vocabulary):
    """The negative control, per vocabulary: the assertion can be false.

    Each counterexample names a term the real corpus never attributes, so the
    control fails if the vocabulary or the scanner stops matching that way. For
    ``areas`` that term set is now narrower than it was on the stand-in this
    file used to run against -- the real corpus mentions AI, which the
    stand-in did not -- so the counterexample had to be re-chosen against a
    measured corpus rather than carried over.
    """
    attributed = _corpus_vocabulary(
        _VOCABULARIES[vocabulary], _fixture_documents()
    )
    overstated = _OVERSTATED_SUBJECTS[vocabulary]
    ungrounded = _terms_in(overstated, _VOCABULARIES[vocabulary]) - attributed
    assert ungrounded, (
        f"the counterexample is grounded in the corpus, so the {vocabulary} "
        "control is not testing anything: pick a different one"
    )
    with pytest.raises(AssertionError) as caught:
        _assert_within_corpus(overstated, attributed, vocabulary)
    for term in ungrounded:
        assert term in str(caught.value), (
            f"the failure must name the offender {term!r}: {caught.value!r}"
        )


def test_the_checker_catches_an_understated_answer():
    """An answer that omits a database the corpus does list must be REJECTED."""
    corpus_databases = _fixture_databases()
    assert len(corpus_databases) >= 2, (
        f"the control needs a corpus with more than one database to be able to "
        f"omit one; found {sorted(corpus_databases)}"
    )
    understated = "MySQL y PostgreSQL."
    with pytest.raises(AssertionError) as caught:
        _assert_cache_does_not_understate(understated, corpus_databases)
    omitted = corpus_databases - _terms_in(understated, "databases")
    assert omitted, (
        f"the control needs the answer to omit something; it omits nothing out of "
        f"{sorted(corpus_databases)}"
    )
    named = {d for d in omitted if d in str(caught.value).lower()}
    assert named, (
        f"the failure must name an omitted database, and it named none of "
        f"{sorted(omitted)}: {str(caught.value)!r}"
    )


def test_the_checker_reads_a_union_of_pages_not_one():
    """The single-page weakness, made explicit.

    The database check originally read one page's summary line. Redis is
    attributed by the corpus on other pages, so a one-page read reported it as
    unsupported -- a false failure that would have been "fixed" by deleting the
    claim.
    """
    corpus_databases = _fixture_databases()
    profile_only = _terms_in(
        (WIKI_DIR / "profile" / "mikel.md").read_text(encoding="utf-8"),
        _VOCABULARIES["databases"],
    )
    assert corpus_databases > profile_only, (
        "the corpus no longer attributes a database outside the profile page, "
        "so this test no longer distinguishes a union read from a single-page "
        f"one: profile={sorted(profile_only)} union={sorted(corpus_databases)}"
    )
    _assert_cache_does_not_overstate(
        "Trabajo con " + " y ".join(sorted(corpus_databases)) + ".", corpus_databases
    )


def test_the_corpus_attributed_databases_are_the_ones_it_lists():
    """Pin what the corpus attributes, so the controls above stay meaningful.

    If someone edits the corpus and drops a database, the understated control
    stops having anything to omit and starts passing for the wrong reason. That
    is exactly the silent-coverage-loss this section exists to prevent.

    Re-derived from the real ``wiki/``: the invented stand-in these were
    calibrated on attributed only {postgres, postgresql, redis}; the real
    corpus also mentions MySQL, Oracle, MongoDB and SQLAlchemy. Nothing here
    was loosened -- the control has more to omit, which makes it a stricter
    test of the checker, not a weaker one.
    """
    assert _fixture_databases() == {
        "mongodb", "mysql", "oracle", "postgresql", "redis", "sqlalchemy",
    }, (
        "the corpus's database list changed; the consistency controls above are "
        "calibrated on this set. A corpus edit that REMOVES a database makes the "
        "understated control vacuous; one that ADDS one just widens it."
    )


# ─── The class: a claim the corpus does not attribute, in ANY entry ──────────
#
# WHY A THIRD SCANNER, AND WHY IT IS NOT ANOTHER HANDFUL OF ANSWERS
# ------------------------------------------------------------------
# The state this section is written against, measured on the table as it stood:
#
#   * 20 cached answers, spoken verbatim to an interviewer.
#   * The cache is consulted BEFORE retrieval, so RAG grounding cannot see an
#     answer at all. That is the property every one of these defects depended
#     on, including the four found by the per-claim audit that produced this
#     section: an invented self-disclosure ("no tenerle miedo a lo que este por
#     venir"), an invented cognitive event ("mi cabeza hizo click"), and an
#     invented technical claim ("endpoints ... de gestion de sesiones", "Entiendo
#     verbos HTTP").
#   * The guards that existed covered `databases` and `areas` -- two
#     hand-registered vocabularies -- plus repo-evidence deploy mechanisms. A
#     fourth class of claim was reachable by every future entry and by no scan.
#
# So the invariant is one step above the ones already here: a cached answer may
# not name a CONCRETE CLAIM the candidate's own corpus never attributes, in
# either of two classes of claim.
#
#   self_disclosure  a trait or self-assessed psychological state volunteered
#                    as the candidate's own. Nothing in the repository can
#                    settle this either way; only the candidate's pages can, and
#                    they are authoritative. This is the class the audit was
#                    told to look for.
#   artefacts        a nameable technical artefact, construct or product. A
#                    recruiter asking "have you used X" gets a yes or a no, and
#                    this cache would be the one saying it.
#
# Both are SCANNERS, not allowlists, in the sense the rest of this file means
# it: a term the corpus attributes passes without this test knowing anything
# about it, and a term the corpus does not attribute turns the suite red. The
# vocabularies are deliberately separate from ``_VOCABULARIES`` because that one
# carries a reach requirement and a calibration contract of its own, and a
# vocabulary with no current violation must be allowed to be quiet.
#
# CORPUS ABSENCE IS A FAILURE HERE, NOT A SKIP
# ----------------------------------------------
# Every ``needs_real_wiki`` check above is ``skipif``-ed on the corpus being
# absent, which is why "the one guard that exists" historically never ran in
# CI: a skip is green, and a green skip is indistinguishable from a pass. This
# scanner has no such escape. ``wiki/`` is 46 tracked files in ``origin/main``
# and ``actions/checkout`` brings it to every run, so an absent corpus means the
# corpus was REMOVED -- which is precisely the event that would silently turn
# every truthfulness check in this repository into dead code. The failure is
# raised from inside the test, so it is a red run, and the message says which
# event it is looking for.
#
# THE LIMITS, MEASURED RATHER THAN ASSUMED
# -----------------------------------------
#   * A union scan grounds a term the corpus mentions ANYWHERE. So it accepts a
#     term used in a negating context: ``wiki/faq/fortalezas-y-debilidades.md``
#     line 24 names "perfeccionista" in order to warn against claiming it, and
#     that mention alone makes "perfeccionista" scannable-as-grounded. Verified,
#     not assumed -- the negative control below deliberately does NOT use that
#     word, and says so where the reader will find it.
#   * A union scan cannot adjudicate a CONTRADICTION. One cached answer says he
#     used Python on DAM projects while
#     ``wiki/stories/autodidacta-fastapi-docker-async.md`` line 28 says he
#     learned Python on his own account, outside the syllabus. Every term in
#     both sentences is attributed by the corpus, so no vocabulary scan of any
#     kind can catch it. Catching it needs the question-to-page binding this
#     file already documents as the limit of the ``areas`` scan (lines 561-568)
#     and does not have.
#   * The scan reports "this term is nowhere in the corpus", which is weaker
#     than "this answer misstates the page that answers its question". It is
#     still the class, and it is a failure the cache can be shipped against.

#: Terms the corpus currently attributes, and therefore terms a cached answer is
#: ALLOWED to name. Recorded so a corpus edit that drops one is a visible,
#: deliberate change to what the cache may claim, rather than a silent widening.
#:
#: Measured, not chosen -- and measured PER POPULATION, because this constant was
#: frozen on the author's 37 loaded pages while a clean clone served 33. The two
#: rows live in ``tests/real_wiki.py::COMMENT_FIGURES`` as
#: ``CommentFigures.attributed_traits``, the same record
#: ``tests/test_recall_claims.py`` resolves for the figures the two RAG comments
#: publish; ``_self_disclosure_attributed`` resolves this checkout's row and
#: refuses an unknown corpus rather than scanning against a foreign one.
#:
#: THE TWO ROWS ARE NOW IDENTICAL, and that is a change rather than a
#: coincidence. The reduced row used to be two terms SHORTER, because
#: ``curioso`` is grounded only by ``faq/por-que-contratarte.md`` and
#: ``trabajador`` only by that same page as a whole word -- the one page that
#: still has it in ``stories/huelga-camiones-mercadona.md`` has it pluralised,
#: and ``_normalised_tokens`` does not stem -- and that page used to be one of
#: four a clone did not have. Commit ``efda998`` committed it, so the union scan
#: attributes the same seven terms on both corpora and there is no longer a cached
#: answer that names a trait a clone's corpus cannot support.
#:
#: That does not make the per-population record pointless: the two rows are still
#: selected by a digest of the served corpus, and a future commit that drops the
#: grounding page again would move one row and not the other. It removes the
#: reason the row was there, not the reason it is.
#:
#: Two of the terms are grounded only incidentally, and that is the point of
#: writing them down: ``desordenado`` is the one trait the candidate actually
#: discloses
#: (wiki/faq/fortalezas-y-debilidades.md:21, in full, with its mitigation), and
#: ``perfeccionista`` is grounded by a page that names it in order to WARN
#: against claiming it (same file, line 24) -- so a union scan accepts it even
#: though the corpus never attributes it as a trait. That is the limit this
#: scanner has, exercised in a control rather than described in a comment.
def _self_disclosure_attributed(documents: dict[str, str]) -> frozenset[str]:
    """The attributed trait set for the corpus ``documents`` IS.

    Not a constant, and deliberately: a constant here was a figure from one
    checkout compared against a live measurement of another, which is the defect
    this function exists to end. ``_require_wiki_corpus`` has already refused a
    substituted corpus, and ``comment_figures_for`` refuses an uncalibrated one,
    so the two guards together make "which corpus am I scanning" a checked
    question rather than an assumption.
    """
    figures = comment_figures_for(documents)
    assert figures is not None, (
        f"the served corpus digests to {corpus_digest(documents)[:12]}, which has "
        "no recorded attributed-trait set. They live in "
        "tests/real_wiki.py::CommentFigures.attributed_traits, calibrated for "
        f"{sorted(CORPUS_DIGESTS)}. Re-measure on the corpus that remains and add "
        "its row -- do not point this checkout at another corpus's vocabulary, and "
        "do not widen the vocabulary to make a control pass."
    )
    assert figures.pages == len(documents), (
        f"the row resolved for {figures.population} names {figures.pages} pages "
        f"but this corpus loaded {len(documents)}."
    )
    return figures.attributed_traits

_CLAIM_VOCABULARIES: dict[str, frozenset[str]] = {
    "self_disclosure": frozenset({
        # Candidate's own words about himself. Includes the terms the corpus
        # DOES attribute: a vocabulary that omitted them could not check them,
        # and the positive control below would then pass because it was
        # scanning nothing -- which is the failure mode of every scanner in
        # this file, inverted.
        "ansiedad", "autodidacta", "cabezota", "click", "constante",
        "curioso", "desordenado", "impaciente", "irresponsable", "miedo",
        "miedos", "nervios", "obstinado", "perfeccionista", "perezoso",
        "resolutivo", "supersticion", "terco", "timido", "trabajador",
    }),
    "artefacts": frozenset({
        # Nameable artefacts, constructs and products. Deliberately excludes
        # the database names and the container/orchestrator terms, which
        # `_VOCABULARIES["databases"]` and `_MECHANISM_CLAIMS` already own --
        # two scanners covering one term would be one scanner that could be
        # removed without the other noticing.
        "ansible", "aws", "azure", "cassandra", "confluencia", "django",
        "elasticsearch", "flask", "gcp", "gitlab", "grafana", "graphql", "grpc",
        "jenkins", "jira", "kafka", "microservicio", "orquestacion",
        "prometheus", "rabbitmq", "serverless", "sesion", "sesiones", "spring",
        "terraform", "verbo", "verbos",
    }),
}


#: The two corpora this guard is calibrated for, matching the ones
#: ``tests/real_wiki.py`` records for the retrieval floor: ``wiki/`` as it stands
#: in the checkout running the suite, and ``git show HEAD:``. Both are 37 loaded
#: pages since commit ``efda998`` committed the four FAQ pages a clone used to be
#: missing, so the page count no longer tells them apart and a count is not what
#: this check is.
#:
#: Derived from ``COMMENT_FIGURES`` rather than written out, so there is one
#: record of what a corpus is and not two that can drift apart -- the same
#: reason ``_self_disclosure_attributed`` reads the trait set from there too.
#:
#: This is not a count for tidiness. It closes the SUBSTITUTE-CORPUS hole, and
#: that hole was found by trying to break it: pointing ``WIKI_ROOT`` at a
#: directory holding a single unrelated Markdown file makes ``load_documents()``
#: return successfully, and a guard that only checked "did it load?" would
#: cheerfully scan the substitute and report the real cache as clean. Measuring
#: the wrong corpus while appearing to pass is the exact outcome the header of
#: this file calls "strictly worse than not running" -- so an unrecognised
#: corpus is a failure, in the same spirit as ``measurement_for`` returning
#: ``None`` rather than scoring one corpus against a foreign floor. Since
#: 2026-10-04 the identity is a DIGEST of the served corpus
#: (``tests/real_wiki.py::CORPUS_DIGESTS``), which closes the same hole with no
#: tolerance at all: a substituted corpus has to be byte-identical to a
#: calibrated one to get through.
_CALIBRATED_CORPUS_POPULATIONS = frozenset(f.pages for f in COMMENT_FIGURES)


def _require_wiki_corpus() -> dict[str, str]:
    """The candidate's pages, or a FAILURE explaining that they are gone.

    Not a skip. See the section comment: a skip is green, and the whole point
    of this scanner is that a guard which cannot run must not be able to look
    like one that passed.
    """
    from tests.real_wiki import load_documents

    try:
        documents = load_documents()
    except Exception as exc:  # noqa: BLE001  -- any loader failure is the same event
        raise AssertionError(
            f"the candidate's wiki/ is absent or unloadable at {WIKI_DIR} "
            f"({type(exc).__name__}: {exc}). These 50 files are tracked in "
            "origin/main and actions/checkout brings them to every run, so an "
            "absent corpus means the corpus was REMOVED. Failing here on "
            "purpose: with the corpus gone, every truthfulness check in this "
            "repository becomes dead code that still looks like coverage, and a "
            "skip would report that as green."
        ) from exc
    assert documents, (
        f"{WIKI_DIR} loaded zero documents. The cached answers are claims about "
        "a person; a corpus that attributes nothing cannot support any of them, "
        "so this is a failure and not an empty set of violations."
    )
    assert len(documents) in _CALIBRATED_CORPUS_POPULATIONS, (
        f"the corpus loaded {len(documents)} documents, which is not a "
        f"population this guard is calibrated for "
        f"({sorted(_CALIBRATED_CORPUS_POPULATIONS)}: 37 pages on either corpus, "
        "and the two differ only in text -- this is a fast pre-check, and "
        f"`_self_disclosure_attributed` then resolves the row by digest). "
        "Either wiki/ was replaced with something else that still loads, or it "
        "was edited. This is a failure on purpose: a substituted corpus would "
        "let this scan report the real cache as clean while measuring a "
        "different set of pages, which is worse than not running at all."
    )
    return documents


def test_the_claim_scan_refuses_a_substituted_corpus(monkeypatch):
    """The substitute-corpus hole, closed and pinned.

    A directory containing one unrelated Markdown file makes the production
    loader succeed. Without the population assertion in ``_require_wiki_corpus``
    every claim scan in this section would have run against that file, found
    nothing, and reported the cache as clean -- a green run measuring the
    wrong subject, which is the outcome this file's header calls out as worse
    than a skipped guard.
    """
    import tests.real_wiki as real_wiki

    with tempfile.TemporaryDirectory() as empty:
        substitute = Path(empty)
        (substitute / "notes.md").write_text(
            "Nothing the candidate has ever said lives here.", encoding="utf-8"
        )
        monkeypatch.setattr(real_wiki, "WIKI_ROOT", substitute)
        monkeypatch.setattr(real_wiki, "CANDIDATE_ROOT", substitute)

        with pytest.raises(AssertionError) as caught:
            _require_wiki_corpus()
        assert "calibrated" in str(caught.value), (
            f"the failure must say the population is uncalibrated: {caught.value!r}"
        )


def test_the_corpus_attributes_exactly_the_disclosed_traits_this_scan_assumes():
    """Pin the attributed/unattributed split, so the control stays a control.

    If a corpus edit starts attributing "miedo", the negative control below
    becomes vacuous and would start passing for the wrong reason. That is a
    visible change here rather than a control that quietly stopped testing.

    Scoped to the population this checkout produced. It was one frozen set of
    seven terms, measured on the author's 37 pages, compared against a live
    measurement of whatever corpus was loaded -- so on a clean clone it reported
    a vocabulary the clone does not have. The comparison is still exact set
    equality: what changed is which set it is compared against, not how
    strictly.
    """
    documents = _require_wiki_corpus()
    attributed = _corpus_vocabulary(
        _CLAIM_VOCABULARIES["self_disclosure"], documents
    )
    recorded = _self_disclosure_attributed(documents)

    assert attributed == recorded, (
        f"the self_disclosure vocabulary's attributed set changed: "
        f"{sorted(attributed)}. The calibration and the negative control below "
        f"are both written against {sorted(recorded)}; re-derive them rather "
        "than let a control pass for the wrong reason."
    )


def test_no_cached_answer_makes_a_claim_the_wiki_does_not_support():
    """The class, as a property of every entry rather than one of them.

    Fails if any cached answer -- present or future, named or not -- names a
    concrete claim the candidate's own pages never attribute. Every defect this
    repository has found in this table was a claim of exactly this shape, made
    in a string that never reaches retrieval.
    """
    documents = _require_wiki_corpus()
    attributed = {
        name: _corpus_vocabulary(terms, documents)
        for name, terms in _CLAIM_VOCABULARIES.items()
    }
    for name, attributed_terms in attributed.items():
        unattributed = set(_CLAIM_VOCABULARIES[name]) - attributed_terms
        assert unattributed, (
            f"every term of the {name} vocabulary is now attributed by the "
            f"corpus, so the {name} scan can never turn red and is inert"
        )

    offenders = _scan_offenders(
        _cached_answers(), attributed, _CLAIM_VOCABULARIES
    )
    assert not offenders, (
        f"cached answers making a claim the wiki does not support: {offenders}. "
        "Either the answer names something the candidate's pages never say, or "
        "the page that says it is not in the corpus. An interviewer is the last "
        "person who should find out which."
    )


def test_the_claim_scan_rejects_a_disclosure_the_wiki_never_makes():
    """The negative control, on the shape of the defect that was reported.

    An invented self-disclosure is the worst case this system can ship: it is
    volunteered to a recruiter as a fact about a person, and no file in the
    repository can settle it. "Perfeccionista" would be the natural example and
    is deliberately NOT used -- wiki/faq/fortalezas-y-debilidades.md:24 names it
    to warn against claiming it, so a union scan grounds it and the control
    would pass for the wrong reason. That is the limit recorded in the section
    comment, exercised rather than described.
    """
    documents = _require_wiki_corpus()
    attributed = _corpus_vocabulary(
        _CLAIM_VOCABULARIES["self_disclosure"], documents
    )
    invented = "Mi mayor logro es no tenerle miedo a lo que este por venir."
    assert "miedo" not in attributed, (
        "the corpus now attributes 'miedo'; this control proves nothing and the "
        "negative example has to change"
    )
    offenders = _scan_offenders(
        [invented], {"self_disclosure": attributed}, _CLAIM_VOCABULARIES
    )
    assert offenders, (
        "an invented self-disclosure was not rejected by the scan, so the scan "
        "is not pointed at the class of defect it was written for"
    )
    assert "miedo" in offenders[invented]["self_disclosure"], (
        f"the failure must name the undisclosed trait: {offenders!r}"
    )


def test_the_claim_scan_accepts_the_disclosure_the_wiki_makes():
    """The positive control, and the discrimination that makes the guard usable.

    If the scanner banned trait words outright it would be unusable: the
    candidate's one honestly-disclosed weakness would have to be removed from
    the answer to a question that asks for it, which is the opposite defect.
    wiki/faq/fortalezas-y-debilidades.md:21 states the weakness in full,
    including the mitigation, so the honest answer passes the same scan that
    rejects the invented one.
    """
    documents = _require_wiki_corpus()
    attributed = {
        name: _corpus_vocabulary(terms, documents)
        for name, terms in _CLAIM_VOCABULARIES.items()
    }
    honest = get_cached_response("¿Cuáles son tus debilidades?")
    assert honest is not None
    assert "desordenado" in honest.lower(), (
        "the weaknesses answer no longer states the weakness the corpus "
        f"discloses: {honest!r}"
    )
    offenders = _scan_offenders([honest], attributed, _CLAIM_VOCABULARIES)
    assert not offenders, (
        "the scan rejected a claim wiki/faq/fortalezas-y-debilidades.md:21 "
        f"makes verbatim, so it cannot be used to tell a true answer from a "
        f"false one: {offenders}"
    )


def test_the_claim_scan_rejects_an_artefact_the_wiki_never_mentions():
    """The artefact class, proved on the claim shape that shipped.

    "Endpoints ... de gestion de sesiones" and "Entiendo verbos HTTP" were both
    removed from the REST answer with no page behind either. Neither word occurs
    anywhere in the corpus, which is the whole of the evidence.
    """
    documents = _require_wiki_corpus()
    attributed = _corpus_vocabulary(
        _CLAIM_VOCABULARIES["artefacts"], documents
    )
    invented = (
        "En InterviewTTS cree endpoints de conversacion, streaming de audio con "
        "SSE y gestion de sesiones. Entiendo verbos HTTP y status codes."
    )
    offenders = _scan_offenders(
        [invented], {"artefacts": attributed}, _CLAIM_VOCABULARIES
    )
    assert offenders, (
        "an artefact claim the corpus never mentions was accepted, so the scan "
        "is not pointed at the class of defect it was written for"
    )
    assert set(offenders[invented]["artefacts"]) == {"sesiones", "verbos"}, (
        f"the scan must name exactly the ungrounded constructs: {offenders!r}"
    )
