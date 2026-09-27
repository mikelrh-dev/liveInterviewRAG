"""Does CHUNK_SIZE deserve a change? A measured sweep, not an opinion.

WHY THIS FILE EXISTS
--------------------
``backend/config.py`` ships ``CHUNK_SIZE=400`` / ``CHUNK_OVERLAP=50``. An earlier
review called those constants "dead" because the largest chunk in the corpus is
smaller than 400, and a later change to ``split_sections()`` (a document's own
H1 now rides with the body it titles) measurably cost FAQ recall: 0.724 -> 0.690
on a labelled set, because ``# "Cuéntame sobre ti" — Presentación de 30
segundos`` went from a sharp 0.745 title-only retrieval key to 0.514 merged into
its 67-word body.

That leaves an open question with a real tradeoff on both sides, so it has to be
MEASURED rather than argued:

  * smaller chunks might keep a title's discriminative power while keeping the
    answer with it (the hypothesis under test);
  * larger chunks might suit the narrative pages better, and picking small
    chunks could pay for FAQ recall by handing the LLM a near-title chunk with no
    answer in it.

So the sweep runs, and — crucially — it can come out NULL. ``CHUNK_SIZE`` is a
knob with very little surface area on a 37-document corpus whose longest
section is 266 words, and a knob that barely moves is a knob whose apparent
"winner" is noise. ``DECISION_RULE`` below states, in advance and in numbers,
what evidence would justify moving it; ``test_no_config_justifies_replacing_the_
current_chunk_size`` applies that rule. If nothing clears the bar, the correct
outcome is to keep 400 and say so.

WHAT IS MEASURED
----------------
For every (chunk_size, chunk_overlap) cell, over ``LABELLED_CASES``:

  * ``recall@1 / @3 / @5`` — fraction of questions whose PRIMARY gold document
    appears in the top k. "Primary" means the page whose whole purpose is that
    question. This is the STRICT view, and it is the one that detects the FAQ
    regression: a duplicate page that also answers the question must not be
    allowed to mask a page losing its own retrieval key.
  * ``recall@k_any`` — the LENIENT view: the top k contains *any* document that
    genuinely answers. This is what the user experiences, and it is reported so
    a strict regression that a lenient view hides cannot pass unnoticed.
  * ``MRR`` — mean reciprocal rank, in both views.
  * SUBSTANCE, the control that recall alone cannot provide. A size can win
    recall by making chunks more question-shaped while losing the answer, which
    is precisely the regression the H1 fix exists to prevent. Measured as the
    median number of body words in the top-1 chunk (threshold-free, and the
    primary statistic), plus the fraction of questions whose top-1 body carries
    at least 20 words, plus the fraction whose top-1 is a bare heading or a
    title plus a fragment — the pre-H1-fix failure mode.
  * The FAQ-only and non-FAQ-only recall, so a size that helps FAQ pages at the
    expense of the narrative ones is visible instead of averaged away.

Paired bootstrap over question indices gives a CI on the difference against the
current configuration, because the same 49 questions are scored in every cell
and an unpaired comparison throws that pairing away.

HONEST SCOPE OF N
-----------------
49 questions, one gold document each (plus one documented duplicate pair).
That detects gross failure and large swings. It does NOT resolve differences of
one or two questions: at n=49 a single question is 0.020 of recall, so the
resolution limit is roughly +/-0.04. ``DECISION_RULE`` demands a margin several
times that, plus a CI that excludes zero, precisely so a one-question wobble
cannot be read as a result.

THE SET IS A STRICT SUPERSET OF THE REPOSITORY'S OWN GUARD
-----------------------------------------------------------
All six ``TestRetrievalRegressionGuard.CASES`` questions and both of its xfail
questions appear below, verbatim. The earlier 29-question set was ad-hoc and was
not committed, so it could not be reused; this set was rebuilt from the corpus
and extended to 49, keeping the committed guard questions as a subset so the two
measurements stay comparable in direction if not in composition.

MEASURED RESULT: KEEP 400/50
----------------------------
Run this file to reproduce. Every cell of the grid, all figures measured on the
real 37-page wiki with real ``all-MiniLM-L6-v2`` embeddings:

    cfg      chunks  R@1    R@3    R@5    MRR    FAQ@3  other@3  top1body  subst  thin
    120/24     137   0.531  0.633  0.673  0.591  0.579  0.667        33w  0.898  0.020
    200/40     127   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    300/60     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    400/50     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    400/80     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    600/120    125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000

Nothing beats the shipped value. 300, 400 and 600 emit BYTE-IDENTICAL chunk
sets — no section in the corpus exceeds 266 words, so every cell from 300 up
runs the same experiment four times and carries no information. 200 ties it
exactly (paired delta 0 questions, CI [0.000, 0.000]). 120 is one question
WORSE, and the single question that flips ("cuando podrias incorporarte al
puesto", rank 3 -> absent) is the exact noise scale this measurement warns
about: one question is 0.020 of recall at n=49, and the previous run measured
one question moving recall by 0.034 at n=29.

The hypothesis is refuted structurally, not just numerically — see
``test_the_faq_regression_page_is_untouched_by_the_entire_grid`` and
``test_shrinking_the_chunk_cannot_recover_the_title_only_retrieval_key``.

THREE FIGURES IN THE BRIEF THAT DO NOT SURVIVE MEASUREMENT
----------------------------------------------------------
1. "max 210" is wrong: the longest chunk is 266 words (``skills/backend.md``).
   210 is the SECOND longest (``faq/hobbies-intereses.md``). The conclusion is
   unaffected — 266 is still far below 300 — but anyone reasoning about
   headroom needs the real number.
2. "the constants are dead" is right for CHUNK_SIZE and CHUNK_OVERLAP and also
   true of a THIRD constant nobody was looking at: ``threshold=0.3``. The lowest
   top-1 cosine observed across the 49 questions is 0.414, so no chunk is ever
   filtered out on score.
3. Duplicated documents inside a single top-3 look like the cause of the
   misses and are not: deduplicating by source rescues 0 of 17. See
   ``test_duplicated_top_k_slots_are_a_symptom_not_the_cause``.
"""

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import numpy as np
import pytest

from backend.services.candidate import CandidateProfile
from backend.services.rag import RAGPipeline

ROOT = Path(__file__).resolve().parent.parent

# ── The labelled set ──────────────────────────────────────────────────────────
#
# (question, primary gold, also-correct golds, doc class)
#
# Phrasing rules, taken from how this system is actually driven: the question
# reaches the RAG verbatim from Whisper, so it arrives lowercase, unpunctuated,
# with unreliable accents, and the recruiter rarely bothers with the accents at
# all. Questions are therefore written that way on purpose — "an accurate
# Spanish question" would be measuring a distribution this pipeline never sees.
#
# ``also_golds`` is deliberately almost empty. A document earns an entry here
# only when it is *itself* about the same question, not merely adjacent to it.
# Being generous would inflate the lenient recall and hide strict regressions.
@dataclass(frozen=True)
class Case:
    question: str
    primary: str
    also: FrozenSet[str]
    doc_class: str


def _c(question: str, primary: str, also: Sequence[str] = (), doc_class: str = "narrative") -> Case:
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
    _c("que fue lo mas dificil de aprender cuando empezaste dam",
       "faq/lo-mas-dificil-dam.md", doc_class=FAQ),
    _c("por que deberiamos contratarte a ti", "faq/por-que-contratarte.md", doc_class=FAQ),
    _c("dame tres razones para contratarte", "faq/por-que-contratarte.md", doc_class=FAQ),
    # The one documented duplicate: two pages really do answer "why leave
    # retail for DAM" — the FAQ page and the decision record. Named so the
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
    _c("cuentame lo de la huelga de camiones en mercadona", "stories/huelga-camiones-mercadona.md"),
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

# ── The sweep grid ───────────────────────────────────────────────────────────
#
# Overlap rule, chosen before running anything: ~20% of the size, never >= the
# size (an overlap that exceeds the size is nonsense — the step goes backwards
# and the loop cannot terminate) and never below ~15% (below that it is
# effectively zero and buys nothing). 12.5% (400 -> 50, the shipped default) is
# below that floor, so 400 is measured BOTH ways: at the shipped 50 and at the
# proportional 80.
CURRENT_CONFIG: Tuple[int, int] = (400, 50)

CONFIGS: Tuple[Tuple[int, int], ...] = (
    (120, 24),
    (200, 40),
    (300, 60),
    CURRENT_CONFIG,
    (400, 80),
    (600, 120),
)

# Minimum body words for the top-1 chunk to count as carrying substance. Two
# sentences; below that a chunk cannot hold a complete answer to a recruiter
# question. Reported at several thresholds plus the threshold-free median so the
# conclusion is not hostage to one number.
SUBSTANCE_WORD_THRESHOLD = 20
# A top-1 chunk whose body is this thin is a title, or a title plus a fragment:
# the LLM receives the interviewer's own question back instead of an answer.
THIN_BODY_THRESHOLD = 8


# ── DECISION_RULE: written before any number below was known ──────────────────
#
# A challenger replaces (400, 50) only if it clears ALL FIVE gates. Each gate
# exists because of a specific way this measurement can lie:
#
#   1. margin          — n=49 resolves ~0.04 of recall, so a 1-2 question
#                        difference is noise, and the previous run already
#                        demonstrated 1 question moving recall by 0.034.
#   2. CI excludes 0   — paired bootstrap, because the same questions are
#                        scored everywhere and the pairing is real information.
#   3. same direction  — no trading MRR for recall, or vice versa.
#   4. substance held  — the control. A size that wins recall by returning
#                        more question-shaped chunks has REGRESSED the product,
#                        and must not be adopted no matter what it scores.
#   5. lenient view    — the strict view can fall while the user-facing view
#                        holds; that is acceptable only if lenient does not
#                        also fall.
MIN_RECALL3_MARGIN = 3 / len(LABELLED_CASES)   # 3 questions
MIN_SUBSTANCE_TOLERANCE = 0.02


@dataclass(frozen=True)
class Metrics:
    chunk_size: int
    chunk_overlap: int
    n_chunks: int
    median_chunk_words: float
    max_chunk_words: int
    recall1: float
    recall3: float
    recall5: float
    recall1_any: float
    recall3_any: float
    recall5_any: float
    mrr: float
    mrr_any: float
    faq_recall3: float
    other_recall3: float
    median_top1_body_words: float
    substance_ok_rate: float
    thin_top1_rate: float

    @property
    def label(self) -> str:
        return f"{self.chunk_size}/{self.chunk_overlap}"


def _norm(source: str) -> str:
    """Document keys are ``str(Path.relative_to(...))``, so separators vary."""
    return source.replace("\\", "/")


def _body_of(chunk_text: str) -> str:
    """The chunk's answer-bearing text: everything after its leading heading.

    A heading names a topic, a body answers it. The H1 re-attachment exists so
    that a retrieved chunk is not a bare title, and the substance metrics have
    to look past the heading to see whether that actually happened.
    """
    lines = [l for l in chunk_text.split("\n") if l.strip()]
    if lines and re.match(r"^#{1,3}\s", lines[0]):
        return " ".join(lines[1:])
    return " ".join(lines)


def _rank(sources: List[str], targets: FrozenSet[str]) -> Optional[int]:
    for i, src in enumerate(sources, start=1):
        if src in targets:
            return i
    return None


class _MemoisedEncoder:
    """The production embedder, with a text -> vector memo.

    Configurations in this sweep share most of their chunks (a 266-word section
    is emitted whole at 400 and split at 200, but every section under the
    threshold is byte-identical across all six cells). Memoising turns ~390
    chunk embeddings into ~300 and makes the sweep affordable in the default
    test run rather than a separate script nobody re-runs.
    """

    def __init__(self, inner, memo: Dict[str, np.ndarray]):
        self._inner = inner
        self._memo = memo
        self.calls = 0
        self.embedded = 0

    def encode(self, texts, **kwargs):
        self.calls += 1
        missing = [t for t in texts if t not in self._memo]
        if missing:
            vectors = self._inner.encode(missing, show_progress_bar=False)
            for text, vector in zip(missing, np.asarray(vectors, dtype=np.float32)):
                self._memo[text] = vector
            self.embedded += len(missing)
        return np.stack([self._memo[t] for t in texts])


@pytest.fixture(scope="module")
def real_wiki_documents() -> Dict[str, str]:
    profile = CandidateProfile(ROOT / "candidate", wiki_dir=ROOT / "wiki")
    profile.load()
    assert profile.documents, "the real wiki must still load"
    return profile.documents


@dataclass(frozen=True)
class Sweep:
    metrics: Dict[Tuple[int, int], Metrics]
    pipelines: Dict[Tuple[int, int], RAGPipeline]
    documents: Dict[str, str]


@pytest.fixture(scope="module")
def sweep(real_wiki_documents) -> Sweep:
    """Ingest the real wiki at every cell in the grid and score the labelled set.

    ``cache_dir`` is deliberately left as None: the pipeline then writes no
    cache at all, so this fixture cannot touch ``backend/.rag_cache/`` — the
    repository's own test suite rewrites that file when the app boots, and a
    measurement harness must not be a second writer to a real cache.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    encoder = _MemoisedEncoder(model, {})

    metrics: Dict[Tuple[int, int], Metrics] = {}
    pipelines: Dict[Tuple[int, int], RAGPipeline] = {}
    for chunk_size, overlap in CONFIGS:
        rag = RAGPipeline(chunk_size=chunk_size, chunk_overlap=overlap)
        # Replicates initialize()'s successful branch exactly (real model, no
        # TF-IDF) without paying the per-cell model load.
        rag._initialized = True
        rag._use_tfidf = False
        rag._embedder = encoder
        rag.initialize = lambda: None  # guard against an accidental re-init
        rag.ingest_documents(real_wiki_documents)
        pipelines[(chunk_size, overlap)] = rag
        metrics[(chunk_size, overlap)] = _score(rag)
    return Sweep(metrics=metrics, pipelines=pipelines, documents=real_wiki_documents)


def _score(rag: RAGPipeline) -> Metrics:
    """Score one configuration against the labelled set.

    Retrieval goes through ``retrieve()`` with no ``doc_type``, matching
    ``TestRetrievalRegressionGuard`` so the numbers are comparable in direction
    with the 0.724 / 0.690 figures the H1 change was measured against. The
    production entry points (``get_chunks_with_scores`` / ``get_context_string``)
    additionally apply ``detect_doc_type``; that is a separate mechanism which
    chunk size does not touch, and it is exercised separately below rather than
    folded in here.
    """
    assert not rag._use_tfidf, "TF-IDF fallback would silently measure a different retriever"
    assert rag.embedder is not None, "embedder unavailable — the measurement is meaningless"

    words = sorted(len(c.content.split()) for c in rag.chunks)
    n = len(LABELLED_CASES)

    ranks_primary: List[Optional[int]] = []
    ranks_any: List[Optional[int]] = []
    body_words: List[int] = []

    for case in LABELLED_CASES:
        results = rag.retrieve(case.question, top_k=5)
        sources = [_norm(c.source) for c, _ in results]
        ranks_primary.append(_rank(sources, frozenset({case.primary})))
        ranks_any.append(_rank(sources, frozenset({case.primary}) | case.also))
        top1 = results[0][0].content if results else ""
        body_words.append(len(_body_of(top1).split()))

    def at(ranks: List[Optional[int]], k: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= k) / n

    def mrr_of(ranks: List[Optional[int]]) -> float:
        return float(statistics.mean(1.0 / r if r else 0.0 for r in ranks))

    def by_class(doc_class: str) -> float:
        hits = [
            r for c, r in zip(LABELLED_CASES, ranks_primary) if c.doc_class == doc_class
        ]
        return sum(1 for r in hits if r is not None and r <= 3) / len(hits) if hits else 0.0

    return Metrics(
        chunk_size=rag.chunk_size,
        chunk_overlap=rag.chunk_overlap,
        n_chunks=len(rag.chunks),
        median_chunk_words=float(statistics.median(words)) if words else 0.0,
        max_chunk_words=words[-1] if words else 0,
        recall1=at(ranks_primary, 1),
        recall3=at(ranks_primary, 3),
        recall5=at(ranks_primary, 5),
        recall1_any=at(ranks_any, 1),
        recall3_any=at(ranks_any, 3),
        recall5_any=at(ranks_any, 5),
        mrr=mrr_of(ranks_primary),
        mrr_any=mrr_of(ranks_any),
        faq_recall3=by_class(FAQ),
        other_recall3=by_class(NARRATIVE),
        median_top1_body_words=float(statistics.median(body_words)),
        substance_ok_rate=sum(1 for w in body_words if w >= SUBSTANCE_WORD_THRESHOLD) / n,
        thin_top1_rate=sum(1 for w in body_words if w < THIN_BODY_THRESHOLD) / n,
    )


def _per_question_recall3(rag: RAGPipeline) -> List[bool]:
    """The 0/1 vector the paired bootstrap resamples, one entry per question."""
    out = []
    for case in LABELLED_CASES:
        sources = [_norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)]
        rank = _rank(sources, frozenset({case.primary}))
        out.append(rank is not None and rank <= 3)
    return out


def _paired_bootstrap_ci(a: List[bool], b: List[bool], seed: int = 20260927) -> Tuple[float, float]:
    """95% CI on mean(a) - mean(b), resampling question indices with replacement.

    The two configurations are scored on the SAME questions, so the paired
    difference removes per-question difficulty. Treating the cells as
    independent samples would inflate the CI and hide exactly the small,
    consistent differences this sweep is looking for.
    """
    assert len(a) == len(b)
    rng = np.random.default_rng(seed)
    diffs = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    idx = rng.integers(0, len(diffs), size=(10000, len(diffs)))
    samples = diffs[idx].mean(axis=1)
    lo, hi = np.percentile(samples, [2.5, 97.5])
    return float(lo), float(hi)


# ── What the grid can and cannot vary ────────────────────────────────────────


def test_only_three_distinct_configurations_exist_in_this_grid(sweep):
    """How much can CHUNK_SIZE even change? Prove it instead of asserting it.

    The corpus's longest section is well under 300 words, so every cell from 300
    upwards emits the SAME chunks, byte for byte, and overlap is inert above the
    threshold. That is the single most important fact about this sweep: a third
    of the grid is one experiment repeated four times, so it carries no
    information at all, and no "trend" can be read out of it.

    This test is a property of the CORPUS, so if the wiki grows long enough for
    the threshold to bind, it fails and the grid needs rethinking.
    """
    signatures = {
        cfg: tuple((c.source, c.content) for c in rag.chunks)
        for cfg, rag in sweep.pipelines.items()
    }
    current = signatures[CURRENT_CONFIG]
    inert = sorted(cfg for cfg, sig in signatures.items() if sig == current)
    assert inert == [(300, 60), (400, 50), (400, 80), (600, 120)], (
        f"expected every cell at or above 300 words to be identical to the "
        f"current config on this corpus, but these were: {inert}"
    )
    assert sweep.metrics[CURRENT_CONFIG].max_chunk_words < 300, (
        "the longest chunk reached the smallest inert cell's threshold, so "
        f"{inert} is no longer a set of duplicates"
    )


# ── The decision ─────────────────────────────────────────────────────────────


def _justifies(challenger: Tuple[int, int], sweep: Sweep) -> Optional[str]:
    """Apply DECISION_RULE. Returns the failing gate's name, or None to adopt."""
    base = sweep.metrics[CURRENT_CONFIG]
    cand = sweep.metrics[challenger]

    if cand.recall3 - base.recall3 < MIN_RECALL3_MARGIN:
        return "margin"

    a = _per_question_recall3(sweep.pipelines[challenger])
    b = _per_question_recall3(sweep.pipelines[CURRENT_CONFIG])
    lo, _hi = _paired_bootstrap_ci(a, b)
    if lo <= 0:
        return "ci"

    if cand.mrr < base.mrr or cand.recall1 < base.recall1:
        return "same-direction"

    if cand.substance_ok_rate < base.substance_ok_rate - MIN_SUBSTANCE_TOLERANCE:
        return "substance"

    if cand.recall3_any < base.recall3_any:
        return "lenient-view"

    return None


def test_no_config_justifies_replacing_the_current_chunk_size(sweep):
    """THE RESULT. Fails if any cell clears the pre-declared bar; passes if none does.

    Read this file top to bottom before changing anything: the honest outcome of
    a sweep like this is very often "the constant is fine", and a test that only
    knows how to fail is a test that will be argued with instead of re-run.
    """
    current = sweep.metrics[CURRENT_CONFIG]
    justified, blocked = {}, {}
    for cfg in CONFIGS:
        if cfg == CURRENT_CONFIG:
            continue
        gate = _justifies(cfg, sweep)
        if gate is None:
            justified[cfg] = "cleared all five gates"
        else:
            blocked[cfg] = gate

    assert not justified, (
        f"{len(justified)} configuration(s) cleared the pre-declared bar, so "
        f"CHUNK_SIZE should change: {justified}. Current config "
        f"({CURRENT_CONFIG[0]}/{CURRENT_CONFIG[1]}) scores recall@3="
        f"{current.recall3:.3f} MRR={current.mrr:.3f} "
        f"median_top1_body={current.median_top1_body_words:.0f}w. "
        f"Change backend/config.py and bump CHUNK_FILTER_VERSION. Every other "
        f"config was blocked at: {blocked}."
    )


def test_current_config_meets_its_measured_floor(sweep):
    """Lock in today's behaviour so a later change that breaks it fails loudly.

    These numbers are the MEASURED values of the shipped configuration, recorded
    after the sweep and deliberately not derived from it — a floor fitted to the
    best cell would be a floor that only the best cell passes. Their job is the
    same as ``TestRetrievalRegressionGuard``'s: not to discriminate between
    configurations, but to make a silent retrieval regression impossible to
    merge.

    Flipping ``test_no_config_justifies_replacing_the_current_chunk_size`` to
    failing is the signal that these must be re-baselined in the same change.
    """
    current = sweep.metrics[CURRENT_CONFIG]
    assert current.recall3 == pytest.approx(0.653, abs=0.021), (
        f"recall@3 at {CURRENT_CONFIG} is {current.recall3:.3f}, was 0.653 "
        f"(32/49). A change moved it; re-baseline deliberately or revert."
    )
    assert current.recall1 == pytest.approx(0.551, abs=0.021)
    assert current.mrr == pytest.approx(0.611, abs=0.021)
    # The control, and the reason the H1 fix exists: the top-1 context is an
    # answer, not a restatement of the interviewer's own question.
    assert current.substance_ok_rate >= 0.90, (
        f"only {current.substance_ok_rate:.1%} of top-1 chunks carry a body of "
        f">= {SUBSTANCE_WORD_THRESHOLD} words — the LLM is getting titles back"
    )
    assert current.thin_top1_rate == 0.0, (
        f"{current.thin_top1_rate:.1%} of top-1 chunks are a title or a title "
        f"plus a fragment (< {THIN_BODY_THRESHOLD} body words)"
    )
    assert current.median_top1_body_words >= 30, (
        f"median top-1 body is {current.median_top1_body_words:.0f} words, "
        f"was 36"
    )


# ── Why the answer is "no change": the two structural refutations ────────────
#
# The brief behind this sweep is that CHUNK_SIZE is what dilutes a merged FAQ
# title, and that a smaller size might keep the title's discriminative power
# while retaining the answer. Both of the measurements below were taken AFTER
# the sweep, and both say the hypothesis cannot hold on this corpus.


FAQ_REGRESSION_PAGE = "faq/presentacion-30-segundos.md"
FAQ_REGRESSION_QUERY = "cuentame sobre ti en treinta segundos"


def test_the_faq_regression_page_is_untouched_by_the_entire_grid(real_wiki_documents):
    """The page blamed on chunk sizing is smaller than the SMALLEST cell.

    ``faq/presentacion-30-segundos.md`` is the page whose title-only chunk used
    to score 0.745 and whose merged chunk scores 0.514, and it is why
    ``TestRetrievalRegressionGuard`` carries an xfail. The tempting fix is "make
    the chunks smaller so the title keeps its punch" — but every section on this
    page is under 120 words, the smallest cell in the grid. Lowering CHUNK_SIZE
    to 120 therefore emits this page BYTE FOR BYTE IDENTICALLY, and so does
    every smaller value too. There is no chunk size that changes this page
    without a size below one word.

    So the FAQ regression cannot be a chunk-size problem. Pinning the section
    lengths here means the next person to try this fix finds the arithmetic
    instead of re-running the sweep.
    """
    from backend.services.rag import (
        is_wikilink_reference,
        parse_frontmatter,
        split_sections,
        strip_placeholders,
    )

    key = FAQ_REGRESSION_PAGE.replace("/", "\\")
    metadata, body = parse_frontmatter(real_wiki_documents[key])
    assert str(metadata.get("confidence", "")).lower() != "low", "page was dropped as a draft"
    body, _ = strip_placeholders(body)
    sections = [s.strip() for s in split_sections(body) if not is_wikilink_reference(s.strip())]
    lengths = [len(s.split()) for s in sections]
    smallest_cell = min(size for size, _ in CONFIGS)

    assert max(lengths) < smallest_cell, (
        f"{FAQ_REGRESSION_PAGE} now has a {max(lengths)}-word section, which the "
        f"{smallest_cell}-word cell WOULD split — the 'it is too big to be "
        f"affected' argument no longer holds and this sweep must be re-run."
    )


def test_shrinking_the_chunk_cannot_recover_the_title_only_retrieval_key(sweep):
    """The dilution is complete after ~15 body words, so chunk size is not a dial.

    Measured on the FAQ page above, scoring ``title + first N body words`` for
    the question that lost it:

        N=0   0.745      N=10  0.611      N=30  0.564
        N=3   0.670      N=15  0.508      N=54  0.608
        N=5   0.653      N=20  0.509      N=67  0.514  (the chunk as shipped)

    The curve is steep for the first handful of words and then FLAT and
    non-monotonic: by N=15 the score is already at its floor, and the remaining
    50 words of answer move it by less than 0.1 in either direction. So the
    title's discriminative power is not diluted *proportionally to chunk size*
    — it is swamped the instant any body text is present, and the only chunk
    that would score 0.745 is a chunk containing nothing but the heading.

    A chunk size cannot produce that chunk, because ``split_sections()`` treats
    the heading and the body as one section by design (filter version 3) and
    only ever splits a section in the middle. And that chunk would be exactly
    the defect the H1 fix exists to remove: a title-only top-1 hands the LLM the
    interviewer's own question back instead of an answer. The current corpus
    scores substance_ok=0.918 and thin_top1=0.000, and a title-only top-1 would
    put both back to their pre-fix values.

    The other FAQ titles behave the same way, by wildly different amounts, which
    is the point: a knob that cannot control the magnitude cannot be tuned to
    it. Measured N=0 -> full-merged drops: area-preferida 0.916->0.753,
    fortalezas 0.806->0.647, nivel-ingles 0.778->0.565, por-que-contratarte
    0.805->0.752, disponibilidad 0.514->0.499.
    """
    encoder = sweep.pipelines[CURRENT_CONFIG].embedder
    assert encoder is not None

    def cosine(a: str, b: str) -> float:
        import numpy as np

        v = np.asarray(encoder.encode([a, b]), dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        return float(np.dot(v[0], v[1]))

    chunk = next(
        c for c in sweep.pipelines[CURRENT_CONFIG].chunks
        if _norm(c.source) == FAQ_REGRESSION_PAGE and c.content.startswith("#")
    )
    lines = chunk.content.splitlines()
    title = lines[0].strip()
    body = " ".join(" ".join(lines[1:]).split()).split()

    score_title_only = cosine(title, FAQ_REGRESSION_QUERY)
    score_15 = cosine(f"{title} {' '.join(body[:15])}", FAQ_REGRESSION_QUERY)
    score_full = cosine(chunk.content, FAQ_REGRESSION_QUERY)

    # The premise: a bare title really is a far sharper key, so the brief's
    # diagnosis of the mechanism is correct even though its proposed cause is not.
    assert score_title_only - score_full > 0.15, (
        f"the title-only key is no longer clearly sharper ({score_title_only:.3f} "
        f"vs {score_full:.3f}) — the premise of this test has changed"
    )

    # The refutation: shrinking the chunk buys almost nothing, because the drop
    # has already happened by 15 body words.
    assert abs(score_15 - score_full) < 0.12, (
        f"title+15 words scores {score_15:.3f} against the full chunk's "
        f"{score_full:.3f} — a smaller chunk would now recover real score, so "
        f"the 'dilution is complete early' argument needs re-measuring"
    )
    assert score_15 < score_title_only - 0.10, (
        f"title+15 words scores {score_15:.3f}, not materially below the "
        f"title-only {score_title_only:.3f} — a small chunk WOULD keep the key"
    )


def test_duplicated_top_k_slots_are_a_symptom_not_the_cause(sweep):
    """Top-3 lists DO repeat a document — and deduplicating them fixes NOTHING.

    While diagnosing the misses, this looked like the obvious defect: several
    failing top-3 lists hold the same document twice, e.g. for "empezaste como
    frutero en mercadona no" the shipped configuration returns
    ``decisions/dejar-mercadona-para-dam.md`` at ranks 1 AND 3. Three slots are
    meant to be three candidate documents for the LLM to choose between.

    Measured, that is NOT what is costing the recall. Re-running every question
    with the top-k cut taken over DISTINCT sources instead of distinct chunks
    moves recall@3 from 0.653 to 0.653: the same 17 questions miss, and zero
    questions are rescued. The gold documents are not sitting at rank 4 waiting
    for a slot to free up; they are genuinely ranked low.

    So this is pinned as the negative result it is. Duplicated slots look like
    the bug, cost nothing measurable, and chasing them would be a change to
    retrieval semantics with no evidence behind it. Anyone who notices the
    repetition again should re-check this test before "fixing" it.

    The bar is deliberately loose — a guard on an observation, not a target.
    """
    rag = sweep.pipelines[CURRENT_CONFIG]

    def deduped(case, depth=20):
        seen, out = set(), []
        for chunk, _score in rag.retrieve(case.question, top_k=depth):
            key = _norm(chunk.source)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) == 3:
                break
        return out

    duplicated = 0
    rescued = 0
    for case in LABELLED_CASES:
        sources = [_norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)]
        if len(set(sources)) < len(sources):
            duplicated += 1
        plain = _rank(sources, frozenset({case.primary}))
        dedup = _rank(deduped(case), frozenset({case.primary}))
        if (plain is None or plain > 3) and dedup is not None and dedup <= 3:
            rescued += 1

    assert duplicated > 0, (
        "no top-3 list repeats a document any more — the observation this test "
        "records has changed; re-check whether deduplication now matters"
    )
    assert rescued == 0, (
        f"deduplicating top-k by source now rescues {rescued} question(s) that "
        f"the shipped configuration misses. That would make it a real lever, "
        f"worth its own change with its own measurement."
    )


# ── The table, re-runnable ───────────────────────────────────────────────────


def test_report_the_sweep(sweep, capsys):
    """Print the measured table. Asserts only that every cell was measured.

    The numbers live here, not in a comment, so the next person re-runs
    ``pytest tests/test_rag_chunk_size_sweep.py -s`` and compares against this
    output instead of re-arguing from memory. ``-s`` is required to see it.
    """
    header = (
        f"{'cfg':>9} {'chunks':>6} {'med_w':>6} {'max_w':>6} "
        f"{'R@1':>6} {'R@3':>6} {'R@5':>6} {'R@1a':>6} {'R@3a':>6} {'R@5a':>6} "
        f"{'MRR':>6} {'MRRa':>6} {'FAQ3':>6} {'OTH3':>6} "
        f"{'top1body':>8} {'subst':>6} {'thin':>6}"
    )
    rows = [header]
    for cfg in CONFIGS:
        m = sweep.metrics[cfg]
        rows.append(
            f"{m.label:>9} {m.n_chunks:>6} {m.median_chunk_words:>6.0f} {m.max_chunk_words:>6} "
            f"{m.recall1:>6.3f} {m.recall3:>6.3f} {m.recall5:>6.3f} "
            f"{m.recall1_any:>6.3f} {m.recall3_any:>6.3f} {m.recall5_any:>6.3f} "
            f"{m.mrr:>6.3f} {m.mrr_any:>6.3f} {m.faq_recall3:>6.3f} {m.other_recall3:>6.3f} "
            f"{m.median_top1_body_words:>8.0f} {m.substance_ok_rate:>6.3f} {m.thin_top1_rate:>6.3f}"
        )

    # Paired recall@3 difference against the current config, for the three
    # cells that are not byte-identical to it.
    base = _per_question_recall3(sweep.pipelines[CURRENT_CONFIG])
    rows.append("")
    rows.append("paired delta recall@3 vs 400/50 (primary gold), 95% bootstrap CI:")
    for cfg in CONFIGS:
        if cfg == CURRENT_CONFIG:
            continue
        label = f"  {cfg[0]}/{cfg[1]:<4}"
        if _signature(sweep, cfg) == _signature(sweep, CURRENT_CONFIG):
            rows.append(f"{label} identical chunk set to 400/50 — no difference exists")
            continue
        cand = _per_question_recall3(sweep.pipelines[cfg])
        lo, hi = _paired_bootstrap_ci(cand, base)
        rows.append(f"{label} {sum(cand) - sum(base):+d} questions  CI [{lo:+.3f}, {hi:+.3f}]")

    report = "\n".join(rows)
    with capsys.disabled():
        print("\n" + report + "\n")

    assert len(sweep.metrics) == len(CONFIGS)
    for m in sweep.metrics.values():
        assert 0.0 <= m.recall3 <= 1.0 and m.n_chunks > 0


def _signature(sweep: Sweep, cfg: Tuple[int, int]):
    return tuple((c.source, c.content) for c in sweep.pipelines[cfg].chunks)


# ── Controls: the measurement must not be measuring the wrong thing ──────────


def test_the_measurement_uses_the_real_embedder_not_the_tfidf_fallback(sweep):
    """A silent TF-IDF fallback would make every number below meaningless.

    ``RAGPipeline.initialize()`` falls back to TF-IDF on any import failure and
    logs a warning nobody reads in CI. TF-IDF shares no geometry with the
    sentence embedder the product uses, so a sweep run on it would be a
    confident, tidy, wrong answer.
    """
    for cfg, rag in sweep.pipelines.items():
        assert not rag._use_tfidf, f"{cfg} fell back to TF-IDF"
        assert rag.embedder is not None, f"{cfg} has no sentence embedder"
        assert len(rag.chunks) == sweep.metrics[cfg].n_chunks


def _live_recall3(rag: RAGPipeline) -> List[bool]:
    """Same 0/1 vector, measured through the production entry point.

    ``get_chunks_with_scores`` applies ``detect_doc_type`` and pre-filters by
    document type, which ``retrieve()`` does not. That filter is a real,
    separate mechanism which chunk size does not touch — so if the sweep's
    ranking only appears when the filter is skipped, the sweep measured a path
    the product never takes.
    """
    out = []
    for case in LABELLED_CASES:
        sources = [
            c["source"].replace("\\", "/")
            for c in rag.get_chunks_with_scores(case.question, top_k=3)
        ]
        rank = _rank(sources, frozenset({case.primary}))
        out.append(rank is not None and rank <= 3)
    return out


def test_conclusion_holds_on_the_production_retrieval_path(sweep):
    """No challenger may win by the margin on the live path either.

    Deliberately a weaker bar than the main decision test: this one asks only
    "does any configuration become clearly better once the doc_type pre-filter
    is included", not "is this the best configuration", so it cannot fail merely
    because the filter moves a couple of borderline questions.
    """
    base_live = _live_recall3(sweep.pipelines[CURRENT_CONFIG])
    winners = {}
    for cfg, rag in sweep.pipelines.items():
        if cfg == CURRENT_CONFIG:
            continue
        live = _live_recall3(rag)
        margin = sum(live) - sum(base_live)
        if margin >= len(LABELLED_CASES) * MIN_RECALL3_MARGIN:
            winners[cfg] = margin
    assert not winners, (
        f"on the production retrieval path these configs beat the current one by "
        f"more than the margin (wins in questions): {winners}. Re-run the whole "
        f"sweep before believing it — the main decision test measures "
        f"retrieve() without the doc_type pre-filter."
    )


def test_sweep_never_writes_the_repositorys_embedding_cache(sweep):
    """The harness must not be a second writer to a real cache.

    ``backend/.rag_cache/`` is gitignored, which makes it invisible to review,
    and ``test_lifespan_shutdown`` boots the app — which re-ingests at startup
    and rewrites it. A measurement run that also wrote there would quietly make
    every later run depend on which measurement ran last.
    """
    import json

    repo_cache = ROOT / "backend" / ".rag_cache" / "embeddings.json"
    for cfg, rag in sweep.pipelines.items():
        assert rag._cache_dir is None, f"{cfg} was given a cache dir: {rag._cache_dir}"
    if repo_cache.exists():
        meta = json.loads(repo_cache.read_text(encoding="utf-8"))
        # Whatever the repo's own suite last wrote, it must be internally
        # consistent: the recorded count has to match a real chunking of the
        # real corpus at the shipped constants. A mismatch would mean something
        # outside this harness wrote a cache it cannot account for.
        from backend.services.rag import CHUNK_FILTER_VERSION

        assert meta["chunk_filter_version"] == CHUNK_FILTER_VERSION, (
            f"repo cache was written at filter version {meta['chunk_filter_version']}, "
            f"current is {CHUNK_FILTER_VERSION}"
        )
        assert meta["chunk_count"] == sweep.metrics[CURRENT_CONFIG].n_chunks, (
            f"repo cache records {meta['chunk_count']} chunks but the corpus "
            f"chunks to {sweep.metrics[CURRENT_CONFIG].n_chunks} at "
            f"{CURRENT_CONFIG}"
        )
