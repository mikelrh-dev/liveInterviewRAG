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

The measurement runs against ``wiki/``. This comment used to describe that
directory as "gitignored, private, and absent from a clean clone" and to skip
here on that basis. All three were wrong: ``git ls-files wiki`` returns 46
files, they are in ``origin/main``, and ``actions/checkout`` brings them to
every CI run. So the measurement runs everywhere, and the skip below is the
narrow one it should have been all along -- this test is skipped only if the
corpus is genuinely gone, which is a state that now makes the retrieval guard in
``tests/test_rag.py`` fail rather than skip.

WHAT IT DOES NOT DO
-------------------
It does not assert a chunk size, and it does not fail when the corpus changes.
It asserts that the figures in the comment equal the figures measured now, on
the corpus the comment names. If the corpus grows, the comment is stale and
this fails -- which is the intended outcome, and the reason the fix was to state
the corpus and the method in the comment in the first place.
"""

import re
import statistics
from pathlib import Path

import pytest

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


@pytest.mark.skipif(
    not WIKI_DIR.is_dir(),
    reason=(
        "wiki/ is absent from this checkout. It is 46 TRACKED files, so this "
        "only happens where the corpus was removed -- and there "
        "TestTheRetrievalGuardActuallyRan in tests/test_rag.py is failing, "
        "which is the signal. Do not read this skip as a pass."
    ),
)
class TestTheChunkSizeCommentIsTrue:
    """Everything below re-derives the numbers and compares them to the prose."""

    @pytest.fixture(scope="class")
    def measured(self) -> dict:
        from backend.services.candidate import CandidateProfile
        from backend.services.rag import RAGPipeline

        profile = CandidateProfile(REPO_ROOT / "candidate", wiki_dir=WIKI_DIR)
        profile.load()
        pipeline = RAGPipeline(chunk_size=400, chunk_overlap=50)
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
            "max": max(words),
            "under_15_pct": 100 * sum(1 for w in words if w < 15) / len(words),
        }

    def test_the_corpus_size_is_stated_correctly(self, measured):
        comment = _comment_under_test()
        stated = _stated_int(r"(\d+)\s+(?:wiki\s+)?(?:pages|documents)", comment)

        assert stated == measured["documents"], (
            f"the comment says {stated} documents; CandidateProfile loads "
            f"{measured['documents']} from wiki/ at 400/50"
        )

    def test_the_chunk_count_is_stated_correctly(self, measured):
        comment = _comment_under_test()
        stated = _stated_int(r"(\d+)\s+chunks", comment)

        assert stated == measured["chunks"], (
            f"the comment says {stated} chunks; _chunk_document produces "
            f"{measured['chunks']}"
        )

    def test_the_median_is_stated_correctly(self, measured):
        comment = _comment_under_test()
        stated = _stated_int(r"median\s+(\d+)", comment)

        assert stated == measured["median"], (
            f"the comment says median {stated}; measured {measured['median']}"
        )

    def test_the_longest_chunk_is_stated_correctly(self, measured):
        comment = _comment_under_test()
        stated = _stated_int(r"longest\s+(\d+)", comment)

        assert stated == measured["max"], (
            f"the comment says longest {stated}; measured {measured['max']}"
        )

    def test_the_short_chunk_share_is_stated_correctly(self, measured):
        comment = _comment_under_test()
        stated = _stated(r"([\d.]+)%\s+are under 15 words", comment, float)

        assert stated == pytest.approx(measured["under_15_pct"], abs=0.05), (
            f"the comment says {stated}% of chunks are under 15 words; measured "
            f"{measured['under_15_pct']:.1f}%"
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
