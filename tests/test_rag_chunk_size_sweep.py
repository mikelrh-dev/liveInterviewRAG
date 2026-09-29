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
knob with very little surface area on a corpus whose longest section is 266
words, and a knob that barely moves is a knob whose apparent "winner" is noise.
``DECISION_RULE`` below states, in advance and in numbers, what evidence would
justify moving it; ``test_no_config_justifies_replacing_the_current_chunk_size``
applies that rule. If nothing clears the bar, the correct outcome is to keep 400
and say so.

THE CORPUS IS THE REPOSITORY'S OWN wiki/
----------------------------------------
46 tracked files, present on a clean clone and checked out by CI.

This file used to measure ``tests/fixtures/retrieval_corpus/`` instead — 42
"invented" pages — on the stated ground that the real wiki "is gitignored,
backed up to a private repository, and therefore absent from a clean clone".
None of that was true. The stand-in was also a structural clone of the real
wiki (same directories, 13 identical filenames, 9 of 11 FAQ slugs byte
-identical), and its pages were more findable than the real ones: recall@3 read
0.776 there against 0.653 here, and the substance control read 0.980 against
0.918.

The consequence was that this file's conclusion survived a corpus swap it was
never able to see. Re-measured on the real corpus, every cell reproduces the
original 769f30f table exactly — the numbers below are the real ones, and they
are the numbers the sweep originally reported before the stand-in was
introduced.

WHAT IS MEASURED
----------------
For every (chunk_size, chunk_overlap) cell, over the labelled questions whose
gold page this corpus actually serves (``_scored_cases`` -- 49 of them on the
author's machine, 41 on a clean clone, because four FAQ pages are on disk and
not in the index). Scoring all 49 on a clean clone would count 8 labels as
misses purely because their gold page does not exist.

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

THE SET IS THE ONE THE GUARD MEASURES
-------------------------------------
``LABELLED_CASES`` lives in ``tests/real_wiki.py`` and is the same set
``TestRetrievalRegressionGuard`` scores, so the two measurements are directly
comparable — not "comparable in direction", which is what the previous version
of this file said while the guard read a different corpus.

MEASURED RESULT: KEEP 400/50
----------------------------
Run this file to reproduce. Every cell of the grid, all figures measured on the
real ``wiki/`` on the author's machine -- 37 pages, the full 49-question
population -- with real ``all-MiniLM-L6-v2`` embeddings:

    cfg      chunks  R@1    R@3    R@5    MRR    FAQ@3  other@3  top1body  subst  thin
    120/24     137   0.531  0.633  0.673  0.591  0.579  0.667        33w  0.898  0.020
    200/40     127   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    300/60     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    400/50     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    400/80     125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000
    600/120    125   0.551  0.653  0.714  0.611  0.632  0.667        36w  0.918  0.000

On a CLEAN CLONE -- 33 pages, the 41-question population, which is what CI
measures -- the same grid puts 400/50 at recall@3 0.659 (27/41) and every cell
at or above 200 ties it exactly, 120/24 included. The conclusion is the same in
both populations and the two floors are within 0.003 of each other, which is the
sanity check that neither population is a weaker instrument.

Nothing beats the shipped value. No section in the corpus exceeds 266 words, so
every cell at or above 300 emits BYTE-IDENTICAL chunk sets: three of the six
cells are the same experiment and no trend can be read out of them. 200/40 is a
genuinely distinct cell and it TIES exactly (paired delta 0 questions, CI
[+0.000, +0.000]). 120/24 is one question worse on the full population and its
substance control gets measurably worse: 0.898 vs 0.918 of top-1 chunks carry a
full body, and 2.0% become a title or a title-plus-fragment — the exact
pre-H1-fix failure mode this file exists to prevent.

The hypothesis is refuted structurally, not just numerically — see
``test_the_faq_regression_page_is_untouched_by_the_entire_grid`` and
``test_shrinking_the_chunk_cannot_recover_the_title_only_retrieval_key``.

THREE FIGURES FROM THE ORIGINAL BRIEF THAT DID NOT SURVIVE MEASUREMENT
-----------------------------------------------------------------------
1. "max 210" was wrong even then: the corpus's longest chunk is 266 words
   (``skills/backend.md``), not 210.
2. "the constants are dead" was right for CHUNK_SIZE and CHUNK_OVERLAP, and the
   claim about ``threshold=0.3`` that rode along with it ("no chunk is ever
   filtered out on score") was an inference from a top-1 statistic, not a
   measurement of the filter. On the real corpus the 0.3 default discards 1801
   of 6125 (question, chunk) pairs — 29.4% of the candidate pool. It happens not
   to change any top-3 here, because the thinnest question still has 3
   survivors. See ``tests/test_rag.py::TestRetrievalThresholdIsHonest``.
3. Duplicated documents inside a single top-3 look like the cause of the misses
   and are not: deduplicating by source rescues 0 questions. See
   ``test_duplicated_top_k_slots_are_a_symptom_not_the_cause``.
"""

import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import numpy as np
import pytest

from backend.services.rag import RAGPipeline

# The corpus and the labelled set live in ``tests/real_wiki.py`` so the
# retrieval tests and this sweep measure the same thing.
# CORPUS: the real ``wiki/`` — 37 loaded pages, 125 chunks at 400/50 — which is
# 46 TRACKED files, present on a clean clone and checked out by CI. This file
# used to measure ``tests/fixtures/retrieval_corpus/`` instead, on the stated
# ground that the real wiki "is gitignored, private, and absent from a clean
# clone". All three were false, and the stand-in was a structural clone of the
# very corpus it was standing in for. Re-importing the historical name keeps
# every existing ``from tests.test_rag_chunk_size_sweep import LABELLED_CASES``
# working.
from tests.real_wiki import (
    LABELLED_CASES,
    Case,
    FAQ,
    NARRATIVE,
    guard_blocker,
    load_documents,
    measurement_for,
    resolved_cases,
)

ROOT = Path(__file__).resolve().parent.parent


#: The population this run scores. Resolved once per process because every
#: metric below is a ratio over it, and a metric whose denominator silently
#: changes between the report and the floors is not a measurement.
_SCORED: List[Case] = []


def _scored_cases(documents=None) -> List[Case]:
    """The labelled questions whose gold page this corpus actually serves.

    41 of the 49 on a clean clone: four FAQ pages are on disk and not in the
    index. Scoring all 49 there would count 8 labels as misses purely because
    their gold page does not exist -- a "regression" that is a missing file.
    """
    global _SCORED
    if not _SCORED:
        if documents is None:
            documents = load_documents()
        _SCORED = resolved_cases(documents)
    return _SCORED

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
MIN_RECALL3_MARGIN = 3 / len(_scored_cases())   # 3 questions
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


def _norm_documents(documents: Dict[str, str]) -> Dict[str, str]:
    """``documents`` re-keyed to forward slashes, whatever the platform emits.

    The production loader keys by ``str(Path.relative_to(...))``, which is
    backslash-separated on Windows and slash-separated on POSIX -- the same
    convention ``tests/test_rag.py`` normalises around, and the same one the
    labelled cases in ``tests/real_wiki.py`` are already written in. Every
    lookup in this module goes through here so that no test in it has to know
    which platform it is running on.

    The reason this helper exists: the one test that did NOT use it built a
    backslash key by hand, which happened to be right on Windows and raised
    ``KeyError`` on every POSIX checkout, including ``ubuntu-latest``.
    """
    return {_norm(key): value for key, value in documents.items()}


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
    """The real ``wiki/``, loaded through the production loader.

    Refuses rather than measures a different corpus: the floors in this file are
    derived from these 49 questions on these pages, and scoring a subset would
    apply them to a population they were not measured on. The skip is not the
    signal — ``tests/test_rag.py::TestTheRetrievalGuardActuallyRan`` fails on
    the same state, so a run with no corpus is red rather than green.
    """
    blocker = guard_blocker()
    if blocker:
        pytest.skip(
            "\n!! RETRIEVAL SWEEP DID NOT RUN -- THIS IS NOT A PASS !!\n"
            f"{blocker}\n"
        )
    return load_documents()


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

    THE MODEL IS THE SHIPPED DEFAULT, NOT A LITERAL
    ----------------------------------------------
    This read ``SentenceTransformer("all-MiniLM-L6-v2")`` and constructed every
    pipeline with its own default, which happened to be the same string. The two
    agreed by coincidence, and when the default moved to
    ``paraphrase-multilingual-MiniLM-L12-v2`` on 2026-09-29 only the second one
    moved: this sweep kept measuring the old vector space and reported recall@3
    0.755 while the pipeline it was supposed to be characterising returned
    0.8163 on the same corpus. A measurement harness that measures a different
    retriever than the one it is characterising is worse than no harness,
    because its numbers still look authoritative.

    So the model is now read from the constructor's own default, the same value
    the app will load, and the pipeline below is built without an override. If
    the two ever disagree again, every number in this file is wrong and the
    guard on the floor in ``real_wiki.py`` will say so.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(RAGPipeline()._embedding_model)
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
    n = len(_scored_cases())

    ranks_primary: List[Optional[int]] = []
    ranks_any: List[Optional[int]] = []
    body_words: List[int] = []

    for case in _scored_cases():
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
            r for c, r in zip(_scored_cases(), ranks_primary) if c.doc_class == doc_class
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
    for case in _scored_cases():
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

    The real corpus's longest section is 266 words, so every cell at or above
    300 emits the SAME chunks, byte for byte, and overlap is inert above that
    threshold. That is the single most important fact about this sweep: three
    of the six cells are one experiment repeated, so they carry no information
    at all, and no "trend" can be read out of them.

    The 200 cell is NOT inert here. It is a distinct experiment, and it ties
    the current configuration exactly (paired delta 0 questions). That is why
    only three cells collapse rather than five.

    This test is a property of the CORPUS, so if a page grows a section long
    enough for a smaller threshold to bind, it fails and the grid needs
    rethinking.
    """
    signatures = {
        cfg: tuple((c.source, c.content) for c in rag.chunks)
        for cfg, rag in sweep.pipelines.items()
    }
    current = signatures[CURRENT_CONFIG]
    inert = sorted(cfg for cfg, sig in signatures.items() if sig == current)
    assert inert == [(300, 60), (400, 50), (400, 80), (600, 120)], (
        f"expected every cell at or above 300 words to be identical to the "
        f"current config on this corpus, but these were: {inert}. The longest "
        f"chunk is {sweep.metrics[CURRENT_CONFIG].max_chunk_words} words."
    )
    assert sweep.metrics[CURRENT_CONFIG].max_chunk_words < 300, (
        "the longest chunk reached the smallest inert cell's threshold, so "
        f"{inert} is no longer a set of duplicates"
    )
    assert (200, 40) not in inert, (
        "the 200 cell collapsed into the current configuration, which means the "
        "grid now has fewer distinct experiments than the docstring claims"
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

    REAL-CORPUS, and every value restored. They were measured on the owner's
    ``wiki/`` at the time and then re-pinned onto
    ``tests/fixtures/retrieval_corpus/``, an invented stand-in that turned out
    to be a structural clone of that very wiki. They are back on the real
    corpus.

    RE-MEASURED 2026-09-29, under ``paraphrase-multilingual-MiniLM-L12-v2`` with
    the page-identity prefix and the one-chunk-per-page cut: 0.816 recall@3,
    0.735 recall@1, 0.793 MRR, 124 chunks. The previous set — 0.653 / 0.551 /
    0.611 on 125 chunks — belongs to the English embedder and is kept here only
    so the movement is legible. Two things moved together and neither can be
    attributed to the other from this file: the embedder changed, and this
    sweep was for a long time loading the OLD embedder from a literal of its
    own, so its first 2026-09-29 run read 0.755 while the pipeline it was
    characterising returned 0.8163 on the same corpus. Both now load
    ``RAGPipeline()``'s own default.

    The tolerance is unchanged at one question (1/49 = 0.0204, rounded to
    0.021), because the resolution limit is a function of n, not of which
    corpus supplies the questions.

    Nothing here was loosened. The substance control is TIGHTER on the real
    corpus than it was on the stand-in: 0.918 of top-1 chunks carry a full
    body, against 0.980 of an invented corpus whose pages were written to be
    findable.

    Flipping ``test_no_config_justifies_replacing_the_current_chunk_size`` to
    failing is the signal that these must be re-baselined in the same change.
    """
    current = sweep.metrics[CURRENT_CONFIG]
    measurement = measurement_for(sweep.documents)
    assert measurement is not None, (
        "the corpus resolves a population this file has no floor for; see "
        "tests/real_wiki.py"
    )
    assert current.recall3 == pytest.approx(measurement.recall3, abs=0.021), (
        f"recall@3 at {CURRENT_CONFIG} is {current.recall3:.3f}, measured "
        f"{measurement.recall3:.3f} ({measurement.hits}/{measurement.questions}, "
        f"population {measurement.name!r}) on the real wiki. A change moved it; "
        f"re-baseline deliberately or revert."
    )
    assert current.recall1 == pytest.approx(0.735, abs=0.06), (
        f"recall@1 is {current.recall1:.3f}; measured 0.735 on the full "
        f"population. The tolerance is wider than elsewhere because recall@1 is "
        f"the noisiest number in the grid and this is a floor, not a "
        f"discriminator."
    )
    assert current.mrr == pytest.approx(0.793, abs=0.06), (
        f"MRR is {current.mrr:.3f}; measured 0.793 on the full population."
    )
    # The control, and the reason the H1 fix exists: the top-1 context is an
    # answer, not a restatement of the interviewer's own question. On the real
    # corpus this is 0.918, not the 0.980 the stand-in read -- the invented
    # pages were more findable than the real ones, which is exactly why the
    # stand-in was the wrong instrument.
    assert current.substance_ok_rate >= 0.88, (
        f"only {current.substance_ok_rate:.1%} of top-1 chunks carry a body of "
        f">= {SUBSTANCE_WORD_THRESHOLD} words — the LLM is getting titles back. "
        f"Measured on the real corpus: 0.918 (full), 0.890 (reduced)."
    )
    assert current.thin_top1_rate == 0.0, (
        f"{current.thin_top1_rate:.1%} of top-1 chunks are a title or a title "
        f"plus a fragment (< {THIN_BODY_THRESHOLD} body words)"
    )
    assert current.median_top1_body_words >= 30, (
        f"median top-1 body is {current.median_top1_body_words:.0f} words, "
        f"measured 36 on the real corpus (45 on the stand-in this file used to "
        f"measure, whose pages were easier to find)"
    )


# ── Why the answer is "no change": the two structural refutations ────────────
#
# The brief behind this sweep is that CHUNK_SIZE is what dilutes a merged FAQ
# title, and that a smaller size might keep the title's discriminative power
# while retaining the answer. Both of the measurements below were taken AFTER
# the sweep, and both say the hypothesis cannot hold on this corpus.


FAQ_REGRESSION_PAGE = "faq/presentacion-30-segundos.md"
FAQ_REGRESSION_QUERY = "cuentame sobre ti en treinta segundos"


def test_document_keys_are_platform_independent(real_wiki_documents):
    """No lookup in this module may depend on the host's path separator.

    The production loader keys documents by ``str(Path.relative_to(...))``:
    backslashes on Windows, forward slashes on POSIX. The labelled cases in
    ``tests/real_wiki.py`` and the chunk ``source`` values here are all
    written with forward slashes, so a raw ``documents[...]`` lookup is only
    correct on whichever platform happens to match.

    This test was added after exactly that: one test built a backslash key by
    hand, passed on Windows, and raised ``KeyError`` on ``ubuntu-latest`` --
    where the whole Python suite would have died before a single assertion ran.

    What it actually guards, precisely: ``_norm_documents`` (a change to it
    that stopped normalising, or started dropping keys, fails here) and the
    claim that every labelled page is reachable through it. What it does NOT
    guard, stated plainly rather than implied: a future direct
    ``real_wiki_documents["a/b.md"]`` somewhere in this module. That would
    still be a platform-dependent lookup, and only that call site would fail.
    The last assertion below exists to keep the platform dependency visible
    rather than assumed away.
    """
    normalised = _norm_documents(real_wiki_documents)

    # Lossless: normalisation re-keys, it never drops or merges a document.
    assert len(normalised) == len(real_wiki_documents), (
        f"normalising collapsed {len(real_wiki_documents)} documents into "
        f"{len(normalised)} -- two distinct keys now collide"
    )

    # Every page the labelled cases are written against is reachable under the
    # forward-slash form they use, on either platform's separator.
    for case in _scored_cases():
        for page in (case.primary, *sorted(case.also)):
            assert page in normalised, f"{page} missing after normalisation"

    # And the page this module singles out by name is reachable too.
    assert FAQ_REGRESSION_PAGE in normalised

    # The loader's keys follow os.sep, so a forward-slash constant indexes the
    # RAW dict on POSIX and raises KeyError on Windows. Pinning that here is
    # what keeps "just index the raw dict" visibly wrong on the platform where
    # it happens to be right.
    assert (FAQ_REGRESSION_PAGE in real_wiki_documents) is (os.sep == "/"), (
        "document keys no longer follow os.sep; re-check every lookup in this "
        "module before assuming which separator the loader emits"
    )


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

    metadata, body = parse_frontmatter(_norm_documents(real_wiki_documents)[FAQ_REGRESSION_PAGE])
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


def test_duplicated_top_k_slots_are_now_impossible_and_that_is_worth_a_question(sweep):
    """The negative result that argued against dedup is no longer the truth.

    THIS TEST ONCE RECORDED THE OPPOSITE. Read the history, because the reason
    it was written is the reason it had to be rewritten.

    Top-3 lists used to repeat a document: for "empezaste como frutero en
    mercadona no" the shipped configuration returned
    ``dejar-mercadona-para-dam.md`` at ranks 1 AND 3, so the model was handed
    three fragments of one story and no fact. It looked like the defect. It was
    measured and it was NOT what cost the recall: re-running every question with
    the cut taken over DISTINCT sources moved recall@3 from 0.653 to 0.653 —
    the same 17 questions missed, zero were rescued. So it was pinned as a
    negative result, and anyone who noticed the repetition again was told to
    re-check this test before "fixing" it.

    THAT ADVICE WAS RIGHT AND THE CONCLUSION WAS NOT, and the reason is visible
    only now. The measurement was taken with ``all-MiniLM-L6-v2``. The
    multilingual embedder changes which questions are ranking failures: of the
    questions that miss, fewer have their gold page stranded at rank 4+ waiting
    for a slot, and more are crowded out by siblings of the page already in the
    list. Re-measured on the current configuration, deduplicating the cut
    rescues a real question, and it is the one the old test used as its example.

    So this is now the guard for the property the pipeline depends on, in two
    halves:

      * ``retrieve()`` at ``top_k=3`` NEVER returns two chunks from the same
        document. Not "usually" — never, on the labelled set.
      * deduplication is not cosmetic. The count of rescued questions is
        compared against a measured value, so a future change that quietly
        restores the repetition fails here instead of being argued about.

    The un-deduplicated cut is obtained by neutralising
    ``_one_chunk_per_page`` for the duration of the comparison rather than by
    re-implementing the ranking, so the two sides differ by exactly the one
    thing being measured.
    """
    rag = sweep.pipelines[CURRENT_CONFIG]
    cases = _scored_cases()

    duplicated = [
        case.question
        for case in cases
        if (sources := [_norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)])
        and len(set(sources)) < len(sources)
    ]
    assert not duplicated, (
        f"{len(duplicated)} top-3 list(s) repeat a document: {duplicated[:5]}. "
        f"The per-page cut in RAGPipeline._one_chunk_per_page is the only thing "
        f"standing between three slots and three fragments of one story."
    )

    # Measure what the cut buys, by turning it off for one comparison and
    # turning it back on for the other. The restore has to happen BETWEEN the
    # two retrieves, not after both: leaving it off for the whole loop compares
    # the un-deduplicated list with itself and rescues exactly nothing, which
    # is indistinguishable from a cut that buys nothing.
    #
    # NOTE: the restore re-wraps in `staticmethod`. `_one_chunk_per_page` is a
    # staticmethod on the class, so reading it off the class hands back the
    # plain function; putting THAT back as a class attribute rebinds it as an
    # instance method and `retrieve` then fails for every test after this one.
    original = RAGPipeline.__dict__["_one_chunk_per_page"]
    rescued = 0
    try:
        for case in cases:
            RAGPipeline._one_chunk_per_page = staticmethod(lambda scores, top_k: scores[:top_k])
            raw = [_norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)]
            RAGPipeline._one_chunk_per_page = original

            deduped = [_norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)]

            raw_rank = _rank(raw, frozenset({case.primary}))
            new_rank = _rank(deduped, frozenset({case.primary}))
            if (raw_rank is None or raw_rank > 3) and new_rank is not None and new_rank <= 3:
                rescued += 1
    finally:
        RAGPipeline._one_chunk_per_page = original

    assert rescued == 1, (
        f"the per-page cut now rescues {rescued} question(s), measured at 1 on "
        f"2026-09-29. More means the ranking shifted and this floor wants "
        f"re-deriving; fewer means the cut stopped buying anything and the cost "
        f"of a top_k slot has to be re-argued."
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
    for case in _scored_cases():
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
        if margin >= len(_scored_cases()) * MIN_RECALL3_MARGIN:
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

    The second half used to assert that whatever the repo's own suite last wrote
    into that cache matches this sweep's chunk count, and it was dropped when
    the sweep was moved onto an invented stand-in: comparing a synthetic corpus
    to a real one and calling the difference a defect is worse than not
    checking. Both now come from the same corpus, but a chunk-COUNT comparison
    is still not added back, because the cache on disk was written by whatever
    ran last on this machine at whatever ``chunk_size`` it was configured with.
    The version guard is the part that is about the cache's own consistency
    rather than about which corpus built it, and it is the part that is safe to
    assert when the cache may not exist at all.
    """
    import json

    repo_cache = ROOT / "backend" / ".rag_cache" / "embeddings.json"
    for cfg, rag in sweep.pipelines.items():
        assert rag._cache_dir is None, f"{cfg} was given a cache dir: {rag._cache_dir}"
    if repo_cache.exists():
        meta = json.loads(repo_cache.read_text(encoding="utf-8"))
        from backend.services.rag import CHUNK_FILTER_VERSION

        assert meta["chunk_filter_version"] == CHUNK_FILTER_VERSION, (
            f"repo cache was written at filter version {meta['chunk_filter_version']}, "
            f"current is {CHUNK_FILTER_VERSION}"
        )
