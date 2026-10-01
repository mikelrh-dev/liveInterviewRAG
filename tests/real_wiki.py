"""The REAL corpus, the labels authored against it, and the floor's provenance.

WHY THIS MODULE REPLACED ``tests/fixture_corpus.py``
---------------------------------------------------
The synthetic corpus it carried was not a neutral stand-in. It was a structural
clone of this repository's own ``wiki/``: the same eight directories, 13
identical filenames, 9 of 11 FAQ slugs byte-identical, 14 of 42 pages present
under the same name. The gate that certified it as independent
(``verify_no_derivation``) read named entities out of page *bodies* and never
looked at a filename or a directory, so it reported ``shared 0 / OK`` over a
clone. The corpus it replaced was never absent: ``git ls-files wiki`` returns 46
files, they are in ``origin/main``, and CI checks them out. The test file, the
CI workflow and this module all claimed otherwise.

The measured consequence, on the only labelled set that was ever authored
against the real pages (recovered from git at ``769f30f``):

    fixture corpus, 49 labels     recall@3 0.776
    real wiki,   49 labels        recall@3 0.6531

The "0.653 -> 0.776 improvement" that was reported was a corpus swap. On the
fixture the current chunker ALSO fell (0.816 -> 0.776): the fixture floor was
rejecting the better chunker while the production corpus was never consulted.

The other half of the story is why the fixture was so bad as an instrument. Its
gold pages sat roughly 6x further from their nearest distractor than real pages
do (median gold-vs-distractor cosine margin +0.0360 fixture vs +0.0060 real).
A retrieval floor is only sensitive in proportion to how close the right answer
is to the wrong ones; the fixture was a corpus where almost any retriever wins,
so its floor measured nothing about the retriever that ships.

So the guard measures ``wiki/``. What is left here is the machinery that makes
that honest: the loader, the labels, and a floor that records where it came
from.

THE LABELS
----------
Recovered verbatim from ``769f30f`` (``git show 769f30f:tests/test_rag.py`` and
``:tests/test_rag_chunk_size_sweep.py``). They are NOT re-authored. Their value
is precisely that they were written by hand against the real pages; new
questions written today would carry today's assumptions about how the corpus
looks, which is the bias being removed.

PHRASING RULES, unchanged from the recovered set and worth restating: the
question reaches the RAG verbatim from Whisper, so it arrives lowercase,
unpunctuated and with unreliable accents. "An accurate Spanish question" would
be measuring a distribution this pipeline never sees.

It is deliberately NOT named ``test_*.py``: it contains no tests.

WHICH MODEL PRODUCED THESE FLOORS, AND WHEN
------------------------------------------
The floors in this module are a FROZEN HISTORICAL MEASUREMENT, not a value
recomputed from whatever run is happening now. Re-deriving them from the current
run would make the guard agree with itself by construction, which is the exact
defeat of a floor. What may change is the NUMBER, and only when the measurement
it records has genuinely been re-run; the FORMULA that turns a measurement into
a floor is pinned by
``tests/test_rag.py::test_the_floor_is_a_function_of_the_measurement_not_a_literal``
and must not be touched.

There have been two such re-measurements, and both are recorded here so a reader
never has to guess which vector space a number belongs to:

  * 2026-08-28, ``all-MiniLM-L6-v2`` (English), no identity prefix, no per-page
    cut. recall@3 32/49 = 0.6531 full, 27/41 = 0.6585 reduced; floors 0.6122 and
    0.6098. The 0.6531 figure is the one quoted across this repository's own
    audit trail; it describes a configuration that no longer ships.
  * 2026-09-29, ``paraphrase-multilingual-MiniLM-L12-v2`` (multilingual), the
    page-identity prefix on the embedded text, the one-chunk-per-page top-k cut
    and the bodyless-heading filter. recall@3 40/49 = 0.8163 full. THE FULL
    FLOOR IS FROM THAT RUN; the reduced floor is not (see ``MEASURED_REDUCED``).
  * 2026-10-01, the reduced population re-measured on the COMMITTED corpus --
    35/41 = 0.8537, floor 0.8049 -- after the 34/41 = 0.8293 published for it
    turned out to have been measured on the author's working tree.

Both populations were measured through the production path -- the real loader,
the real 400/50 chunker, real embeddings, real ``expand_query``, strict
primary-gold-page matching at ``top_k=3``.

BECAUSE THE CORPUS HAS TWO STATES, THE COMMENTS DO TOO
-----------------------------------------------------
``Measurement`` covers the recall floor. The corpus-SHAPE figures this
repository publishes -- chunk count, matrix shape, what the threshold filter
costs a caller, the word-shape of the chunk distribution, what the per-page cut
rescues, which self-disclosure traits the corpus attributes -- are recorded per
population in ``CommentFigures`` below, because they differ between the two
checkouts (124 chunks against 116; a median of 54.0 against 53.5; a page cut
worth 1 rescued question against 2). A comment naming only one of them
describes a corpus the other checkout does not have, which is how
``tests/test_recall_claims.py`` came to fail on a clean clone and how
``tests/test_chunk_size_comment.py``,
``tests/test_rag_chunk_size_sweep.py`` and
``tests/test_response_cache.py`` came to fail alongside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = REPO_ROOT / "wiki"
CANDIDATE_ROOT = REPO_ROOT / "candidate"

#: The shipped chunker configuration, pinned here so the floor below is a
#: statement about a specific chunker rather than about whatever config says
#: today. ``backend/config.py`` ships 400/50 and ``TestChunkSizeComment`` and
#: ``tests/test_rag_chunk_size_sweep.py`` both measure it there.
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50

#: The embedding model the production pipeline loads by default. A floor
#: measured with a different vector space is a different measurement.
#:
#: ``paraphrase-multilingual-MiniLM-L12-v2``, which is what ``backend/config.py``
#: and ``RAGPipeline.__init__`` both default to. It replaced the English
#: ``all-MiniLM-L6-v2`` on 2026-09-29; every floor below was re-measured under it
#: and none of them survived the change unaltered, which is the point of
#: recording the model in the module that asserts the floor.
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


# ── Loading ──────────────────────────────────────────────────────────────────


def wiki_is_present() -> bool:
    """True when ``wiki/`` exists as a directory with Markdown in it."""
    return WIKI_ROOT.is_dir() and any(WIKI_ROOT.rglob("*.md"))


def load_documents() -> Dict[str, str]:
    """Load the real wiki through the production loader.

    Not a hand-rolled ``rglob``: the point is that ``_SKIP_FILES``
    (``index.md``, ``README.md``, ``CONVENCIONES.md``) and ``_SKIP_DIRS``
    (``templates/``) really drop the build artifacts, because that is the
    behaviour ``TestGeneratedIndexIsNotACandidateDocument`` guards.
    """
    from backend.services.candidate import CandidateProfile

    profile = CandidateProfile(CANDIDATE_ROOT, wiki_dir=WIKI_ROOT)
    profile.load()
    assert profile.documents, f"wiki/ must load; checked {WIKI_ROOT}"
    return profile.documents


def build_pipeline(chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP, **kwargs):
    """A ``RAGPipeline`` over the real wiki, writing no cache anywhere.

    ``cache_dir`` stays ``None`` -- the pipeline's own default -- because that
    is the only way to guarantee this harness is not a second writer to
    ``backend/.rag_cache/``, which the app rewrites at startup.
    """
    from backend.services.rag import RAGPipeline

    rag = RAGPipeline(chunk_size=chunk_size, chunk_overlap=chunk_overlap, **kwargs)
    rag.ingest_documents(load_documents())
    return rag


# ── The labelled question set (recovered from 769f30f, unedited) ──────────────


@dataclass(frozen=True)
class Case:
    question: str
    primary: str
    also: FrozenSet[str]
    doc_class: str


def _c(
    question: str, primary: str, also: Sequence[str] = (), doc_class: str = "narrative"
) -> Case:
    return Case(question, primary, frozenset(also), doc_class)


FAQ = "faq"
NARRATIVE = "narrative"

LABELLED_CASES: Tuple[Case, ...] = (
    # ── FAQ pages: the H1 IS the canonical interview question. This is the
    # group the H1 re-attachment was measured to hurt, so it is the group the
    # "chunk size is the cause" hypothesis has to answer for.
    _c("cual es tu nivel de ingles", "faq/nivel-ingles.md", doc_class=FAQ),
    _c("como es tu ingles hablando", "faq/nivel-ingles.md", doc_class=FAQ),
    _c("cuando podrias incorporarte al puesto", "faq/disponibilidad.md", doc_class=FAQ),
    _c("puedes empezar a trabajar ya estas disponible", "faq/disponibilidad.md", doc_class=FAQ),
    _c("cuales son tus fortalezas y debilidades", "faq/fortalezas-y-debilidades.md", doc_class=FAQ),
    _c("cuentame sobre ti en treinta segundos", "faq/presentacion-30-segundos.md", doc_class=FAQ),
    _c("hazme una presentacion rapida de treinta segundos",
       "faq/presentacion-30-segundos.md", doc_class=FAQ),
    _c("que area del desarrollo te gusta mas", "faq/area-preferida.md", doc_class=FAQ),
    _c("prefieres backend o frontend", "faq/area-preferida.md", doc_class=FAQ),
    _c("como te ves profesionalmente en tres o cinco anos",
       "faq/donde-veo-en-3-5-anos.md", doc_class=FAQ),
    _c("que planes tienes para los proximos años", "faq/donde-veo-en-3-5-anos.md", doc_class=FAQ),
    _c("que haces en tu tiempo libre", "faq/hobbies-intereses.md", doc_class=FAQ),
    _c("cuales son tus aficiones y hobbies", "faq/hobbies-intereses.md", doc_class=FAQ),
    _c("que fue lo mas dificil de aprender cuando empece dam",
       "faq/lo-mas-dificil-dam.md", doc_class=FAQ),
    _c("por que deberian contratarte a ti", "faq/por-que-contratarte.md", doc_class=FAQ),
    _c("dame tres razones para contratarte", "faq/por-que-contratarte.md", doc_class=FAQ),
    # The one documented duplicate: two pages really do answer "why leave
    # retail for DAM" -- the FAQ page and the decision record. Named so the
    # strict view can still see the FAQ page lose its own retrieval key.
    _c("por que dejaste los supermercados para estudiar dam",
       "faq/por-que-dejar-supermercados.md",
       ["decisions/dejar-mercadona-para-dam.md"], doc_class=FAQ),
    _c("por que quieres trabajar aqui", "faq/por-que-esta-empresa.md", doc_class=FAQ),
    _c("que buscas en una empresa", "faq/por-que-esta-empresa.md", doc_class=FAQ),

    # ── Decision records
    _c("por que decidiste dejar mercadona para estudiar dam",
       "decisions/dejar-mercadona-para-dam.md"),
    _c("por que una arquitectura de tres capas para el detector de fraude",
       "decisions/fraud-detector-3-layer-architecture.md"),
    _c("por que elegiste construir interviewtts como portfolio",
       "decisions/por-que-interviewtts.md"),

    # ── Experience
    _c("que estabas haciendo en mercadona los ultimos años",
       "experience/gerente-mercadona-2019-2025.md"),
    _c("cuentame tu trabajo como encargado en bm",
       "experience/encargado-bm-2016-2019.md"),
    _c("empezaste como frutero en mercadona no", "experience/frutero-bm-2015-2016.md"),

    # ── Opinions
    _c("para que sirven los tests hoy en dia con ia", "opinions/importancia-tests.md"),
    _c("que opinas de la ia en el desarrollo de software", "opinions/opinion-ia-desarrollo.md"),
    _c("trabajo remoto o presencial y frameworks o vanilla",
       "opinions/remoto-presencial-frameworks.md"),

    # ── Profile
    _c("cuentame tu perfil profesional", "profile/mikel.md"),

    # ── Projects
    _c("que es el detector de fraude", "projects/fraud-detector.md"),
    _c("con que stack hiciste el detector de fraude", "projects/fraud-detector.md"),
    _c("cuales son las metricas del detector de fraude", "projects/fraud-detector.md"),
    _c("que es interviewtts", "projects/interview-tts.md"),
    _c("que tecnologias usa interviewtts", "projects/interview-tts.md"),
    _c("que resultados dio el proyecto de la pagina web de velneo",
       "projects/pagina-web-practicas.md"),
    _c("en que consistian tus practicas en ceesa", "projects/pagina-web-practicas.md"),

    # ── Skills
    _c("que experiencia tienes con javascript y frontend", "skills/frontend.md"),
    _c("que sabes de backend y java", "skills/backend.md"),
    _c("que bases de datos has usado", "skills/data.md"),
    _c("que herramientas de devops y git manejas", "skills/devops.md"),
    _c("como testias tu codigo", "skills/testing.md"),

    # ── Stories
    _c("cuentame lo de la huelga de camiones en mercadona",
       "stories/huelga-camiones-mercadona.md"),
    _c("por que usaste edge tts en vez de clonar la voz", "stories/edge-tts-vs-clonacion.md"),
    _c("como aprendes algo nuevo", "stories/aprendizaje-autodidacta.md"),
    _c("aprendiste todo el stack de interviewtts por tu cuenta",
       "stories/aprender-interviewtts.md"),
    _c("que hiciste con fastapi docker y asincronia en dam",
       "stories/autodidacta-fastapi-docker-async.md"),
    _c("como gestionabas el tiempo en mercadona", "stories/gestion-tiempo-mercadona.md"),
    _c("lidieraste al equipo durante el covid", "stories/liderazgo-covid-mercadona.md"),
    _c("te equivocaste con la configuracion de whisper", "stories/whisper-config-default.md"),
)


# ── When can this corpus be measured at all? ─────────────────────────────────
#
# Two things have to be true, and both are checked rather than assumed, because
# each one produces a guard that looks green while measuring nothing:
#
#   1. ``wiki/`` is there. It is 46 files in the index and in origin/main today,
#      and the owner's decision to keep a personal dossier on a public remote is
#      a known open issue they have explicitly deferred. If that changes, this
#      corpus goes with it.
#
#   2. EVERY labelled gold page is present. Four FAQ pages
#      (``nivel-ingles``, ``disponibilidad``, ``hobbies-intereses``,
#      ``por-que-contratarte``) exist on disk but are NOT in the index, so 8 of
#      the 49 labels point at a page a clean clone does not have.
#
# On (2) the answer is deliberately NOT "score the other 41 and carry on". A
# floor derived from 49 questions applied to 41 of them is a floor that kept its
# value while its population changed, which is the exact mistake this module
# was written to undo -- and the 8 missing questions are the FAQ group, which is
# the group the H1 decision turns on, so silently dropping them would erase the
# evidence for the decision rather than merely weaken it.
#
# So: no verdict is produced, the guard skips, and
# ``tests/test_rag.py::TestTheRetrievalGuardActuallyRan`` fails so that a skip
# can never be read as a pass.

#: The labelled gold pages that exist on disk but are NOT in the index, so a
#: clean clone does not have them. Measured, not assumed: these are the four of
#: ``_UNTRACKED_FAQ_PAGES`` that the labelled set names, and they cover 8 of the
#: 49 questions. A clean clone therefore resolves 41 labels, not 49 -- which is
#: exactly why the guard refuses to score a subset rather than reporting a
#: 49-question floor over 41 questions.
_UNTRACKED_FAQ_PAGES = (
    "faq/nivel-ingles.md",
    "faq/disponibilidad.md",
    "faq/hobbies-intereses.md",
    "faq/por-que-contratarte.md",
)


def _norm(source: str) -> str:
    """Document keys are ``str(Path.relative_to(...))``, so separators vary."""
    return source.replace("\\", "/")


def unresolved_gold_pages(documents: Dict[str, str] | None = None) -> List[str]:
    """Labelled gold pages the loader does not serve, in first-seen order."""
    if documents is None:
        documents = load_documents()
    present = {_norm(k) for k in documents}
    seen: List[str] = []
    for case in LABELLED_CASES:
        if case.primary not in present and case.primary not in seen:
            seen.append(case.primary)
    return seen


def resolved_cases(documents: Dict[str, str]) -> List[Case]:
    """The labelled questions whose PRIMARY gold page the loader actually serves."""
    present = {_norm(k) for k in documents}
    return [c for c in LABELLED_CASES if c.primary in present]


# ── The floor, and the measurement it came from ──────────────────────────────
#
# A floor that does not say what it measured is a rumour. So this one says it
# here, in the module that asserts it, rather than in a comment that can drift
# out of the code it describes.

#: How many questions of loss a floor tolerates, and WHY that number.
#:
#: One question is 1/49 = 0.0204 of recall (1/41 = 0.0244 on the reduced
#: population). That is the noise scale: the chunk size sweep already recorded a
#: single question moving recall by 0.034 on a 29-question set, and it set its
#: own minimum credible margin at THREE questions for exactly this reason. A
#: floor therefore sits two questions below the measurement: a two-question
#: wobble is not a result, and a three-question loss is. The floors below are
#: written as expressions so that raising a measurement without deciding about
#: the tolerance cannot happen by accident.
TOLERATED_QUESTIONS = 2


@dataclass(frozen=True)
class Measurement:
    """One measured population, and the floor derived from it.

    There are two because the corpus has two states, and a clean clone is not
    the same corpus as the author's checkout.
    """

    name: str
    questions: int
    hits: int
    corpus: str

    @property
    def recall3(self) -> float:
        return self.hits / self.questions

    @property
    def floor(self) -> float:
        return self.recall3 - TOLERATED_QUESTIONS / self.questions


#: The full population: every one of the 49 labelled gold pages is served. This
#: is the author's working tree, where four FAQ pages exist on disk.
#:
#: MEASURED 2026-09-29 with ``paraphrase-multilingual-MiniLM-L12-v2``, the
#: identity-prefixed chunk text and the one-chunk-per-page top-k cut: 40 of 49.
#: The same configuration measured 32 of 49 (0.6531) under the previous
#: ``all-MiniLM-L6-v2`` with no prefix and no cut, so the floors in this module
#: are not comparable across that model change and never were meant to be.
MEASURED_FULL = Measurement(
    name="full",
    questions=len(LABELLED_CASES),
    hits=40,
    corpus="wiki/ as it is on the author's machine -- 37 loaded pages",
)

#: The reduced population: the four untracked FAQ pages are gone, so 41 of the
#: 49 questions can be scored. This is what ``actions/checkout`` produces, and
#: therefore what CI measures.
#:
#: It is a SEPARATE measurement with its own floor rather than the 49-question
#: floor applied to 41 questions, which is the exact mistake this module exists
#: to stop. It is not a weaker instrument, it is a different one: 35/41 =
#: 0.8537, above the full population's own 0.8163, on a corpus where the eight
#: dropped questions are exactly the FAQ group the H1 decision turns on.
#:
#: RE-MEASURED 2026-10-01, and this is the second re-measurement of this
#: population because the first one measured the wrong tree. The 34/41 = 0.8293
#: published until then was derived on the author's working tree -- 15 uncommitted
#: ``wiki/*.md`` files -- so it described a corpus with the same 33 pages and the
#: same 116 chunks and none of the same text, and therefore a different ranking.
#: ``tests/test_committed_corpus_figures.py`` is the guard that measures this
#: population against ``git show HEAD:`` rather than against disk, which is the
#: only checkout this row is ever read on.
MEASURED_REDUCED = Measurement(
    name="reduced",
    questions=41,
    hits=35,
    corpus="wiki/ as actions/checkout produces it -- 33 loaded pages, 4 untracked "
           "FAQ pages absent",
)

MEASUREMENTS: Tuple[Measurement, ...] = (MEASURED_FULL, MEASURED_REDUCED)


# ── The figures the two shipped comments publish, per population ────────────
#
# WHY THIS IS NOT ``Measurement`` DOING THE JOB
# --------------------------------------------
# A ``Measurement`` owns a recall figure and the floor derived from it. What this
# repository publishes beyond recall is corpus-SHAPE: a chunk count, a matrix
# shape, the share the threshold filter costs a caller, the shape of the chunk
# word-length distribution, what the per-page top-k cut rescues, and which
# self-disclosure traits the corpus attributes. Those are facts about a corpus,
# so they differ between the two populations exactly as recall does -- 124
# chunks against 116, a 49 x 124 matrix against 41 x 116, 959 discarded results
# against 720, a median of 54.0 words against 53.5.
#
# Before this existed, each of those figures was published for the full
# population and nothing else. On a clean clone that sentence is false: the
# corpus loads 33 pages and 116 chunks. ``tests/test_recall_claims.py`` turned
# red against a simulated clone for exactly that reason -- five assertions
# binding a comment's number to a live measurement of a population the comment
# never claimed to describe -- and so, later, did one guard in each of
# ``test_chunk_size_comment.py``, ``test_rag_chunk_size_sweep.py`` and
# ``test_response_cache.py``. Same defect, same shape: a frozen figure from the
# author's tree compared against a live measurement of a clone's.
#
# So the two populations are recorded here together with the recall
# measurements they belong to, and every one of those figures is published per
# population. Nothing is relaxed: the same live measurement is bound to the
# published figure in either population, against the row for THAT population.
#
# MEASURED 2026-10-01, same production path as ``MEASURED_FULL``/``MEASURED_REDUCED``
# (real loader, real 400/50 chunker, real ``expand_query``, strict
# primary-gold-page matching, counted through ``retrieve()``). The question set
# is ``resolved_cases`` and the divisor is that population's size, because the
# eight questions whose gold page a clean clone lacks cannot be scored against a
# corpus that does not contain it.
#
# AND ON WHICH CHECKOUT, which is not a detail: the ``full`` row is measured on
# the author's working tree, which is what that population IS. The ``reduced``
# row is measured on the COMMITTED corpus, because that is the only checkout
# anybody else ever has, and its previous calibration had been taken on the
# working tree -- where its figures are never exercised, because there the
# population is ``full``. ``tests/test_committed_corpus_figures.py`` rebuilds
# that corpus from ``git show HEAD:`` and holds this row to it.
#
# The word-shape, page-cut and attributed-traits fields are MEASURED
# 2026-10-01, on both checkouts, through the same production path:
# ``_chunk_document`` for the word shape, ``retrieve()`` with
# ``_one_chunk_per_page`` neutralised for the duration of the comparison for the
# page cut, and ``_corpus_vocabulary`` over the union of every served page for
# the traits. ``p95`` is NEAREST RANK, ``sorted(words)[ceil(0.95 * n) - 1]``,
# which is the definition that reproduces the 131 this row published before the
# reduced population was measured at all; a linear-interpolation p95 would put
# the same corpus at 130.7 and the reduced one at 129.5, so the definition is
# named here because two of them are in common use and only one is this row's.


@dataclass(frozen=True)
class CommentFigures:
    """One population's corpus-shape figures, as this repository publishes them.

    ``filtered``/``unfiltered`` are counts of RESULTS returned by ``retrieve()``
    at ``top_k`` = twice the chunk count -- the only space the public path can be
    asked about, and the unit the threshold comment had to be rewritten into.

    The word-shape fields (``median_words`` .. ``chunks_at_ceiling``) were added
    for ``backend/main.py``'s ``CHUNK_SIZE`` justification, and
    ``rescued_by_page_cut`` for the guard in
    ``tests/test_rag_chunk_size_sweep.py`` that pins what the per-page cut buys;
    ``attributed_traits`` for the self-disclosure vocabulary in
    ``tests/test_response_cache.py``. They live here for one reason: every one of
    them is a function of the corpus, so every one of them has a different value
    on each of the two checkouts. Measured on this branch:

        field                  full (37pp/49q)    reduced (33pp/41q)
        chunks                       124                 116
        median_words                54.0               53.5
        p95_words                    131                 131
        longest_words                266                 266
        under15_pct                  0.0                 0.0
        under30_pct                 13.7                13.8
        chunks_at_ceiling              0                   0
        rescued_by_page_cut            1                   2
        attributed_traits              7                   5

    ``median_words`` is a float because it is one. ``statistics.median`` over an
    EVEN number of chunks returns the mean of the two middle values, and 124 and
    116 are both even, so the reduced population's median is genuinely ``53.5``.
    That is not a rounding artefact and is not a defect in the measurement: an
    assertion that could only compare an integer would have forced one of the
    two populations to publish a number the measurement does not produce.

    ``attributed_traits`` is smaller on the reduced population because two of
    the terms are grounded ONLY by ``faq/por-que-contratarte.md``, one of the
    four FAQ pages that exist on disk and are not in the index: ``curioso``
    nowhere else, and ``trabajador`` nowhere else as a whole word (the
    remaining page has it pluralised, which ``_normalised_tokens`` does not
    stem). A union scan therefore attributes fewer traits on a clean clone, and
    a cached answer naming one of those two is a claim the clone's corpus does
    not support -- which is the scan working, not the scan weakening.
    """

    population: str
    pages: int
    questions: int
    recall1: float
    recall3: float
    mrr5: float
    chunks: int
    filtered: int
    unfiltered: int
    median_words: float
    p95_words: int
    longest_words: int
    under15_pct: float
    under30_pct: float
    chunks_at_ceiling: int
    rescued_by_page_cut: int
    attributed_traits: FrozenSet[str]

    @property
    def dropped(self) -> int:
        return self.unfiltered - self.filtered

    @property
    def share(self) -> float:
        """The filter's cost as a share of the unfiltered result count."""
        return self.dropped / self.unfiltered

    @property
    def matrix(self) -> str:
        return f"{self.questions} x {self.chunks}"

    @property
    def headroom(self) -> int:
        """How far the longest chunk is from ``CHUNK_SIZE``.

        The load-bearing number in the ``CHUNK_SIZE`` justification: it is what
        "400 never binds" means, and it is derived rather than recorded so it
        cannot drift away from ``longest_words``.
        """
        return CHUNK_SIZE - self.longest_words


COMMENT_FIGURES_FULL = CommentFigures(
    population="full",
    pages=37,
    questions=49,
    recall1=0.7347,
    recall3=0.8163,
    mrr5=0.7803,
    chunks=124,
    filtered=854,
    unfiltered=1813,
    median_words=54.0,
    p95_words=131,
    longest_words=266,
    under15_pct=0.0,
    under30_pct=13.7,
    chunks_at_ceiling=0,
    rescued_by_page_cut=1,
    attributed_traits=frozenset({
        "autodidacta", "constante", "curioso", "desordenado", "perfeccionista",
        "resolutivo", "trabajador",
    }),
)

COMMENT_FIGURES_REDUCED = CommentFigures(
    population="reduced",
    pages=33,
    questions=41,
    recall1=0.7561,
    recall3=0.8537,
    mrr5=0.8118,
    chunks=116,
    filtered=631,
    unfiltered=1353,
    median_words=53.5,
    p95_words=131,
    longest_words=266,
    under15_pct=0.0,
    under30_pct=13.8,
    chunks_at_ceiling=0,
    rescued_by_page_cut=2,
    attributed_traits=frozenset({
        "autodidacta", "constante", "desordenado", "perfeccionista", "resolutivo",
    }),
)

COMMENT_FIGURES: Tuple[CommentFigures, ...] = (
    COMMENT_FIGURES_FULL,
    COMMENT_FIGURES_REDUCED,
)


def comment_figures_for(
    documents: Dict[str, str] | None = None,
) -> Optional[CommentFigures]:
    """The comment figures calibrated for this exact population, or ``None``.

    A third population is a refusal, for the same reason ``measurement_for`` is
    one: a figure is a statement about a corpus, and scoring it against a
    population it was not measured on is the mistake this module exists to stop.
    """
    if documents is None:
        documents = load_documents()
    n = len(resolved_cases(documents))
    for figures in COMMENT_FIGURES:
        if figures.questions == n:
            return figures
    return None

def p95(words: Sequence[int]) -> int:
    """The 95th percentile by NEAREST RANK, over ``words`` in any order.

    Nearest rank, not interpolation: ``sorted(w)[ceil(0.95 * n) - 1]``. Two
    percentile definitions are in common use and they disagree here -- on the
    full population the nearest-rank p95 is 131 and the linearly interpolated
    one is 130.7 -- so the published ``p95_words`` would be meaningless without
    this. It lives here, next to the rows, so a guard cannot quietly re-derive
    it with the other convention.
    """
    ordered = sorted(words)
    assert ordered, "a percentile over an empty chunk set is not a measurement"
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


#: Convenience aliases for the population most checkouts see. The guard resolves
#: its measurement through ``measurement_for`` rather than through these, so a
#: third population cannot be silently scored against either one.
MEASURED_HITS = MEASURED_FULL.hits
MEASURED_QUESTIONS = MEASURED_FULL.questions
MEASURED_RECALL3 = MEASURED_FULL.recall3
RECALL3_FLOOR = MEASURED_FULL.floor


def measurement_for(documents: Dict[str, str]) -> Optional[Measurement]:
    """The measurement calibrated for this exact population, or ``None``.

    ``None`` is a refusal, not a fallback. A corpus that resolves some THIRD
    number of labels has been edited in a way that changes the population, and
    scoring it against either existing floor would be the "floor keeps its value
    while its population changed" mistake in a new costume.
    """
    n = len(resolved_cases(documents))
    for measurement in MEASUREMENTS:
        if measurement.questions == n:
            return measurement
    return None


def guard_blocker(documents: Dict[str, str] | None = None) -> str | None:
    """Why the real-corpus measurement cannot be made, or ``None`` when it can.

    ``None`` is the only value that means "this guard has a verdict". Every
    other return is a refusal, and the caller must not turn one into a pass.
    """
    if not wiki_is_present():
        return (
            f"{WIKI_ROOT} is absent or contains no Markdown. The 46 wiki files "
            f"are in the index today (git ls-files wiki), so this means the "
            f"corpus was removed, not that it was never here."
        )
    if documents is None:
        documents = load_documents()
    missing = unresolved_gold_pages(documents)
    if not missing:
        return None
    n = len(resolved_cases(documents))
    calibrated = {m.questions for m in MEASUREMENTS}
    untracked = [p for p in _UNTRACKED_FAQ_PAGES if p in missing]
    if n in calibrated:
        return None
    covered = sum(1 for c in LABELLED_CASES if c.primary not in missing)
    return (
        f"{len(missing)} labelled gold page(s) are not served by the loader: "
        f"{missing}. {len(untracked)} of them are on disk but not in the index "
        f"({untracked}), which is expected on a clean clone. What is NOT "
        f"expected is the population this leaves: {covered} of "
        f"{len(LABELLED_CASES)} questions resolve, and this guard has floors "
        f"calibrated for {sorted(calibrated)} only. Re-measure and add a "
        f"Measurement for {covered}, or restore the pages. Scoring an "
        f"uncalibrated population against an existing floor is the mistake this "
        f"module was written to prevent."
    )


#: The commit that set these floors, for the reader who wants to diff against it.
#: The floors above were last re-measured on 2026-09-29, together with the
#: multilingual embedder that produced them; the previous pair (32/49 and 27/41,
#: floors 0.6122 and 0.6098) was measured with ``all-MiniLM-L6-v2``, no identity
#: prefix and no per-page cut, and belongs to a vector space that no longer
#: ships.
FLOOR_SET_BY = "2026-09-29, under paraphrase-multilingual-MiniLM-L12-v2"

FLOOR_CHUNKER = f"chunk_size={CHUNK_SIZE} chunk_overlap={CHUNK_OVERLAP}"
FLOOR_EMBEDDER = EMBEDDING_MODEL
FLOOR_VIEW = "strict (primary gold page only), top_k=3, direct retrieve() path"
