"""Tests for the response cache service (backend/services/response_cache.py)."""

import re
from pathlib import Path

import pytest

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
    """'¿Qué experiencia tienes con Docker?' returns the Docker answer."""
    answer = get_cached_response("¿Qué experiencia tienes con Docker?")
    assert answer is not None
    assert "docker" in answer.lower()


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
    """Keyword 'presenta' inside 'representa' must NOT trigger the pitch."""
    assert get_cached_response("Este proyecto representa mucho para mí") is None


def test_keyword_exact_word_hits():
    """A kept keyword as a standalone word DOES trigger its answer."""
    answer = get_cached_response("¿Cuáles son tus fortalezas y cómo las demuestras?")
    assert answer is not None
    assert "disciplina" in answer.lower()


def test_multiword_keyword_hits():
    """Multi-word keyword 'ia generativa' still triggers the AI answer."""
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


def test_misroute_more_about_interviewtts_still_hits_kept_keyword():
    """'Cuéntame más sobre InterviewTTS' is NOT fixed by the keyword change.

    Verified against the running cache: this question matches on the keyword
    ``interviewtts``, which the removal list explicitly keeps, and on no
    phrase. The answer it returns is the InterviewTTS project description —
    topically correct, but the same text the candidate already heard, which is
    why it was reported as a misroute.

    It is left hitting on purpose: the fix would be to drop the ``interviewtts``
    keyword, and that is the owner's call, not a silent edit here. Recorded as
    a test so the behaviour cannot drift unnoticed.
    """
    answer = get_cached_response("Cuéntame más sobre InterviewTTS")
    assert answer is not None
    assert answer is get_cached_response("¿Qué es InterviewTTS?")


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
            "¿Qué experiencia tienes con Docker?", "docker-compose", id="phrase-docker"
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
    """
    assert len(_CACHED_QUESTIONS) == 20
    assert sum(len(entry["phrases"]) for entry in _CACHED_QUESTIONS) == 85


def test_only_contextually_specific_keywords_remain():
    """The keyword vocabulary is exactly the nine contextually specific terms."""
    remaining = sorted({kw for entry in _CACHED_QUESTIONS for kw in entry["keywords"]})
    assert remaining == [
        "aprendes",
        "aprendiste",
        "bases de datos",
        "debilidades",
        "fortalezas",
        "ia generativa",
        "interviewtts",
        "la ia",
        "presenta",
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
# demonstrated case (window=1: the token before "bases de datos" is "usaste",
# not "no") or rejects legitimate questions (window=2: "¿No puedes diseñar
# bases de datos?"). Resolving scope properly needs parsing, which contradicts
# this module's "no external dependencies" design.


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("¿Por qué no usaste Docker en producción?", id="docker"),
        pytest.param("¿Por qué no dejaste Mercadona?", id="mercadona"),
        pytest.param("¿Por qué no elegiste DAM?", id="dam"),
        pytest.param("¿Has usado alguna vez RAG?", id="rag-not-used"),
    ],
)
def test_negated_question_never_returns_a_positive_cached_answer(question):
    """A negated question about a removed keyword falls through to the LLM."""
    assert get_cached_response(question) is None


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("¿Por qué no usaste bases de datos?", id="why-not"),
        pytest.param("¿Nunca has trabajado con bases de datos?", id="never"),
        pytest.param("Que no es InterviewTTS?", id="what-is-not"),
    ],
)
def test_known_residual_negation_exposure_on_a_kept_keyword(question):
    """KNOWN LIMITATION, needs the owner's call — negation on a kept keyword.

    The eight removed keywords are fixed, but a negation that scopes over one
    of the nine *kept* keywords still selects a positive answer, because
    `bases de datos` and `interviewtts` are contextually specific enough to
    keep and are matched as whole words with no regard to negation.

    This is the case a negation guard would fix, and the case a naive guard
    would break: "no" also occurs in legitimate questions ("¿No puedes diseñar
    bases de datos?"), so a keyword-level negation check would reject those
    too. Recorded here so the exposure stays visible and deliberate rather
    than accidental. Asserted as current behaviour on purpose — if a guard is
    ever added, this test is the one that should flip.
    """
    assert get_cached_response(question) is not None


def test_negation_interposed_in_a_phrase_breaks_the_substring():
    """Characterisation, not a defect test: 'no' inside a phrase breaks the match.

    This is why the phrase fast path needs no negation guard. Inserting the
    negation between two words of a multi-word phrase breaks contiguity, so
    the phrase cannot match. The residual exposure is limited to the nine kept
    keywords, which are contextually specific.
    """
    assert get_cached_response("¿Por qué no dejaste Mercadona?") is None


# ─── Wiki consistency: the cache is a derived store ─────────
#
# `wiki/` is the single source of truth for any factual claim about the
# candidate; this cache is a derived store that loses every disagreement, so a
# divergence is a wrong answer in a real interview. These tests read the wiki
# at test time, so editing the wiki to drop or add a technology fails here
# instead of in front of a recruiter.

WIKI_DIR = Path(__file__).resolve().parents[1] / "wiki"

# Vocabulary used only to *detect* a database name inside a cached answer.
# This is a scanner, not an allowlist of approved claims: a database the wiki
# does not list fails the test, and a database the wiki adds passes without
# touching the test. That is what keeps the assertion maintainable.
_DATABASE_VOCABULARY = {
    "cassandra", "cockroach", "db2", "dynamodb", "elasticsearch", "firebird",
    "mariadb", "mongo", "mongodb", "mssql", "mysql", "oracle", "postgres",
    "postgresql", "redis", "sqlite", "sqlalchemy", "sqlserver",
}


def _cached_answers() -> list[str]:
    return [entry["answer"] for entry in _CACHED_QUESTIONS]


def _wiki_text(relative_path: str) -> str:
    return (WIKI_DIR / relative_path).read_text(encoding="utf-8")


def _wiki_databases() -> set[str]:
    """Databases the wiki attributes to the candidate, from the profile summary.

    wiki/profile/mikel.md, "## Top skills (summary)" -> "**Databases:** ...".
    """
    profile = _wiki_text("profile/mikel.md")
    marker = "**Databases:**"
    for line in profile.splitlines():
        if marker in line:
            listed = line.split(marker, 1)[1]
            return {name.strip().lower() for name in listed.split(",") if name.strip()}
    raise AssertionError("wiki/profile/mikel.md no longer has a '**Databases:**' line")


def test_wiki_lists_the_databases_the_cache_answers_with():
    """The database answer names only databases the wiki attributes to him.

    Regression: the answer claimed "MySQL, PostgreSQL y SQLite" while the wiki
    lists MySQL, PostgreSQL and MongoDB (wiki/profile/mikel.md,
    wiki/skills/data.md) and no SQLite anywhere.
    """
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None

    named = {
        word.lower()
        for word in re.findall(r"[A-Za-z]+", answer)
        if word.lower() in _DATABASE_VOCABULARY
    }
    wiki_databases = _wiki_databases()

    assert named, f"no database name detected in the cached answer: {answer!r}"
    assert named <= wiki_databases, (
        f"cache names databases the wiki does not list: {sorted(named - wiki_databases)}; "
        f"wiki lists {sorted(wiki_databases)}"
    )


def test_cache_answer_names_every_database_the_wiki_lists():
    """The spoken answer covers the whole wiki claim, so it cannot understate it.

    Omission is how "MySQL, PostgreSQL y SQLite" hid the fact that MongoDB is
    the third database the wiki actually attributes to him.
    """
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None
    for database in _wiki_databases():
        assert database in answer.lower(), f"{database} is in the wiki but not the answer"


def test_cache_never_claims_sqlite():
    """SQLite appears nowhere in the wiki, so it must appear nowhere in the cache."""
    offenders = [
        answer for answer in _cached_answers() if "sqlite" in answer.lower()
    ]
    assert not offenders, f"SQLite claimed by the cache, unsupported by the wiki: {offenders}"


def test_pitch_matches_the_wiki_presentation():
    """The pitch must not describe the pre-DAM job as 'encargado de supermercado'.

    The wiki supersedes it: wiki/faq/presentacion-30-segundos.md (2026-08-28)
    replaced that phrasing with "empecé como frutero, progresé a encargado y
    terminé como gerente en Mercadona liderando equipos de ~50 personas", and
    wiki/profile/mikel.md records Gerente B, Mercadona, 2019-Nov 2025.
    """
    answer = get_cached_response("Cuéntame sobre ti")
    assert answer is not None
    assert "encargado de supermercado" not in answer.lower()
    assert "gerente" in answer.lower()
