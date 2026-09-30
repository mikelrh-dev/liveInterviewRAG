"""The RAG numbers in comments are claims, and this makes the shipped ones checkable.

The problem
-----------
``backend/config.py`` justified the multilingual embedder with a measurement
whose "after" figures had been superseded hours later the same day:

    recall@1 0.5510 -> 0.6122, recall@3 0.6531 -> 0.7959, MRR@5 0.6109 -> 0.7088

The defect is not that those numbers are false. They are the isolated embedder
swap (commit 4de600d, 2026-09-29) with the model as the only lever touched, and
as a record of that one decision they are exactly right. The defect is that they
were undated, carried no configuration, and sat where a reader takes the current
state from -- so a comment describing a superseded vector space read as a
description of the shipping one. The re-measured state, in
``tests/real_wiki.py::MEASURED_FULL`` and re-verified here, is recall@1 0.7347,
recall@3 0.8163, MRR@5 0.7803.

The same failure, in the same comments, is a NUMBER WITH THE WRONG UNIT. The
threshold block in ``RAGPipeline.__init__`` said the 0.25 filter "discards 956 of
6125 pairs, 15.6%". 956 is a count of DEDUPLICATED results -- what
``retrieve()`` hands back, at most one per page -- and 6125 is a count of RAW
(question, chunk) cells, the whole 49 x 125 matrix. The two are not the same
space, so the ratio was not a percentage of anything. The corpus is 124 chunks
now, not 125, which is a third problem wearing a disguise: the figure describes
a corpus this repository no longer ships.

WHY A TEST, WHEN THE FIX IS PROSE
--------------------------------
Because a wrong number in a comment compiles, passes every suite, and is
believed. ``tests/test_chunk_size_comment.py`` established the pattern for the
``CHUNK_SIZE`` comment; this is the same guard for the two RAG comments, and it
reads the comments the way a reader parses them (as prose, with figures matched
to the words around them) rather than by line number.

WHAT IS AND IS NOT PINNED
-------------------------
Pinned: every figure the comments present as the CURRENT state, plus the
structure that keeps the historical figures from being read as current.

Not pinned: the historical figures' VALUES. They are the record of a decision
that is not being re-litigated, and the guard's job is to make sure they are
labelled and dated -- not to keep re-measuring a configuration that no longer
ships, which is the mistake ``tests/real_wiki.py`` exists to prevent. What is
pinned instead is that they cannot be mistaken for the present.

The measurements run against ``wiki/`` through the production pipeline, exactly
as ``tests/test_rag.py::real_wiki_pipeline`` builds it, so a figure in a comment
can only match if the code produces it.
"""

import re
from pathlib import Path

import pytest

from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    LABELLED_CASES,
    MEASURED_FULL,
    guard_blocker,
    load_documents,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "backend" / "config.py"
RAG_PY = REPO_ROOT / "backend" / "services" / "rag.py"

#: Located by its own subject rather than by line number, so an unrelated edit
#: above either block does not break the guard -- and so a block that MOVED is
#: found, not silently missed.
EMBEDDER_ANCHOR = "The default embedder is"
THRESHOLD_ANCHOR = "Minimum cosine"


def _block(path: Path, anchor: str) -> str:
    """The contiguous ``#`` comment block in ``path`` mentioning ``anchor``."""
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


def _prose(comment: str) -> str:
    """The block as one whitespace-collapsed string.

    Line-wrapped prose splits a figure away from the words that give it meaning,
    so every pattern below is matched against prose rather than source lines.
    The comment markers are stripped with a regex rather than by
    ``str.replace("#", "")`` because these blocks are INDENTED -- they sit
    inside a method body -- and a literal ``"\\n#"`` never matches an indented
    comment line.
    """
    return re.sub(r"\s+", " ", re.sub(r"(?m)^[ \t]*#[ \t]?", "", comment)).strip()


def _section(prose: str, start: str, end: str) -> str:
    """The slice of ``prose`` from one signpost up to the next.

    Both blocks are required to MARK their two sets of figures. That is the
    whole point of the guard: an undated pair of numbers with nothing between it
    and the reader is a number that gets believed, and the fix for a number
    getting believed is a signpost a test can find.
    """
    begin = prose.find(start)
    if begin < 0:
        raise AssertionError(
            f"the comment has no {start!r} signpost. Without one there is no way "
            "for a reader -- or this test -- to tell the current state from the "
            "history, which is the defect being fixed."
        )
    rest = prose[begin:]
    stop = rest.find(end, len(start))
    return rest if stop < 0 else rest[:stop]


def _figure(section: str, pattern: str) -> float:
    match = re.search(pattern, section)
    assert match is not None, (
        f"the comment does not state {pattern!r}, so this guard can no longer "
        f"check it. Section was:\n{section}"
    )
    return float(match.group(1))


@pytest.fixture(scope="module")
def measured() -> dict:
    """Every figure the two comments publish, re-derived through production.

    Module-scoped because embedding the corpus costs ~100s, and read through
    ``retrieve()`` rather than through a local dot product: a guard that
    recomputes the scoring itself tests itself. The one thing it cannot reach
    through the public path is the raw cosine MATRIX, which is why the threshold
    comment's counts are required to be counts of results -- the space
    ``retrieve()`` can actually be asked about.
    """
    documents = load_documents()
    blocker = guard_blocker(documents)
    assert blocker is None, (
        f"the real-corpus measurement cannot be made: {blocker} The figures "
        "in the comments under test are therefore unverifiable here."
    )

    from backend.services.rag import RAGPipeline

    rag = RAGPipeline(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    rag.ingest_documents(documents)

    def norm(source: str) -> str:
        return source.replace("\\", "/")

    recall1 = recall3 = 0
    reciprocal_rank = 0.0
    for case in LABELLED_CASES:
        sources = [
            norm(c.source)
            for c, _ in rag.retrieve(case.question, top_k=MEASURED_FULL.questions)
        ]
        rank = sources.index(case.primary) + 1 if case.primary in sources else None
        recall1 += rank == 1
        recall3 += rank is not None and rank <= 3
        reciprocal_rank += (1.0 / rank) if rank is not None and rank <= 5 else 0.0

    # The filter's effect, read through retrieve() only. The unfiltered run is
    # bounded by the page count rather than the chunk count, so the two figures
    # below are counts of RESULTS and are only ever compared to each other.
    # That is the unit the comment has to use.
    deeper = len(rag.chunks) * 2
    shipped = rag.threshold
    try:
        rag.threshold = shipped
        filtered = sum(
            len(rag.retrieve(c.question, top_k=deeper)) for c in LABELLED_CASES
        )
        rag.threshold = -1.0
        unfiltered = sum(
            len(rag.retrieve(c.question, top_k=deeper)) for c in LABELLED_CASES
        )
    finally:
        rag.threshold = shipped

    return {
        "chunks": len(rag.chunks),
        "questions": len(LABELLED_CASES),
        "recall1": recall1 / len(LABELLED_CASES),
        "recall3": recall3 / len(LABELLED_CASES),
        "mrr5": reciprocal_rank / len(LABELLED_CASES),
        "filtered": filtered,
        "unfiltered": unfiltered,
        "dropped": unfiltered - filtered,
    }


@pytest.fixture(scope="module")
def embedder_prose() -> str:
    return _prose(_block(CONFIG_PY, EMBEDDER_ANCHOR))


@pytest.fixture(scope="module")
def threshold_prose() -> str:
    return _prose(_block(RAG_PY, THRESHOLD_ANCHOR))


@pytest.mark.skipif(
    not (REPO_ROOT / "wiki").is_dir(),
    reason=(
        "wiki/ is absent from this checkout. It is 46 TRACKED files, so this "
        "only happens where the corpus was removed -- and there "
        "TestTheRetrievalGuardActuallyRan in tests/test_rag.py is failing, "
        "which is the signal. Do not read this skip as a pass."
    ),
)
class TestTheRAGCommentsQuoteTheCurrentMeasurement:
    """The figures two comments publish, against the code that produces them."""

    def test_the_current_recall3_is_the_measured_one(self, measured, embedder_prose):
        current = _section(embedder_prose, "CURRENT", "HISTORY")
        stated = _figure(current, r"recall@3\s+([\d.]+)")

        assert stated == pytest.approx(MEASURED_FULL.recall3, abs=0.0005), (
            f"the comment's CURRENT recall@3 is {stated}, measured "
            f"{MEASURED_FULL.recall3:.4f} ({MEASURED_FULL.hits}/"
            f"{MEASURED_FULL.questions}). tests/real_wiki.py::MEASURED_FULL owns "
            "that number; re-derive it there and re-measure, do not edit this "
            "comment to match a figure nobody re-ran."
        )

    def test_the_current_recall1_and_mrr5_are_the_measured_ones(
        self, measured, embedder_prose
    ):
        current = _section(embedder_prose, "CURRENT", "HISTORY")

        assert _figure(current, r"recall@1\s+([\d.]+)") == pytest.approx(
            measured["recall1"], abs=0.0005
        ), (
            f"the comment's CURRENT recall@1 disagrees with the measured "
            f"{measured['recall1']:.4f}"
        )
        assert _figure(current, r"MRR@5\s+([\d.]+)") == pytest.approx(
            measured["mrr5"], abs=0.0005
        ), (
            f"the comment's CURRENT MRR@5 disagrees with the measured "
            f"{measured['mrr5']:.4f}"
        )

    def test_no_current_figure_contradicts_the_measurement(
        self, measured, embedder_prose
    ):
        """Every ``recall@k`` in the CURRENT section, whatever its k.

        Written as a scan rather than as three named assertions so a fourth
        metric quoted in this comment is checked the day it is added, instead of
        being the one nobody pinned.
        """
        current = _section(embedder_prose, "CURRENT", "HISTORY")
        known = {
            "1": measured["recall1"],
            "3": measured["recall3"],
        }

        quoted = dict(re.findall(r"(recall@\d|MRR@\d)\s+([\d.]+)", current))
        assert quoted, (
            "the CURRENT section quotes no measurement, so the signpost exists "
            "but says nothing. Either state the current figures or drop the "
            f"signpost. Section was:\n{current}"
        )
        for metric, value in quoted.items():
            k = metric.split("@")[1]
            if k == "5":
                expected = measured["mrr5"]
            else:
                assert k in known, (
                    f"the comment quotes {metric} and this guard cannot measure "
                    f"it; add it to the measured fixture rather than leaving it "
                    "unchecked"
                )
                expected = known[k]
            assert float(value) == pytest.approx(expected, abs=0.0005), (
                f"the comment states {metric} {value}; measured {expected:.4f}. "
                "A figure that contradicts the measurement is the defect this "
                "file exists to prevent."
            )

    def test_the_current_section_is_dated(self, embedder_prose):
        current = _section(embedder_prose, "CURRENT", "HISTORY")

        dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", current)
        assert dates, (
            "the CURRENT figures are undated. An undated measurement is read as "
            "a standing fact, which is how a superseded one survived a day."
        )

    def test_the_history_is_labelled_and_dated_as_history(self, embedder_prose):
        history = _section(embedder_prose, "HISTORY", "\x00")

        assert re.search(r"\b(\d{4}-\d{2}-\d{2})\b", history), (
            "the historical figures must carry the date they were measured on. "
            f"Section was:\n{history}"
        )
        assert re.search(r"([\d.]+)\s*->\s*([\d.]+)", history), (
            "the historical record must still be a before/after PAIR, so the "
            "movement it recorded stays legible. It was a one-decision "
            "measurement; a single figure would not be a record of anything. "
            f"Section was:\n{history}"
        )

    def test_the_two_figures_never_share_a_signpost(self, measured, embedder_prose):
        """The current and superseded pairs must be in different sections.

        This is the assertion the original defect could not fail: both numbers
        were in one paragraph, with nothing marking which was live.
        """
        current = _section(embedder_prose, "CURRENT", "HISTORY")
        history = _section(embedder_prose, "HISTORY", "\x00")

        superseded = ("0.5510", "0.6122", "0.6531", "0.7959", "0.6109", "0.7088")
        for figure in superseded:
            assert figure not in current, (
                f"{figure} is a figure of the superseded embedder configuration "
                "and is in the CURRENT section. Two configurations' numbers in "
                "one paragraph is the defect, whichever paragraph it is."
            )
            assert figure in history, (
                f"{figure} is the historical record of the embedder swap and has "
                "disappeared. The fix is to date and label it, not to delete it."
            )

    # ── the threshold comment in backend/services/rag.py ─────────────────────

    def test_the_chunk_count_is_this_corpus_chunk_count(self, measured, threshold_prose):
        stated = _figure(threshold_prose, r"(\d+)\s+chunks")

        assert int(stated) == measured["chunks"], (
            f"the comment describes a corpus of {int(stated)} chunks; "
            f"_chunk_document at {CHUNK_SIZE}/{CHUNK_OVERLAP} produces "
            f"{measured['chunks']} on this wiki/ today. A comment that names a "
            "corpus the repository does not ship is worse than no comment, "
            "because the next reader cannot tell it is stale."
        )

    def test_the_matrix_is_questions_times_that_chunk_count(self, measured, threshold_prose):
        match = re.search(r"(\d+)\s*x\s*(\d+)\s*\(question,\s*chunk\)", threshold_prose)
        assert match is not None, (
            "the comment must name the shape of the matrix it measured over, as "
            "'49 x 124 (question, chunk)', so the pool size is checkable. "
            f"Comment was:\n{threshold_prose}"
        )
        questions, chunks = int(match.group(1)), int(match.group(2))

        assert questions == measured["questions"]
        assert chunks == measured["chunks"]

    def test_the_filtered_pair_counts_share_one_unit(self, measured, threshold_prose):
        """Numerator and denominator must be counts of the SAME thing.

        The defect this pins: "956 of 6125 pairs" put a count of deduplicated
        results over a count of raw cosine cells. 52.9% of 1813 is a true
        statement about what the filter costs a caller; 15.6% of 6125 was not a
        statement about anything.
        """
        quoted = re.search(
            r"discards\s+(\d+)\s+of\s+(?:the\s+)?(\d+)\s+results", threshold_prose
        )
        assert quoted is not None, (
            "the comment must state the filter's cost as 'discards N of the M "
            "results', both counted as results returned by retrieve(). Comment "
            f"was:\n{threshold_prose}"
        )

        assert int(quoted.group(1)) == measured["dropped"], (
            f"the comment says the filter discards {quoted.group(1)} results; "
            f"measured {measured['dropped']} "
            f"({measured['unfiltered']} unfiltered - {measured['filtered']} "
            "filtered, at top_k=2x the chunk count)"
        )
        assert int(quoted.group(2)) == measured["unfiltered"], (
            f"the comment's denominator is {quoted.group(2)}; the unfiltered run "
            f"returns {measured['unfiltered']}. A denominator from a different "
            "space than the numerator is how a percentage stops meaning anything."
        )

    def test_the_stated_share_follows_from_the_stated_counts(
        self, measured, threshold_prose
    ):
        quoted = re.search(
            r"discards\s+(\d+)\s+of\s+(?:the\s+)?(\d+)\s+results[^.]*?([\d.]+)\s*%",
            threshold_prose,
        )
        assert quoted is not None, (
            "the comment must state the share alongside the two counts, so the "
            "arithmetic is checkable"
        )

        dropped, total, share = int(quoted.group(1)), int(quoted.group(2)), float(quoted.group(3))
        assert share == pytest.approx(100 * dropped / total, abs=0.05), (
            f"the comment states {dropped} of {total} results and then {share}%, "
            f"which is {100 * dropped / total:.2f}%. A percentage that does not "
            "follow from the numbers beside it is the unit bug, not a rounding "
            "difference."
        )
