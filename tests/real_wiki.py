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
    and the bodyless-heading filter. recall@3 40/49 = 0.8163 full, 34/41 =
    0.8293 reduced; floors 0.7755 and 0.7805. THESE are the floors the guard
    enforces now.

Both populations were measured through the production path -- the real loader,
the real 400/50 chunker, real embeddings, real ``expand_query``, strict
primary-gold-page matching at ``top_k=3``.
"""

from __future__ import annotations

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
#: to stop. The two floors land within 0.005 of each other (0.7755 against
#: 0.7805), which is the sanity check: the reduced population is not a weaker
#: instrument, it is a different one, and it happens to agree.
MEASURED_REDUCED = Measurement(
    name="reduced",
    questions=41,
    hits=34,
    corpus="wiki/ as actions/checkout produces it -- 33 loaded pages, 4 untracked "
           "FAQ pages absent",
)

MEASUREMENTS: Tuple[Measurement, ...] = (MEASURED_FULL, MEASURED_REDUCED)

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
