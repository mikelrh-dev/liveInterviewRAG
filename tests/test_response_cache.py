"""Tests for the response cache service (backend/services/response_cache.py)."""

import fnmatch
import os
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
    """'¿Qué experiencia tienes con Docker?' returns a true answer.

    Asserting only that the word "docker" is present was the shape of the
    defect: it passed while the answer fabricated a docker-compose deployment
    this repository does not have. It now asserts the substance — that the
    answer describes the deployment that ships and denies the one that does
    not.
    """
    answer = get_cached_response("¿Qué experiencia tienes con Docker?")
    assert answer is not None
    lowered = answer.lower()
    assert "docker" in lowered
    assert "no lo uso" in lowered, (
        f"the answer no longer declines the Docker credential it cannot "
        f"support: {answer!r}"
    )
    for mechanism in ("systemd", "nginx"):
        assert mechanism in lowered, (
            f"the answer no longer describes the deployment that actually "
            f"ships ({mechanism}): {answer!r}"
        )
    assert "docker-compose" not in lowered, (
        f"the answer credits a compose deployment this repository does not "
        f"have: {answer!r}"
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
    answers: list[str], attributed: dict[str, set[str]]
) -> dict[str, dict[str, list[str]]]:
    """Answers naming, per vocabulary, terms the corpus does not attribute.

    One function, used by every scan in this file. That is deliberate: the
    defect this section corrects was partly that the check lived beside one
    hand-picked answer, so there was exactly one thing to get wrong and exactly
    one place to extend.
    """
    offenders: dict[str, dict[str, list[str]]] = {}
    for answer in answers:
        for name, terms in attributed.items():
            ungrounded = sorted(_terms_in(answer, _VOCABULARIES[name]) - terms)
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
    offenders = _scan_offenders(_cached_answers(), {vocabulary: attributed})
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
# also disclaims it, and the disclaimed case is the one the shipped answer
# takes ("En InterviewTTS no hay contenedores", "Docker no lo uso en este
# proyecto").
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
        "sentence -- which is how the Docker answer says it."
    )


def test_the_mechanism_scan_detects_the_shipped_docker_claim(monkeypatch):
    """The negative control, on the exact string that shipped.

    A guard that has never been seen to reject the thing it was written for is
    a guard nobody can trust, and this one is a lexical scan that could quietly
    stop matching -- a renamed vocabulary entry, a stripped disclaimer list.
    So the real sentence is injected and must be rejected by the real scan.
    """
    shipped = (
        "He usado Docker con docker-compose para desplegar InterviewTTS en un "
        "VPS. Lo configuré con Nginx como reverse proxy. Aún estoy aprendiendo, "
        "pero entiendo los conceptos básicos de contenedores y orquestación."
    )
    monkeypatch.setattr(
        "backend.services.response_cache._CACHED_QUESTIONS",
        [{"answer": shipped, "phrases": ["x"], "keywords": []}],
    )
    offenders = _mechanism_offenders(_cached_answers()[0])
    assert offenders, (
        "the scan no longer rejects the claim that shipped, so it is not "
        "pointed at the defect it was written for"
    )
    for mechanism in ("docker", "contenedores"):
        assert mechanism in offenders, (
            f"the scan missed {mechanism!r} in the shipped claim; it now "
            f"reports {sorted(offenders)}"
        )


def test_the_mechanism_scan_accepts_a_disclaimed_mechanism():
    """The positive control: denying a mechanism the repo lacks is the fix.

    Without this the guard would be "never say the word Docker", which would
    make the honest answer to "¿Qué experiencia tienes con Docker?" impossible
    to give — the recruiter asked, and silence is its own failure.
    """
    assert _mechanism_offenders("En este proyecto no hay contenedores.") == {}
    assert _mechanism_offenders("Docker no lo uso en este proyecto.") == {}
    assert _mechanism_offenders("En InterviewTTS no hay contenedores.") == {}


def test_the_mechanism_scan_accepts_a_mechanism_the_repository_ships():
    """Naming a mechanism this repository really ships is not a defect.

    The scan is evidence-bound in both directions. This asserts the other
    direction explicitly, and asserts the evidence exists — so if the systemd
    unit were ever deleted, this fails and says the evidence moved, rather than
    the guard quietly becoming narrower.
    """
    for artifacts in (("*.service",), ("*.conf",)):
        assert _repo_has_artifact(artifacts), f"no artifact matches {artifacts}"
    assert _repo_has_artifact(_CONTAINER_ARTIFACTS) is False, (
        "this repository ships a container artifact, so a Docker claim would "
        "no longer be a fabrication; recalibrate rather than let the "
        "evidence-bound branch of the scan go untested"
    )
    offenders = _mechanism_offenders(
        "Lo despliego con systemd y nginx delante haciendo de proxy inverso."
    )
    assert offenders == {}, f"a shipped mechanism was reported as ungrounded: {offenders}"


def test_the_mechanism_scan_is_not_pointed_at_nothing():
    """Guard the guard: the vocabulary must be able to fire.

    A scanner whose patterns match no shipped answer, and whose evidence
    globs match no file, would pass every test above while checking nothing.
    This is the "does the scan reach" question from the section above, asked
    of the deployment vocabulary.
    """
    offenders = _mechanism_offenders("Despliego con Kubernetes en el VPS.")
    assert "kubernetes" in offenders, (
        f"the vocabulary cannot detect a claim it names: {offenders}"
    )
    # The container globs must currently match NOTHING, or the docker control
    # above is passing for the wrong reason (this repository has no Dockerfile).
    assert not _repo_has_artifact(_CONTAINER_ARTIFACTS), (
        "this repository now ships a container artifact; the docker claim is "
        "no longer a fabrication, so recalibrate the guard rather than "
        "letting it pass for the wrong reason"
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

    offenders = _scan_offenders(_cached_answers(), {"areas": attributed})
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
