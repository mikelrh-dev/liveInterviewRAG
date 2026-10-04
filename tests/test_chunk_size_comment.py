"""A comment that quotes a measurement is a claim, and this makes it checkable.

The problem
-----------
``backend/main.py`` carried a comment justifying ``CHUNK_SIZE=400`` with measured
figures: "38 candidate/wiki documents ... 214 chunks, median 31 words, p95 107,
longest 210, and ZERO chunks reach 400 words", plus a conclusion built on "37.4%
are under 15 words" -- that the distribution was bottom-heavy and that a ceiling
which actually bites "would need to be far lower".

Re-measured on the same corpus through the same ``_chunk_document``:

    37 documents, 125 chunks, median 54, p95 131, longest 266
    0.8% under 15 words (1 of 125)

Every figure was wrong, and the conclusion was inverted: with a median of 54 and
a single chunk under 15 words, the distribution is not bottom-heavy at all. The
correct conclusion -- that 400 words never binds, and the overlap is therefore
inert -- survives, but for the opposite reason than the one written down.

WHY A TEST, WHEN THE FIX IS A COMMENT
-------------------------------------
Because the failure mode is invisible. A wrong number in a comment compiles,
passes every suite, and is believed by the next person who does not re-measure.
That is exactly how "214 chunks, median 31" survived long enough to be written
up as a finding.

THE SECOND DEFECT: ONE POPULATION WAS PUBLISHED AS IF THERE WERE ONE
--------------------------------------------------------------------
The comment named the full population's figures and nothing else, so on a clean
clone every assertion below was true about a corpus the reader did not have:

    the comment says 124 chunks; _chunk_document produces 116
    the comment says median 54; measured 53.5

Four FAQ pages (``nivel-ingles``, ``disponibilidad``, ``hobbies-intereses``,
``por-que-contratarte``) used to exist on disk without being in the index, so a
clone loaded 33 pages and chunked to 116. Commit ``efda998`` committed them: both
corpora are 37 pages and 124 chunks now, and what still separates them is that
15 ``wiki/*.md`` files are modified in the working tree and uncommitted. Same
method, same 400/50, different text -- and the chunk count no longer moves, which
is worse for a reader who trusts it, not better. The comment publishes BOTH
populations on rows named by their population, and every assertion below binds
this checkout's row to a live measurement of this checkout's corpus -- same
assertions, same strength, in either population. The rows live in
``tests/real_wiki.py::COMMENT_FIGURES``, the same record
``tests/test_recall_claims.py`` uses, and the row is selected by a digest of the
served corpus rather than by a question count, because both rows are 49 wide.

The median is worth calling out because it looked like a rounding artefact and
is not: ``statistics.median`` over an EVEN number of chunks returns the mean of
the two middle values, and 124 is even, so 54.0 and 53.0 are what those corpora
really measure. An assertion that could only compare integers would have forced
one of the two populations to publish a number the measurement does not produce.

The measurement runs against ``wiki/``. This comment used to describe that
directory as "gitignored, private, and absent from a clean clone" and to skip
here on that basis. All three were wrong: ``git ls-files wiki`` returns 50
files, they are in ``origin/main``, and ``actions/checkout`` brings them to
every CI run. So the measurement runs everywhere, and the skip below is the
narrow one it should have been all along -- this test is skipped only if the
corpus is genuinely gone, which is a state that now makes the retrieval guard in
``tests/test_rag.py`` fail rather than skip.

WHAT IT DOES NOT DO
-------------------
It does not assert a chunk size, and it does not fail merely because the corpus
changed shape. It asserts that the figures the comment publishes for the
population THIS checkout produced equal the figures measured now on that
population. If the corpus grows, the comment is stale for both populations and
this fails -- which is the intended outcome, and the reason the fix was to state
the corpus and the method in the comment in the first place.
"""

import re
import statistics
from pathlib import Path

import pytest

from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COMMENT_FIGURES,
    CommentFigures,
    comment_figures_for,
    p95,
)
from tests.test_recall_claims import _population_row

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "backend" / "main.py"
WIKI_DIR = REPO_ROOT / "wiki"

#: The comment block that carries the measurement, located by its own subject
#: rather than by line number so an unrelated edit above it does not break it.
_ANCHOR = "CHUNK_SIZE=400"


def _comment_under_test() -> str:
    """The contiguous ``#`` block that mentions the chunk-size constants.

    Collected as blocks rather than by walking outwards from the anchor: the
    anchor is at the START of this block, so a walk-back from it stops
    immediately and returns an empty string. Grouping the whole file's comment
    lines and selecting on content also survives someone reordering the block.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in MAIN_PY.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            current.append(line)
        else:
            if current:
                blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    for block in blocks:
        if _ANCHOR in "\n".join(block):
            return "\n".join(block)

    raise AssertionError(
        f"no comment block in {MAIN_PY.name} mentions {_ANCHOR!r}; the "
        "justification this test guards has been deleted or moved"
    )


def _prose(comment: str) -> str:
    """The comment block as one whitespace-collapsed string.

    Line-wrapped prose splits figures away from the words that give them
    meaning -- "0.8%" at the end of one line, "are under 15 words" at the start
    of the next -- so every pattern below is written against prose, not against
    source lines. That is deliberate: a measurement quote has to be matched the
    way a reader parses it.
    """
    return re.sub(r"\s+", " ", comment.lstrip("#").replace("\n#", " ")).strip()


def _stated(pattern: str, comment: str, cast):
    text = _prose(comment)
    match = re.search(pattern, text)
    assert match is not None, (
        f"the comment no longer states {pattern!r}, so this guard can no longer "
        f"check it. Comment was:\n{text}"
    )
    return cast(match.group(1))


def _stated_int(pattern: str, comment: str) -> int:
    return _stated(pattern, comment, int)


def _stated_float(pattern: str, comment: str) -> float:
    return _stated(pattern, comment, float)


@pytest.mark.skipif(
    not WIKI_DIR.is_dir(),
    reason=(
        "wiki/ is absent from this checkout. It is 50 TRACKED files, so this "
        "only happens where the corpus was removed -- and there "
        "TestTheRetrievalGuardActuallyRan in tests/test_rag.py is failing, "
        "which is the signal. Do not read this skip as a pass."
    ),
)
class TestTheChunkSizeCommentIsTrue:
    """Everything below re-derives the numbers and compares them to the prose.

    Every assertion is scoped to the population THIS checkout produced, and
    reads the row the comment publishes for THAT population. The strength of
    each check is unchanged; only the row it is pointed at moved.
    """

    @pytest.fixture(scope="class")
    def figures(self) -> CommentFigures:
        """The population this checkout has, and the figures recorded for it."""
        recorded = comment_figures_for()
        assert recorded is not None, (
            "this checkout resolves a labelled-question count this guard has no "
            "recorded figures for. They live in "
            "tests/real_wiki.py::COMMENT_FIGURES, calibrated for "
            f"{[f.questions for f in COMMENT_FIGURES]}. Re-measure on the "
            "population that remains and add a row -- do not point this checkout "
            "at another population's numbers."
        )
        return recorded

    @pytest.fixture(scope="class")
    def measured(self) -> dict:
        from backend.services.candidate import CandidateProfile
        from backend.services.rag import RAGPipeline

        profile = CandidateProfile(REPO_ROOT / "candidate", wiki_dir=WIKI_DIR)
        profile.load()
        pipeline = RAGPipeline(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        words = [
            len(chunk.content.split())
            for name, content in profile.documents.items()
            for chunk in pipeline._chunk_document(name, content)
        ]
        return {
            "documents": len(profile.documents),
            "chunks": len(words),
            "words": words,
            "median": statistics.median(words),
            "p95": p95(words),
            "max": max(words),
            "at_ceiling": sum(1 for w in words if w >= CHUNK_SIZE),
            "under_15_pct": 100 * sum(1 for w in words if w < 15) / len(words),
            "under_30_pct": 100 * sum(1 for w in words if w < 30) / len(words),
        }

    def test_every_population_is_published(self, figures):
        """Both rows, not just this checkout's.

        A comment that has drifted back to naming one corpus passes everywhere it
        is read -- the author's tree, where that corpus is the one on disk -- and
        fails only on someone else's, who has no way to check the figure and no
        reason to suspect it is the wrong one. That is the regression this pins.
        """
        block = _comment_under_test()
        for published in COMMENT_FIGURES:
            _population_row(block, published)

    def test_the_corpus_size_is_stated_correctly(self, figures, measured):
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"(\d+)\s+(?:wiki\s+)?(?:pages|documents)", row)

        assert stated == measured["documents"], (
            f"the comment's {figures.population} row says {stated} documents; "
            f"CandidateProfile loads {measured['documents']} from wiki/ at "
            f"{CHUNK_SIZE}/{CHUNK_OVERLAP}"
        )
        assert stated == figures.pages, (
            f"the comment's {figures.population} row says {stated} documents; "
            f"tests/real_wiki.py::COMMENT_FIGURES records {figures.pages}."
        )

    def test_the_chunk_count_is_stated_correctly(self, figures, measured):
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"(\d+)\s+chunks", row)

        assert stated == measured["chunks"], (
            f"the comment's {figures.population} row says {stated} chunks; "
            f"_chunk_document produces {measured['chunks']}"
        )
        assert stated == figures.chunks, (
            f"the comment states {stated} chunks for the {figures.population} "
            f"population; real_wiki.py records {figures.chunks}."
        )

    def test_the_median_is_stated_correctly(self, figures, measured):
        """A float, because that is what an even-length chunk set measures.

        Both corpora chunk to 124, which is even, so ``statistics.median``
        returns a mean of the two middle values: 54.0 on the working tree and
        53.0 on the committed corpus. Parsed as a float and compared for
        EQUALITY -- one word of median is the whole distance between the two
        populations, so a tolerance here would hide exactly the drift this file
        exists to catch.
        """
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_float(r"median\s+([\d.]+)", row)

        assert stated == measured["median"], (
            f"the comment's {figures.population} row says median {stated}; "
            f"measured {measured['median']}"
        )
        assert stated == figures.median_words, (
            f"the comment states median {stated} for the {figures.population} "
            f"population; real_wiki.py records {figures.median_words}."
        )

    def test_the_p95_is_stated_correctly(self, figures, measured):
        """Nearest rank, per ``tests/real_wiki.py::p95``.

        Pinned because the definition is load-bearing and not universal: linear
        interpolation puts this same corpus at 130.7, so a guard that
        re-derived p95 the other way would report a mismatch on a comment that
        is exactly right.
        """
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"p95\s+(\d+)", row)

        assert stated == measured["p95"], (
            f"the comment's {figures.population} row says p95 {stated}; nearest "
            f"rank measures {measured['p95']}"
        )
        assert stated == figures.p95_words, (
            f"the comment states p95 {stated} for the {figures.population} "
            f"population; real_wiki.py records {figures.p95_words}."
        )

    def test_the_longest_chunk_is_stated_correctly(self, figures, measured):
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"longest\s+(\d+)", row)

        assert stated == measured["max"], (
            f"the comment's {figures.population} row says longest {stated}; "
            f"measured {measured['max']}"
        )
        assert stated == figures.longest_words, (
            f"the comment states longest {stated} for the {figures.population} "
            f"population; real_wiki.py records {figures.longest_words}."
        )

    def test_the_ceiling_is_never_reached(self, figures, measured):
        """The load-bearing claim: 400 words does not bind, on either population.

        ``CHUNK_OVERLAP`` is inert for this same reason, so this is the assertion
        the whole justification rests on. It is checked on the DATA and on the
        published figure, because a comment that stopped saying it would leave
        the conclusion standing on nothing.
        """
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"(\d+)\s+chunks reach\s+%d words" % CHUNK_SIZE, row)

        assert measured["at_ceiling"] == 0, (
            f"{measured['at_ceiling']} chunk(s) now reach {CHUNK_SIZE} words, so "
            "_chunk_document DOES take its splitting branch and the comment's "
            "conclusion -- that 400 is unreachable and the overlap is inert -- is "
            "no longer true of either population. Re-measure the sweep."
        )
        assert stated == measured["at_ceiling"], (
            f"the comment's {figures.population} row says {stated} chunks reach "
            f"{CHUNK_SIZE} words; measured {measured['at_ceiling']}"
        )
        assert stated == figures.chunks_at_ceiling, (
            f"the comment states {stated} for the {figures.population} "
            f"population; real_wiki.py records {figures.chunks_at_ceiling}."
        )

    def test_the_headroom_is_stated_correctly(self, figures, measured):
        """``CHUNK_SIZE - longest``, stated in words.

        Derived rather than stored, so the figure in the comment cannot drift away
        from the longest chunk it is derived from.
        """
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_int(r"(\d+)\s+short of the ceiling", row)
        headroom = CHUNK_SIZE - measured["max"]

        assert stated == headroom, (
            f"the comment's {figures.population} row says the longest chunk is "
            f"{stated} words short of the ceiling; {CHUNK_SIZE} - "
            f"{measured['max']} is {headroom}"
        )
        assert stated == figures.headroom, (
            f"the comment states {stated} short of the ceiling for the "
            f"{figures.population} population; real_wiki.py derives "
            f"{figures.headroom} from longest_words={figures.longest_words}."
        )

    def test_the_short_chunk_share_is_stated_correctly(self, figures, measured):
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_float(r"([\d.]+)%\s+are under 15 words", row)

        assert stated == pytest.approx(measured["under_15_pct"], abs=0.05), (
            f"the comment's {figures.population} row says {stated}% of chunks are "
            f"under 15 words; measured {measured['under_15_pct']:.1f}%"
        )
        assert stated == pytest.approx(figures.under15_pct, abs=0.05), (
            f"the comment states {stated}% for the {figures.population} "
            f"population; real_wiki.py records {figures.under15_pct}."
        )

    def test_the_under_30_share_is_stated_correctly(self, figures, measured):
        """The second half of the "not bottom-heavy" claim, pinned like the first.

        13.7% against 13.8% is the whole population difference in one decimal
        place, which is why it is checked per population and not once.
        """
        row = _population_row(_comment_under_test(), figures)
        stated = _stated_float(r"([\d.]+)%\s+are under 30", row)

        assert stated == pytest.approx(measured["under_30_pct"], abs=0.05), (
            f"the comment's {figures.population} row says {stated}% of chunks are "
            f"under 30 words; measured {measured['under_30_pct']:.1f}%"
        )
        assert stated == pytest.approx(figures.under30_pct, abs=0.05), (
            f"the comment states {stated}% for the {figures.population} "
            f"population; real_wiki.py records {figures.under30_pct}."
        )

    def test_the_inverted_conclusion_is_gone(self, measured):
        """The conclusion has to follow the distribution, not the other way round.

        Asserted on the DATA rather than on the prose: "bottom-heavy" is only a
        defensible reading of a distribution where most chunks are short, and the
        measured one is not. Whichever way the comment is worded, it must not
        assert a shape the corpus does not have.
        """
        words = measured["words"]
        share_short = sum(1 for w in words if w < 15) / len(words)

        assert share_short < 0.05, (
            "the corpus is now mostly-short, so a bottom-heavy reading would be "
            f"correct again ({share_short:.1%} under 15 words) -- the comment "
            "may need to say so again, but from a new measurement"
        )
        assert statistics.median(words) > 40, (
            f"median is {statistics.median(words)}, so 'bottom-heavy' no longer "
            "describes this corpus"
        )

    def test_the_comment_names_the_corpus_it_was_measured_on(self):
        """A figure without its corpus is not a measurement, it is a rumour.

        The corpus is tracked, so a reader on any other machine CAN reproduce
        these numbers -- and has to be told which corpus to reproduce them on
        rather than left to assume they are universal.
        """
        comment = _prose(_comment_under_test())
        assert "wiki/" in comment, (
            "the comment must name the corpus it was measured on; without it a "
            "reader cannot tell whether the figures are universal or specific "
            "to one person's page set"
        )
