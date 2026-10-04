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
    turned out to have been measured on the author's working tree. That
    population STOPPED EXISTING on 2026-10-04; see the fourth bullet.
  * 2026-10-04, ``reduced`` re-measured again, on a population of the same
    SHAPE it always had (37 loaded pages, all 49 gold pages served) but not of
    the same corpus: commit ``efda998`` committed the four FAQ pages the old
    definition of ``reduced`` was built on, so a clean clone stopped loading 33
    pages and started loading 37. Measured 40/49 = 0.8163 on the committed
    corpus, floor 0.7755.

Both populations were measured through the production path -- the real loader,
the real 400/50 chunker, real embeddings, real ``expand_query``, strict
primary-gold-page matching at ``top_k=3``.

THE TWO POPULATIONS, AND WHY THEY ARE STILL TWO
----------------------------------------------
``full`` is ``wiki/`` as it stands in the checkout that is running the suite.
``reduced`` is ``git show HEAD:`` -- what ``git clone`` serves. They were two
populations because the corpus had two PAGE SETS: four FAQ pages lived on the
author's disk and were not in the index, so a clone resolved 41 of the 49 labels
and the author resolved 49. Commit ``efda998`` committed those four pages, so
both checkouts now load 37 pages and resolve 49 of 49.

The page sets stopped differing; the corpora did NOT. Fifteen ``wiki/*.md``
files are modified in the working tree and uncommitted, so the text behind the
identical 37 pages and identical 124 chunks is not the same text, and therefore
the ranking is not the same ranking. Measured on both, same harness, same day:
``full`` recall@1 0.7347 / MRR@5 0.7803 / 44 of 49 at ``top_k=3`` / 45 relevant
slots at 3 / median chunk 54.0 words; ``reduced`` 0.7551 / 0.7980 / 45 of 49 /
46 / 53.0. The lexical rescue costs one question on ``full`` and none on
``reduced``. Collapsing them into one row would republish as "the" figure a
number that only the author's uncommitted edits produce -- the exact defect
``tests/test_committed_corpus_figures.py`` exists to catch, reached by deleting
the guard instead of by committing a page.

WHAT THAT FORCED, AND IT IS NOT A COSMETIC CHANGE: the two rows are no longer
distinguishable by a question count, and they used to be SELECTED by one.
``measurement_for``, ``comment_figures_for``, ``precision_for``, ``latency_for``
and ``rerank_for`` all matched ``row.questions == len(resolved_cases(...))``,
and with both rows at 49 that expression returns ``full`` for the committed
corpus -- a silent fallback, which is the failure mode this module was written
to end. Population identity is therefore the CONTENT of the served corpus
(``CORPUS_DIGESTS``), not a count of how many labels happen to resolve.

THE COST OF THAT, STATED PLAINLY
--------------------------------
A digest does not survive an edit, and that is the point: a figure is a
statement about a corpus, so touching any ``wiki/*.md`` in the working tree
stops the ``full`` row from matching until the corpus is re-measured and the
digest is re-recorded. The previous count key could not see that at all, which
is why a wrong row calibrated on older text passed for months. What this does
NOT buy: it does not make every row exercised. ``tests/test_rerank.py`` loads
only the working tree, so its ``reduced`` row is recorded by measurement and
held by nothing.
"""

from __future__ import annotations

import hashlib
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


# ── What makes a population a population ─────────────────────────────────────
#
# WHY A DIGEST AND NOT A COUNT
# ----------------------------
# The rows used to be selected by ``len(resolved_cases(documents))``: 49 meant
# ``full``, 41 meant ``reduced``, anything else was a refusal. That key was an
# adequate PROXY while the two corpora differed by which pages they had, and it
# became a silent fallback the moment they stopped (commit ``efda998``, which
# committed the four FAQ pages the 41-question population was defined by
# excluding). With both rows at 49 the proxy returns ``full`` for a corpus
# ``full`` was never measured on, and it cannot see a change of TEXT at all --
# which is the class of defect this whole module exists to catch, because a
# figure is a statement about a corpus and a question count is not a corpus.
#
# So the key is the corpus itself: a SHA-256 over the documents the PRODUCTION
# loader actually serves, keyed by source path. Three properties it has that a
# count does not:
#
#   * It separates the two rows, which now have the same page count, the same
#     question count and -- as of this measurement -- the same chunk count.
#   * It keeps the refusal property for the case that matters: a corpus that is
#     neither of the two calibrated ones resolves to no population at all, so
#     ``measurement_for``/``comment_figures_for``/``precision_for``/
#     ``latency_for``/``rerank_for`` return ``None`` rather than a neighbour.
#   * It moves with the loader. The digest is over what the loader SERVES, not
#     over the bytes on disk, so a change to ``_SKIP_FILES``/``_SKIP_DIRS``/the
#     [TODO] filter invalidates every row -- correctly, because the corpus the
#     production path serves has changed.
#
# LIMIT: it does not survive an edit to the working tree, by design (see the
# module docstring). The pair below was measured on this branch with 15
# uncommitted ``wiki/*.md`` files; committing or reverting any of them moves the
# ``full`` digest and the row has to be re-measured, not re-pointed.

def corpus_digest(documents: Dict[str, str]) -> str:
    """SHA-256 over ``documents``, path-sorted, content included verbatim.

    Path AND content, not content alone: two corpora that serve the same text
    under different names rank the same but are not the same corpus, and the
    document key is what ``retrieve()`` reports as the source.
    """
    digest = hashlib.sha256()
    for source in sorted(documents, key=_norm):
        digest.update(_norm(source).encode("utf-8"))
        digest.update(b"\0")
        digest.update(documents[source].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


#: ``population name -> digest of the corpus that row was measured on``.
#:
#: ``full`` is this working tree as it stands. ``reduced`` is ``git show HEAD:``,
#: which is what anybody who clones gets; ``tests/test_committed_corpus_figures.py``
#: rebuilds that corpus and holds the ``reduced`` rows to it. They are recorded
#: here rather than inside each row so there is ONE answer to "which corpus is
#: this", and the five resolvers that need it cannot answer five different ways.
CORPUS_DIGESTS: Dict[str, str] = {
    "full": "aee73e6448744ba66646aa7f931abce94e5e4452d488a34b13e14cc065ec127f",
    "reduced": "8b02b3efe687033b906975d38a7a3ca9bb5531aca35c3047c517207721aee105",
}


def corpus_for(documents: Dict[str, str]) -> Optional[str]:
    """The population this exact corpus IS, or ``None``.

    ``None`` is a refusal, never a nearest match: see the section above.
    """
    digest = corpus_digest(documents)
    for population, recorded in CORPUS_DIGESTS.items():
        if digest == recorded:
            return population
    return None


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
#   1. ``wiki/`` is there. It is 50 files in the index and in origin/main today,
#      and the owner's decision to keep a personal dossier on a public remote is
#      a known open issue they have explicitly deferred. If that changes, this
#      corpus goes with it.
#
#   2. EVERY labelled gold page is present. This used to be a live condition:
#      four FAQ pages (``nivel-ingles``, ``disponibilidad``, ``hobbies-intereses``,
#      ``por-que-contratarte``) existed on disk without being in the index, so 8
#      of the 49 labels pointed at a page a clean clone did not have and a clone
#      resolved 41. Commit ``efda998`` committed all four, so BOTH checkouts now
#      resolve 49 of 49 and this condition holds unconditionally.
#
# It is kept as a condition, and not deleted, because it is the one that says a
# label set is still pointed at real pages. What changed is that its second half
# is no longer what distinguishes the two populations: they are distinguished by
# the TEXT of the pages (``CORPUS_DIGESTS``), which is a stricter test than the
# missing-page one ever was.
#
# The old answer to a violated (2) -- deliberately NOT "score the other 41 and
# carry on" -- still stands and is why the refusals below are refusals. A floor
# derived from 49 questions applied to fewer of them is a floor that kept its
# value while its population changed, which is the exact mistake this module was
# written to undo.


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
#: One question is 1/49 = 0.0204 of recall, and BOTH populations are 49 wide now,
#: so the noise scale is the same for the two of them. That is the noise scale:
#: the chunk size sweep already recorded a single question moving recall by 0.034
#: on a 29-question set, and it set its own minimum credible margin at THREE
#: questions for exactly this reason. A floor therefore sits two questions below
#: the measurement: a two-question wobble is not a result, and a three-question
#: loss is. The floors below are written as expressions so that raising a
#: measurement without deciding about the tolerance cannot happen by accident.
TOLERATED_QUESTIONS = 2


@dataclass(frozen=True)
class Measurement:
    """One measured population, and the floor derived from it.

    There are two because the corpus has two states, and what a clone gets is
    not what the author's checkout holds: same 37 pages, same 49 labels, same
    124 chunks, different text (see ``CORPUS_DIGESTS``).
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


#: The full population: ``wiki/`` as it stands in the checkout running the suite.
#: Every one of the 49 labelled gold pages is served, and so are all 37 loaded
#: pages the loader serves from ``candidate/`` and ``wiki/``.
#:
#: MEASURED 2026-09-29 with ``paraphrase-multilingual-MiniLM-L12-v2``, the
#: identity-prefixed chunk text and the one-chunk-per-page top-k cut: 40 of 49.
#: The same configuration measured 32 of 49 (0.6531) under the previous
#: ``all-MiniLM-L6-v2`` with no prefix and no cut, so the floors in this module
#: are not comparable across that model change and never were meant to be.
#: Re-verified 2026-10-04 against the digest in ``CORPUS_DIGESTS``.
MEASURED_FULL = Measurement(
    name="full",
    questions=len(LABELLED_CASES),
    hits=40,
    corpus="wiki/ as it stands in the checkout running the suite -- 37 loaded "
           "pages, 15 of the 50 wiki/*.md files uncommitted",
)

#: The reduced population: ``git show HEAD:``, which is what ``git clone`` serves
#: and therefore what CI measures. Same 37 loaded pages and the same 49 of 49
#: labels as ``full`` since commit ``efda998`` committed the four FAQ pages the
#: old definition excluded -- so it is no longer a REDUCTION of anything, and the
#: name is historical rather than descriptive. It is still a separate
#: measurement, because fifteen committed pages differ in text from the fifteen
#: the author has modified but not committed, and a floor from one is not a floor
#: for the other.
#:
#: It is a SEPARATE measurement with its own floor rather than the 49-question
#: floor applied to the same 49 questions, which is the exact mistake this
#: module exists to stop -- the mistake now wears "they measured the same number
#: of questions" as its disguise, which is why identity is a digest.
#:
#: RE-MEASURED 2026-10-04 at 40 of 49 = 0.8163, the same numerator ``full``
#: records on different text: recall@1 0.7551 against 0.7347 and MRR@5 0.7980
#: against 0.7803, so the two rows are not interchangeable even though recall@3
#: happens to agree. ``tests/test_committed_corpus_figures.py`` is the guard that
#: measures this row against ``git show HEAD:`` rather than against disk, which is
#: the only checkout this row is ever read on.
MEASURED_REDUCED = Measurement(
    name="reduced",
    questions=len(LABELLED_CASES),
    hits=40,
    corpus="git show HEAD: -- 37 loaded pages, 124 chunks, every wiki/*.md file "
           "committed",
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
# so they differ between the two populations exactly as recall does.
#
# Before this existed, each of those figures was published for the full
# population and nothing else. On a clean clone that sentence was false: the
# corpus loaded 33 pages and chunked to 116. ``tests/test_recall_claims.py``
# turned red against a simulated clone for exactly that reason -- five
# assertions binding a comment's number to a live measurement of a population
# the comment never claimed to describe -- and so, later, did one guard in each
# of ``test_chunk_size_comment.py``, ``test_rag_chunk_size_sweep.py`` and
# ``test_response_cache.py``. Same defect, same shape: a frozen figure from the
# author's tree compared against a live measurement of a clone's.
#
# So the two populations are recorded here together with the recall
# measurements they belong to, and every one of those figures is published per
# population. Nothing is relaxed: the same live measurement is bound to the
# published figure in either population, against the row for THAT population.
#
# MEASURED 2026-10-04, same production path as ``MEASURED_FULL``/``MEASURED_REDUCED``
# (real loader, real 400/50 chunker, real ``expand_query``, strict
# primary-gold-page matching, counted through ``retrieve()``). The question set
# is ``resolved_cases`` and the divisor is that population's size, which is 49 for
# both of them now: commit ``efda998`` committed the four FAQ pages that used to
# make the ``reduced`` population a 41-question subset, so there is no longer a
# question in this module that a corpus can leave out.
#
# AND ON WHICH CHECKOUT, which is not a detail: the ``full`` row is measured on
# the working tree, which is what that population IS. The ``reduced`` row is
# measured on the COMMITTED corpus, because that is the only checkout anybody
# else ever has, and its previous calibration had been taken on the working tree
# -- where its figures are never exercised, because there the population is
# ``full``. ``tests/test_committed_corpus_figures.py`` rebuilds that corpus from
# ``git show HEAD:`` and holds this row to it.
#
# WHAT THE TWO ROWS LOOK LIKE NOW, AND IT IS THE POINT OF THE WHOLE EXERCISE:
# they agree on almost everything that used to tell them apart and disagree on
# the things that matter. Same 37 pages, same 49 questions, same 124 chunks, same
# 131-word p95, same 266-word longest chunk, same seven attributed traits, same
# one question rescued by the per-page cut. Different recall@1 (0.7347 against
# 0.7551), different MRR@5 (0.7803 against 0.7980), one more question served at
# ``top_k=3`` (44 against 45), three more results surviving the filter out of the
# same 1813 unfiltered, a median chunk a word shorter, and one more relevant slot
# in the top 3. That is what "a count that stays right while the text behind it
# changes is the worst kind of corroboration" looks like when it happens again,
# and it is why the rows are keyed on ``CORPUS_DIGESTS`` and not on 33-vs-37.
#
# The word-shape, page-cut and attributed-traits fields are MEASURED
# 2026-10-04, on both corpora, through the same production path:
# ``_chunk_document`` for the word shape, ``retrieve()`` with
# ``_one_chunk_per_page`` neutralised for the duration of the comparison for the
# page cut, and ``_corpus_vocabulary`` over the union of every served page for
# the traits. ``p95`` is NEAREST RANK, ``sorted(words)[ceil(0.95 * n) - 1]``,
# which is the definition that reproduces the 131 these rows published; a
# linear-interpolation p95 would put this corpus at 130.7, so the definition is
# named here because two of them are in common use and only one is these rows'.


@dataclass(frozen=True)
class CommentFigures:
    """One population's corpus-shape figures, as this repository publishes them.

    ``filtered``/``unfiltered`` are counts of RESULTS returned by ``retrieve()``
    at ``top_k`` = twice the chunk count -- the only space the public path can be
    asked about, and the unit the threshold comment had to be rewritten into.

    The ``*_at_top3`` fields are the figures at the top_k PRODUCTION asks for
    (``backend/config.py`` ``RAG_TOP_K``, which ships 3), and they exist because
    every other recall figure here is measured at ``top_k`` = the population
    size. That is a deliberate choice for those measurements -- a top_k wide
    enough to hold every page turns the metric into "is the gold page anywhere in
    the ranking", which is what the embedding-cache identity and the filter
    counts want to know -- and it is the wrong question for the one number an
    interviewer experiences, which is what lands in the model's context. For a
    long time the repository published no figure at top_k=3 at all, so an
    improvement there would have been invisible to every published claim and to
    every guard derived from one. ``recall3_at_top3`` is that figure.

    It is a DIFFERENT measurement from ``recall3``, not a restatement of it, and
    the two can disagree in both directions: the lexical rescue in
    ``RAGPipeline._lexical_rescue`` only fires when the per-page cut actually
    bound, which is a top_k-sized event, so it moves ``recall3_at_top3`` and
    leaves ``recall3`` exactly where it was. That is why both are recorded.

    The word-shape fields (``median_words`` .. ``chunks_at_ceiling``) were added
    for ``backend/main.py``'s ``CHUNK_SIZE`` justification, and
    ``rescued_by_page_cut`` for the guard in
    ``tests/test_rag_chunk_size_sweep.py`` that pins what the per-page cut buys;
    ``attributed_traits`` for the self-disclosure vocabulary in
    ``tests/test_response_cache.py``. They live here for one reason: every one of
    them is a function of the corpus. Measured 2026-10-04 on this branch:

        field                  full (tree)    reduced (HEAD)
        chunks                       124            124
        median_words                54.0            53.0
        p95_words                    131            131
        longest_words                266            266
        under15_pct                  0.0             0.0
        under30_pct                 13.7            13.7
        chunks_at_ceiling              0              0
        rescued_by_page_cut            1              1
        attributed_traits              7              7

    Read that table as the argument for keeping two rows and against trusting a
    count. Seven of the nine fields are now IDENTICAL, which is exactly what a
    count-keyed lookup would have called "one population" -- and the two fields
    that are not identical (the median chunk, one word) are enough to move
    recall@1 by 0.0204 and MRR@5 by 0.0177.

    ``median_words`` is a float because it is one. ``statistics.median`` over an
    EVEN number of chunks returns the mean of the two middle values, and 124 is
    even on both corpora, so a median here is genuinely ``54.0`` or ``53.0``
    rather than an integer. That is not a rounding artefact and is not a defect
    in the measurement: an assertion that could only compare an integer would
    have forced one of the two populations to publish a number the measurement
    does not produce.

    ``attributed_traits`` is the same SEVEN terms on both corpora, and that is a
    change, not a coincidence: it used to be five on ``reduced``, because
    ``curioso`` and ``trabajador`` were grounded only by
    ``faq/por-que-contratarte.md`` and that page used to be missing from a clone.
    Commit ``efda998`` committed it, so the union scan attributes the same
    vocabulary on both and there is no longer a cached answer that names a trait
    the clone's corpus cannot support.
    """

    population: str
    pages: int
    questions: int
    recall1: float
    recall3: float
    mrr5: float
    recall3_at_top3: float
    hits3_at_top3: int
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
    # At the top_k production ships. 44 of 49 with the lexical rescue, 40
    # without it: the rescue fires on 5 questions and costs 1, and both numbers
    # are published because the guard re-derives the pair rather than one of
    # them. ``recall3`` above is unchanged by the rescue and must stay that way.
    recall3_at_top3=0.8980,
    hits3_at_top3=44,
    chunks=124,
    filtered=861,
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
    pages=37,
    questions=49,
    recall1=0.7347,
    recall3=0.8163,
    mrr5=0.7803,
    # 44 of 49 at the shipped top_k=3, which is what the ``full`` row above
    # also measures now. It used to be 45 here and 44 there, and the
    # difference was carried by this comment as evidence that the rescue trades
    # differently per population -- one question the full row loses and the
    # reduced row keeps (``para que sirven los tests hoy en dia con ia``, whose
    # committed text scores differently from the author's rewrite of it). The
    # section intent of ``backend/services/rag.py::section_intent`` removed
    # that one-question gap: both rows now return 44, and the per-population
    # distinction this row existed to record is no longer a distinction.
    recall3_at_top3=0.8980,
    hits3_at_top3=44,
    chunks=124,
    filtered=864,
    unfiltered=1813,
    median_words=53.0,
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

COMMENT_FIGURES: Tuple[CommentFigures, ...] = (
    COMMENT_FIGURES_FULL,
    COMMENT_FIGURES_REDUCED,
)


def comment_figures_for(
    documents: Dict[str, str] | None = None,
) -> Optional[CommentFigures]:
    """The comment figures calibrated for this exact corpus, or ``None``.

    Resolved by ``corpus_for`` (the served corpus's digest), NOT by how many
    labelled questions it resolves. Both populations resolve 49 of 49 since
    ``efda998`` committed the four FAQ pages, so the old question-count lookup
    returned ``full`` for the committed corpus: a figure nobody who clones could
    check, selected without a word of complaint.

    A corpus that is neither of the two is still a refusal, for the reason this
    module exists: a figure is a statement about a corpus, and binding one to a
    corpus it was not measured on is the mistake, whatever the corpus happens to
    resolve.
    """
    if documents is None:
        documents = load_documents()
    population = corpus_for(documents)
    for figures in COMMENT_FIGURES:
        if figures.population == population:
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
#: corpus that is neither calibrated population cannot be silently scored against
#: either one.
MEASURED_HITS = MEASURED_FULL.hits
MEASURED_QUESTIONS = MEASURED_FULL.questions
MEASURED_RECALL3 = MEASURED_FULL.recall3
RECALL3_FLOOR = MEASURED_FULL.floor


def measurement_for(documents: Dict[str, str]) -> Optional[Measurement]:
    """The measurement calibrated for this exact corpus, or ``None``.

    ``None`` is a refusal, not a fallback, and the test is now the corpus's
    digest rather than how many labels it resolves. Both populations resolve 49
    of 49, so the count could not tell them apart, and a lookup keyed on it
    handed the author's working-tree floor to whoever cloned -- a floor measured
    on fifteen pages that are not committed, applied to fifteen that are.

    A corpus that matches neither digest has been edited in a way that changes the
    population, and scoring it against either existing floor would be the "floor
    keeps its value while its population changed" mistake in a new costume.
    """
    population = corpus_for(documents)
    for measurement in MEASUREMENTS:
        if measurement.name == population:
            return measurement
    return None


def guard_blocker(documents: Dict[str, str] | None = None) -> str | None:
    """Why the real-corpus measurement cannot be made, or ``None`` when it can.

    ``None`` is the only value that means "this guard has a verdict". Every
    other return is a refusal, and the caller must not turn one into a pass.
    """
    if not wiki_is_present():
        return (
            f"{WIKI_ROOT} is absent or contains no Markdown. The 50 wiki files "
            f"are in the index today (git ls-files wiki), so this means the "
            f"corpus was removed, not that it was never here."
        )
    if documents is None:
        documents = load_documents()
    missing = unresolved_gold_pages(documents)
    if not missing and corpus_for(documents) is not None:
        return None

    covered = sum(1 for c in LABELLED_CASES if c.primary not in missing)
    digest = corpus_digest(documents)
    calibrated = dict(CORPUS_DIGESTS)
    if not missing:
        return (
            f"this checkout serves {len(documents)} pages and resolves all "
            f"{len(LABELLED_CASES)} labelled questions, but its served corpus "
            f"({digest[:12]}) is neither calibrated population "
            f"({', '.join(f'{name} {value[:12]}' for name, value in sorted(calibrated.items()))}). "
            f"That means wiki/ was edited after the rows were measured -- commit "
            f"or revert it, or re-measure and re-record CORPUS_DIGESTS with the "
            f"new digest. The floors are statements about a corpus, and the corpus "
            f"moved."
        )
    return (
        f"{len(missing)} labelled gold page(s) are not served by the loader: "
        f"{missing}. That leaves {covered} of {len(LABELLED_CASES)} questions "
        f"resolvable, and the served corpus ({digest[:12]}) matches no "
        f"calibrated population ({', '.join(f'{name} {value[:12]}' for name, value in sorted(calibrated.items()))}). "
        f"Re-measure on the corpus that remains and add its Measurement and its "
        f"CORPUS_DIGESTS entry, or restore the pages. Scoring an uncalibrated "
        f"corpus against an existing floor is the mistake this module was "
        f"written to prevent."
    )


#: The commit that set these floors, for the reader who wants to diff against it.
#: The floors above were last re-measured on 2026-10-04 on the committed corpus
#: (40 of 49, floor 0.7755), which repeats the 40 of 49 measured on 2026-09-29 on
#: the working tree; the previous pair (32/49 and 27/41, floors 0.6122 and
#: 0.6098) was measured with ``all-MiniLM-L6-v2``, no identity prefix and no
#: per-page cut, and belongs to a vector space that no longer ships.
FLOOR_SET_BY = "2026-10-04, under paraphrase-multilingual-MiniLM-L12-v2"

FLOOR_CHUNKER = f"chunk_size={CHUNK_SIZE} chunk_overlap={CHUNK_OVERLAP}"
FLOOR_EMBEDDER = EMBEDDING_MODEL
FLOOR_VIEW = "strict (primary gold page only), top_k=3, direct retrieve() path"
