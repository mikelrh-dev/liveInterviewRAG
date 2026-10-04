"""The lexical rescue in ``RAGPipeline._lexical_rescue``, held to what it costs.

THE GAP THIS FILE FILLS
-----------------------
Every recall figure this repository published was measured at ``top_k`` equal to
the population size -- ``tests/test_recall_claims.py:260`` and
``tests/test_committed_corpus_figures.py:188`` both do it, and the reason is
sound for what they ask (a top_k wide enough to hold every page answers "is the
gold page anywhere in the ranking"). It is the wrong question for the number an
interviewer experiences, which is what ``backend/config.py`` ships as
``RAG_TOP_K`` and what ``RAGPipeline.retrieve`` is given in production.

So the corpus had no published figure at ``top_k=3`` at all. A change that
improved production retrieval and left every ``top_k = questions`` measurement
untouched would have been invisible to every published claim and to every guard
derived from one. ``tests/real_wiki.py::CommentFigures.recall3_at_top3`` is the
figure, and this file is what makes it mean something.

WHAT IS AND IS NOT PINNED
-------------------------
Pinned: the top_k=3 recall figure for THIS checkout's population; the structural
invariant that the rescue only ever changes the LAST rank; the exact set of
questions it costs and gains; that the cosine filter's published counts are
untouched; and that a restored embedding cache retrieves exactly what a
recomputed one does.

Not pinned: that the rescue is lossless. It is not, and pretending otherwise was
the defect -- see ``tests/lexical_rescue_losses.py`` for the arithmetic that
rules out every threshold that would have made it so, on both populations.

THE COST SET IS THE INTERESTING ASSERTION
-----------------------------------------
``TestTheRescueCostsExactlyWhatIsRecorded`` asserts the precise questions the
rescue costs, by name. A weaker version of this file would assert ``losses <= 1``
or ``net >= 0``, both of which stay green while the specific question that pays
for the feature changes underneath a reader who assumed it was the one they had
approved. The trade is +4 questions on each population, so a growing cost set is
never the cheap option; if it grows, the honest move is to re-derive it here and
say so, not to widen an assertion until it fits.
"""

from pathlib import Path

import pytest

from backend.services.rag import RAGPipeline, bm25_tokens
from tests.lexical_rescue_losses import (
    RECOVERED_TARGETS,
    RESCUE_COSTS,
    RESCUE_COST_PAGES,
    RESCUE_GAINS,
    TARGET_QUESTIONS,
)
from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COMMENT_FIGURES,
    EMBEDDING_MODEL,
    comment_figures_for,
    load_documents,
    resolved_cases,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "backend" / "config.py"
RAG_PY = REPO_ROOT / "backend" / "services" / "rag.py"

#: Located by its own subject rather than by line number, like the anchors in
#: ``tests/test_recall_claims.py``, so an unrelated edit above the block does not
#: silently stop this file from reading the comment it exists to check.
EMBEDDER_ANCHOR = "The default embedder is"
THRESHOLD_ANCHOR = "Minimum cosine"


def _block(path: Path, anchor: str) -> str:
    """The contiguous ``#`` comment block in ``path`` mentioning ``anchor``.

    Copied from ``tests/test_recall_claims.py`` rather than imported, because
    that module's helper is private to it and importing a private name across
    test modules is how two guards end up sharing a bug. The cost of the
    duplication is four lines; the cost of the coupling is a silent one.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            current.append(line)
        else:
            if current:
                blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    for block in blocks:
        if anchor in "\n".join(block):
            return "\n".join(block)

    raise AssertionError(
        f"no comment block in {path.name} mentions {anchor!r}; the "
        "justification this test guards has been deleted or moved"
    )


def _norm(source: str) -> str:
    return source.replace("\\", "/")


@pytest.fixture(scope="module")
def figures():
    documents = load_documents()
    population = len(resolved_cases(documents))
    recorded = comment_figures_for(documents)
    assert recorded is not None, (
        f"this checkout resolves {population} labelled questions, a population "
        "with no recorded comment figures. The rows live in "
        "tests/real_wiki.py::COMMENT_FIGURES; re-measure on the population that "
        "remains and add a row rather than pointing this guard at another one's "
        "numbers."
    )
    assert recorded.questions == population, (
        f"the row resolved for {recorded.population} is calibrated for "
        f"{recorded.questions} questions but this checkout resolves {population}."
    )
    return recorded


@pytest.fixture(scope="module")
def pipeline():
    """The real wiki through the production path, no cache written anywhere."""
    rag = RAGPipeline(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        cache_dir=None,
        embedding_model=EMBEDDING_MODEL,
    )
    rag.ingest_documents(load_documents())
    return rag


@pytest.fixture(scope="module")
def cases():
    return resolved_cases(load_documents())


def _rank_at(case, sources):
    """1-based rank of the case's PRIMARY gold page, or a rank past the end.

    Strict on PRIMARY, for the reason ``tests/test_rag.py::_strict_hit`` gives:
    one labelled question has two pages that genuinely answer it, and a lenient
    rule lets that pair mask the FAQ page losing its own retrieval key.

    The sentinel is ``len(sources) + 1`` rather than ``None`` so the caller can
    compare ranks with ``<=`` without a branch -- "not found" has to sort worse
    than anything actually found, including the last slot.
    """
    if case.primary in sources:
        return sources.index(case.primary) + 1
    return len(sources) + 1


def _sources(rag, question, top_k, rescue):
    """The pages ``retrieve()`` returns, with the rescue on or off.

    Turned off by replacing the method on the CLASS, which is the only handle a
    test has on it, and restored in a ``finally`` because a leaked replacement
    would make every later test in the session measure a dense-only pipeline and
    pass for the wrong reason.
    """
    original = RAGPipeline.__dict__["_lexical_rescue"]
    if not rescue:
        RAGPipeline._lexical_rescue = lambda self, q, d, k: d
    try:
        return [_norm(c.source) for c, _ in rag.retrieve(question, top_k=top_k)]
    finally:
        RAGPipeline._lexical_rescue = original


class TestTheRescueOnlyAdds:
    """The structural invariant: only the LAST rank can change."""

    def test_the_kept_prefix_is_never_reordered_or_dropped(self, pipeline, cases):
        """``result[:top_k - 1]`` must equal ``dense[:top_k - 1]``, exactly.

        Stated as an equality over the whole result list rather than as "the
        gold page is still there", because a rescue that re-sorted the dense
        pages could pass a recall assertion while quietly changing which
        evidence the model reads first. Recall does not notice that; this does.
        """
        top_k = 3
        for case in cases:
            dense = _sources(pipeline, case.question, top_k, rescue=False)
            hybrid = _sources(pipeline, case.question, top_k, rescue=True)
            assert hybrid[: top_k - 1] == dense[: top_k - 1], (
                f"{case.question!r}\n  dense  {dense}\n  hybrid {hybrid}\n"
                "The rescue is supposed to ADD a page at the last rank and leave "
                "everything above it exactly as the cosine side ordered it. A "
                "difference here means the rescue started re-ranking."
            )

    def test_the_result_never_repeats_a_page(self, pipeline, cases):
        """One page per slot survives the second ranker, not just the first."""
        for case in cases:
            sources = _sources(pipeline, case.question, top_k=3, rescue=True)
            assert len(set(sources)) == len(sources), (
                f"{case.question!r} returned {sources}, which repeats a page. "
                "RAGPipeline._one_chunk_per_page is applied to the lexical "
                "ranking too; if this fires, the rescue is spending a slot on a "
                "fragment of a page already in the answer."
            )

    def test_a_query_with_no_lexical_evidence_injects_nothing(self, pipeline):
        """A query whose terms appear nowhere must not have a page invented.

        ``Bm25Index.score`` returns all zeros here, so sorting them returns
        CORPUS ORDER and its first element is whichever page happens to chunk
        first. That is not a weak match, it is an arbitrary page wearing a
        score, and it would displace the third-best cosine match to say so. The
        rescue has to notice it has no opinion and decline.

        The premise is asserted rather than assumed. An earlier version of this
        test used a query of pure Spanish function words, which relied on the
        stopword list -- and that list was removed after being measured to change
        nothing, so the premise quietly became false and the test was passing for
        the wrong reason until it asserted its own precondition.
        """
        query = "zorzorro quixotico wuix"
        assert bm25_tokens(query) == ["zorzorro", "quixotico", "wuix"], (
            "this query was chosen because its terms are nonsense and appear "
            f"nowhere, but bm25_tokens returned {bm25_tokens(query)} -- either "
            "the analyzer changed or the corpus grew those words, and the "
            "premise of this test is no longer true."
        )
        index = pipeline._lexical_index
        assert float(index.score(query).max()) == 0.0, (
            "this query was chosen because it matches no chunk, but BM25 scored "
            f"it {index.score(query).max()}. The corpus now contains one of "
            "those words; pick another nonsense query."
        )

        dense = _sources(pipeline, query, 3, rescue=False)
        hybrid = _sources(pipeline, query, 3, rescue=True)
        if len(dense) == 3:
            assert hybrid == dense, (
                f"a query with no lexical evidence changed the answer: "
                f"{dense} -> {hybrid}. The rescue should decline when BM25 has "
                "nothing to rank on."
            )

    def test_the_rescued_page_is_never_a_repeat_of_a_kept_one(self, pipeline, cases):
        """The second ranker draws from pages the first one did not surface."""
        for case in cases:
            sources = _sources(pipeline, question=case.question, top_k=3, rescue=True)
            assert len(sources) == len(set(sources)), (
                f"{case.question!r} repeats a page: {sources}"
            )


class TestTheRescueCostsExactlyWhatIsRecorded:
    """The trade, by name, per population."""

    def test_the_cost_set_is_exactly_what_is_published(self, pipeline, cases, figures):
        population = figures.population
        dense, hybrid = set(), set()
        for case in cases:
            before = _sources(pipeline, case.question, 3, rescue=False)
            after = _sources(pipeline, case.question, 3, rescue=True)
            if case.primary in before and case.primary not in after:
                dense.add(case.question)
            if case.primary not in before and case.primary in after:
                hybrid.add(case.question)

        assert dense == set(RESCUE_COSTS[population]), (
            f"on the {population} population the rescue costs {sorted(dense)}, "
            f"and tests/lexical_rescue_losses.py records "
            f"{sorted(RESCUE_COSTS[population])}.\n"
            "Either the corpus or the retriever moved. If the retriever moved, "
            "re-derive the set there and say what changed -- do not widen this "
            "assertion, because the published set is what a reader approved."
        )
        assert hybrid == set(RESCUE_GAINS[population]), (
            f"on the {population} population the rescue gains {sorted(hybrid)}, "
            f"and tests/lexical_rescue_losses.py records "
            f"{sorted(RESCUE_GAINS[population])}. A shrinking gain set is a "
            "regression; a growing one is an improvement worth publishing."
        )

    def test_each_cost_names_the_page_it_loses(self, pipeline, figures):
        """A cost recorded as a question alone is half a record."""
        population = figures.population
        for question, page in RESCUE_COST_PAGES[population].items():
            sources = _sources(pipeline, question, 3, rescue=True)
            assert page not in sources, (
                f"{question!r} no longer loses {page!r}, so the {population} cost "
                "set has shrunk. Re-derive tests/lexical_rescue_losses.py."
            )

    def test_the_net_is_a_gain_on_this_population(self, pipeline, cases, figures):
        population = figures.population
        before = sum(
            case.primary in _sources(pipeline, case.question, 3, rescue=False)
            for case in cases
        )
        after = sum(
            case.primary in _sources(pipeline, case.question, 3, rescue=True)
            for case in cases
        )
        assert after > before, (
            f"the rescue made top_k=3 WORSE on the {population} population: "
            f"{before}/{len(cases)} -> {after}/{len(cases)}. It costs "
            f"{len(RESCUE_COSTS[population])} question(s) and must gain more."
        )

    def test_two_of_the_three_target_questions_are_recovered(self, pipeline):
        """The three questions this was built for, checked individually.

        Not as "three of three": two of the three are recoverable with one slot
        and the third is not, because its gold page scores 0.1703 against a 0.25
        threshold and is therefore not in the candidate set at all. Asserting
        three would be asserting something false; asserting nothing would let
        the feature rot back into a no-op without a single test turning red --
        which is what it was, measured, before the gate was changed.
        """
        recovered = set()
        for question, page, _cosine in TARGET_QUESTIONS:
            if page in _sources(pipeline, question, 3, rescue=True):
                recovered.add(question)

        assert recovered == set(RECOVERED_TARGETS), (
            f"the rescue recovers {sorted(recovered)} of the three target "
            f"questions; tests/lexical_rescue_losses.py records "
            f"{sorted(RECOVERED_TARGETS)}.\n"
            "Fewer is a regression. More means a second lexical slot or a "
            "fusion has been implemented -- update the record and say so."
        )


class TestTheTop3FigureIsTheMeasuredOne:
    """The published top_k=3 figure, bound to a live measurement."""

    def test_recall3_at_top3_matches_the_pipeline(self, pipeline, cases, figures):
        hits = sum(
            case.primary in _sources(pipeline, case.question, 3, rescue=True)
            for case in cases
        )
        measured = hits / len(cases)
        assert figures.hits3_at_top3 == hits, (
            f"CommentFigures({figures.population}).hits3_at_top3 is "
            f"{figures.hits3_at_top3} but the pipeline serves the gold page for "
            f"{hits} of {len(cases)} questions at top_k=3."
        )
        assert figures.recall3_at_top3 == pytest.approx(measured, abs=0.0005), (
            f"CommentFigures({figures.population}).recall3_at_top3 is "
            f"{figures.recall3_at_top3:.4f}; measured {measured:.4f}."
        )

    def test_the_wide_measurement_is_untouched_by_the_rescue(
        self, pipeline, cases, figures
    ):
        """``recall3`` at top_k=questions must not have moved.

        The rescue only fires when the per-page cut bound, which cannot happen at
        a top_k wider than the corpus. So the wide figures and the corpus-shape
        counts in ``COMMENT_FIGURES`` are still true -- and if this ever fails,
        the rescue has started reaching pages the filter rejected at a width
        where the filter's published counts are measured.
        """
        wide = len(cases)
        before = sum(_rank_at(case, _sources(
            pipeline, case.question, wide, rescue=False)) <= 3 for case in cases)
        after = sum(_rank_at(case, _sources(
            pipeline, case.question, wide, rescue=True)) <= 3 for case in cases)
        assert after == before, (
            f"at top_k={wide} the rescue changed strict recall@3 from "
            f"{before}/{len(cases)} to {after}/{len(cases)}. It should not fire "
            "at all at that width: every page fits, so the cut never bound."
        )
        assert figures.recall3 == pytest.approx(before / len(cases), abs=0.0005), (
            f"CommentFigures({figures.population}).recall3 is "
            f"{figures.recall3:.4f}; the pipeline measures "
            f"{before / len(cases):.4f} at top_k={wide}."
        )


class TestTheFilterCountsAreNotWidenedByTheRescue:
    def test_the_published_discarded_counts_still_hold(self, pipeline, cases, figures):
        """959 of 1813 and 722 of 1353 must still be what ``retrieve()`` does.

        These are counts of RESULTS at ``top_k`` twice the chunk count, and they
        are the figures ``backend/services/rag.py`` publishes about the cosine
        filter. The rescue draws from all chunks rather than the filter's
        survivors, so the only thing keeping these numbers true is that the gate
        cannot fire at that width -- which is asserted here rather than assumed.
        """
        deeper = len(pipeline.chunks) * 2
        shipped = pipeline.threshold

        def total(threshold):
            pipeline.threshold = threshold
            try:
                return sum(
                    len(pipeline.retrieve(case.question, top_k=deeper))
                    for case in cases
                )
            finally:
                pipeline.threshold = shipped

        filtered, unfiltered = total(shipped), total(-1.0)
        assert filtered == figures.filtered, (
            f"the {figures.population} filter is said to return {figures.filtered} "
            f"results at top_k={deeper}; measured {filtered}. If the rescue "
            "started firing here it would be re-admitting pages this count says "
            "the filter discarded."
        )
        assert unfiltered == figures.unfiltered, (
            f"the {figures.population} unfiltered run is said to return "
            f"{figures.unfiltered}; measured {unfiltered}."
        )


class TestTheGateIsWhatTheMeasurementSays:
    """The two conditions, each measured rather than asserted in prose."""

    def test_nothing_fires_when_the_pages_fit_in_top_k(self, pipeline, cases):
        """A corpus-wide ``top_k`` is the shape the published counts use."""
        deeper = len(pipeline.chunks) * 2
        for case in cases:
            dense = _sources(pipeline, case.question, deeper, rescue=False)
            hybrid = _sources(pipeline, case.question, deeper, rescue=True)
            assert hybrid == dense, (
                f"{case.question!r} changed at top_k={deeper}, where the cut "
                f"cannot bind. {dense} -> {hybrid}"
            )

    def test_it_does_not_fire_below_min_rescue_top_k(self, pipeline, cases):
        """``top_k=2`` is what the context panel asks for, and it is excluded.

        Measured with the rescue forced on, top_k=2 would be 39/49 -> 41/49: five
        questions gained and three lost. So this floor is NOT holding back a
        regression -- it is declining a net gain, because one slot in two makes
        the context half lexical and changes the evidence on 8 of 49 questions
        where one slot in three changes it on 6. ``top_k=2`` is the panel's
        width, not the answer's; the answer context is ``config.RAG_TOP_K``.

        The assertion pins the DECISION so that a future edit cannot quietly turn
        a chosen share into an accident, and its message points at the
        measurement rather than at a rule that was never true.
        """
        assert RAGPipeline.MIN_RESCUE_TOP_K == 3, (
            "MIN_RESCUE_TOP_K is the width below which the rescue declines to "
            "run. At top_k=2 it was measured as a net gain (39/49 -> 41/49, five "
            "gained and three lost), so this is a chosen share and not a "
            "regression guard: if you want the rescue on every caller, lower "
            "it deliberately and re-derive the top_k=2 figures in "
            "tests/real_wiki.py rather than adjusting this test."
        )
        for case in cases:
            dense = _sources(pipeline, case.question, 2, rescue=False)
            hybrid = _sources(pipeline, case.question, 2, rescue=True)
            assert hybrid == dense, (
                f"{case.question!r} changed at top_k=2: {dense} -> {hybrid}"
            )


class TestTheRescueDoesNotDependOnTheCache:
    def test_a_restored_cache_retrieves_what_a_recomputed_one_does(
        self, tmp_path, cases
    ):
        """``LEXICAL_RESCUE_VERSION`` is not in the cache identity, and here is why.

        The rescue is fitted at query time from ``embedding_text(chunk)``, and
        the embedding cache persists all four fields that text is built from --
        content, h1, summary and section. So a run that restored the cache and a
        run that recomputed hold byte-identical text, and there is nothing for a
        cache version to invalidate. If someone adds a field to
        ``embedding_text`` without persisting it, this fails and
        ``CHUNK_FILTER_VERSION`` earns a bump.
        """
        documents = load_documents()

        cold = RAGPipeline(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
            cache_dir=tmp_path, embedding_model=EMBEDDING_MODEL,
        )
        cold.ingest_documents(documents)
        assert (tmp_path / "embeddings.json").exists(), (
            "the cache was not written, so this test is comparing a run against "
            "itself and proving nothing."
        )

        warm = RAGPipeline(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
            cache_dir=tmp_path, embedding_model=EMBEDDING_MODEL,
        )
        warm.ingest_documents(documents)

        for case in cases:
            from_cold = [
                (c.id, round(s, 6)) for c, s in cold.retrieve(case.question, 3)
            ]
            from_warm = [
                (c.id, round(s, 6)) for c, s in warm.retrieve(case.question, 3)
            ]
            assert from_warm == from_cold, (
                f"{case.question!r} retrieves differently from a restored cache "
                "than from a recomputed one. The lexical index is derived from "
                "chunk text, so this means the cache does not persist everything "
                "that text is built from -- which is a cache identity bug, not a "
                "rescue bug."
            )


class TestThePublishedFigureHasAHomeInAComment:
    def test_both_populations_state_a_top_k3_figure(self):
        """A figure nothing publishes is a figure nobody maintains.

        ``tests/test_recall_claims.py`` holds the embedder comment in
        ``backend/config.py`` to publishing every population's recall figures.
        The top_k=3 row is new, so it has to be published there too, or the
        measurement above is a number no reader can find.

        Scoped to that ONE comment on purpose. ``tests/test_recall_claims.py``
        splits the two comments by subject -- recall figures in ``config.py``,
        corpus shape and filter cost in ``rag.py`` -- and asking the threshold
        comment to quote a recall figure would put a number in a comment whose
        subject is not recall, which is the same defect that file exists to
        prevent, running the other way.
        """
        prose = _block(CONFIG_PY, EMBEDDER_ANCHOR)
        for figures_set in COMMENT_FIGURES:
            stated = f"{figures_set.recall3_at_top3:.4f}"
            assert stated in prose, (
                f"the embedder comment in config.py does not state the "
                f"{figures_set.population} top_k=3 recall figure ({stated}). "
                "Both populations have to be published per population, or a "
                "reader on the other corpus has nothing to check against."
            )

    def test_the_threshold_comment_stops_claiming_the_filter_is_free(self):
        """The sentence the rescue falsified has to stop being published.

        It read "at the shipped top_k=3 it costs the caller nothing at all",
        which was true of a dense-only retrieval and is not true of this one:
        one slot in three goes to BM25, which can reach a page the cosine filter
        rejected. A reader who trusts that sentence will believe the 0.25 filter
        bounds everything the model sees. It no longer does.
        """
        prose = _block(RAG_PY, THRESHOLD_ANCHOR)
        assert "costs the caller nothing at all" not in prose, (
            "backend/services/rag.py still claims the cosine filter costs a "
            "top_k=3 caller nothing. The lexical rescue spends one slot in three "
            "on a ranker that looks past the filter, so the claim is false. Say "
            "what it costs instead -- the measured counts are still true."
        )