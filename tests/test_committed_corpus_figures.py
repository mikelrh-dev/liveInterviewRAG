"""The ``reduced`` row must describe the corpus a THIRD PARTY gets.

THE DEFECT, AND WHY IT NEEDS A SEPARATE FILE
--------------------------------------------
``tests/test_recall_claims.py`` binds the figures the two shipped comments
publish to a live measurement of whatever ``wiki/`` happens to be on the
machine running the suite. That is correct for the ``full`` population, which
is DEFINED as the working tree of the checkout running it.

It is the wrong instrument for the ``reduced`` population. ``reduced`` is
defined as what ``git clone`` produces -- and its figures must be DERIVED on
``git show HEAD:<path>``, because the author's working tree is text that 15
modified ``wiki/*.md`` files have moved ahead of HEAD and that nobody else is
ever served.

That was true before commit ``efda998`` and it is true now, but it stopped
being VISIBLE the same way. Before, the two corpora also differed in shape: the
four FAQ pages were untracked, so the committed tree loaded 33 pages and
resolved 41 of the 49 labels. Same page count on both sides would have been a
coincidence, and this file asserted it was not one. Since ``efda998`` committed
those four pages, BOTH sides load 37 pages, resolve 49 of 49 and chunk to 124:
the committed corpus and the working tree are now indistinguishable by any count,
which is exactly why this file -- and not a question count -- is what tells them
apart.

THE RE-MEASUREMENT, 2026-10-04
------------------------------
Against the corpus rebuilt from ``git show HEAD:``:

    pages 37, questions 49, chunks 124
    recall@1 0.7551   recall@3 0.8163   MRR@5 0.7980
    851 results with the filter, 1813 without, 962 discarded (53.1%)
    45 of 49 at the shipped top_k=3

against the working tree's own row: 0.7347 / 0.8163 / 0.7803, 854 / 1813 / 959,
44 of 49. Same page count, same question count, same chunk count, same 1813
unfiltered results -- and a different ranking, because the text behind those
numbers is not the same text. ``recall@3`` agreeing is the coincidence that hides
the rest; ``recall@1`` moves by 0.0204 (one question) and MRR@5 by 0.0177.

WHY A SEPARATE FILE AND NOT A FIXTURE TWIST
-------------------------------------------
Because the only instrument that can tell the two corpora apart is the one
thing the checkout has and the clone does not: git. ``wiki/`` on disk is
whatever the author is editing; ``git show HEAD:<path>`` is what a third party
is served. This file rebuilds the committed corpus into a temporary tree, runs
the PRODUCTION loader over it, and measures it through the production
``retrieve()``. Then it asks one question:

    do the figures labelled ``reduced`` describe that corpus?

In the author's checkout that is a measurement of a corpus the author never
runs, which is the whole point: the reduced row is only ever exercised by
somebody who does NOT have the fifteen uncommitted rewrites, so that is where it
has to be right.

WHAT THIS DOES NOT CLAIM
------------------------
It does not claim the working tree's numbers are wrong -- ``full`` measures the
working tree and is untouched. It does not re-derive the recall FLOOR's
tolerance. ``TOLERATED_QUESTIONS`` and ``MEASURED_FULL`` are not inputs here and
were not changed. What it does assert is that the record of the reduced
measurement (``Measurement``) and the record of the figures the comments print
(``CommentFigures``) both describe the committed corpus, so the two cannot
drift onto different trees again -- which is how ``34/41`` and ``0.8293`` came
to be a working-tree measurement wearing a clone's name, and how ``33/41/116``
came to describe a population that stopped existing when its defining commit
landed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict

import pytest

from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COMMENT_FIGURES_REDUCED,
    CORPUS_DIGESTS,
    MEASURED_REDUCED,
    corpus_digest,
    resolved_cases,
)
from tests.test_recall_claims import (
    CONFIG_PY,
    EMBEDDER_ANCHOR,
    RAG_PY,
    THRESHOLD_ANCHOR,
    _block,
    _figure,
    _population_row,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two populations this file knows how to name. ``full`` is deliberately
#: absent: it is the working tree of the checkout running the suite, and its
#: figures are checked by ``tests/test_recall_claims.py`` against the working
#: tree it is read on.
REDUCED = COMMENT_FIGURES_REDUCED


def _norm(source: str) -> str:
    """Document keys are ``str(Path.relative_to(...))``, so separators vary."""
    return source.replace("\\", "/")


def _git(*args: str) -> bytes:
    """``git`` in this repository, for the index and for committed blobs."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout


@pytest.fixture(scope="module")
def committed_wiki_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A ``wiki/`` containing exactly what ``git clone`` would serve.

    Two commands, and the difference between them is the whole point of this
    file:

    ``git ls-files -- wiki``
        The INDEX. All fifty pages are listed, INCLUDING the four FAQ pages that
        used to be the definition of this population: commit ``efda998`` committed
        them, so the index no longer tells the two corpora apart.
    ``git show HEAD:<path>``
        The COMMITTED blob, and THIS is what still defines ``reduced``. The
        author's 15 modified ``wiki/*.md`` files are read from here at their
        committed content, so an uncommitted rewrite of the corpus cannot move a
        published figure.

    Returns the temp root; the wiki is written under ``<root>/wiki``.
    """
    root = tmp_path_factory.mktemp("committed_corpus")
    for raw in _git("ls-files", "-z", "--", "wiki").split(b"\0"):
        if not raw.endswith(b".md"):
            continue
        relative = raw.decode("utf-8")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git("show", f"HEAD:{relative}"))
    return root


@pytest.fixture(scope="module")
def committed_documents(committed_wiki_root: Path) -> Dict[str, str]:
    """The committed corpus read through the PRODUCTION loader.

    Not a hand-rolled ``rglob``: ``_SKIP_FILES`` and ``_SKIP_DIRS`` are the real
    loader's, so ``index.md``, ``README.md``, ``CONVENCIONES.md`` and
    ``templates/`` drop out here for the same reason they drop out in
    production, and a future addition to either set cannot make this file
    measure a different corpus than the app serves.
    """
    from backend.services.candidate import CandidateProfile

    profile = CandidateProfile(committed_wiki_root, wiki_dir=committed_wiki_root / "wiki")
    profile.load()
    assert profile.documents, (
        f"the committed wiki/ rebuilt into {committed_wiki_root} loaded nothing. "
        "If this is failing, `git ls-files wiki` is empty here rather than the "
        "corpus having been deleted."
    )
    return profile.documents


@pytest.fixture(scope="module")
def committed_cases(committed_documents: Dict[str, str]):
    return resolved_cases(committed_documents)


@pytest.fixture(scope="module")
def measured(committed_documents: Dict[str, str], committed_cases) -> dict:
    """Every figure the reduced row publishes, measured on the COMMITTED corpus.

    Same measurement as ``tests/test_recall_claims.py::measured`` and for the
    same reasons: read through ``retrieve()`` rather than through a local dot
    product, so a guard that recomputed the scoring itself would be testing
    itself; module-scoped because embedding the corpus costs about half a
    minute.
    """
    from backend.services.rag import RAGPipeline

    rag = RAGPipeline(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    rag.ingest_documents(committed_documents)
    questions = len(committed_cases)

    def norm(source: str) -> str:
        return source.replace("\\", "/")

    recall1 = recall3 = 0
    reciprocal_rank = 0.0
    for case in committed_cases:
        sources = [norm(c.source) for c, _ in rag.retrieve(case.question, top_k=questions)]
        rank = sources.index(case.primary) + 1 if case.primary in sources else None
        recall1 += rank == 1
        recall3 += rank is not None and rank <= 3
        reciprocal_rank += (1.0 / rank) if rank is not None and rank <= 5 else 0.0

    deeper = len(rag.chunks) * 2
    shipped = rag.threshold
    try:
        rag.threshold = shipped
        filtered = sum(len(rag.retrieve(c.question, top_k=deeper)) for c in committed_cases)
        rag.threshold = -1.0
        unfiltered = sum(
            len(rag.retrieve(c.question, top_k=deeper)) for c in committed_cases
        )
    finally:
        rag.threshold = shipped

    # At the top_k production ships, which is a DIFFERENT measurement from every
    # other figure in this fixture: everything above is at ``top_k`` = the
    # population size, and this is at ``backend/config.py``'s ``RAG_TOP_K``.
    # Measured here rather than only on the working tree because this file is
    # the only one that can see the corpus a clone gets, and the lexical rescue
    # fires at a width-dependent condition -- so its figure on a clone is not
    # derivable from its figure on the author's machine.
    hits_at_top3 = 0
    for case in committed_cases:
        sources = [norm(c.source) for c, _ in rag.retrieve(case.question, top_k=3)]
        rank = sources.index(case.primary) + 1 if case.primary in sources else None
        hits_at_top3 += rank is not None and rank <= 3

    return {
        "pages": len(committed_documents),
        "questions": questions,
        "chunks": len(rag.chunks),
        "recall1": recall1 / questions,
        "recall3": recall3 / questions,
        "mrr5": reciprocal_rank / questions,
        "hits3": recall3,
        "hits3_at_top3": hits_at_top3,
        "recall3_at_top3": hits_at_top3 / questions,
        "filtered": filtered,
        "unfiltered": unfiltered,
        "dropped": unfiltered - filtered,
    }


# ── The corpus itself ────────────────────────────────────────────────────────
#
# Everything below compares published figures against `measured`, so if this
# fixture ever measured the WRONG corpus every comparison would be vacuously
# true. That is why it is asserted on its own, and why the assertion is now a
# DIGEST rather than a page count: since ``efda998`` both corpora load 37 pages
# and resolve 49 labels, so a count can no longer tell them apart and a count that
# agreed would be agreeing by coincidence.


class TestTheCommittedCorpusIsTheOneTheReducedRowClaimsToDescribe:
    def test_the_committed_corpus_resolves_the_reduced_population(
        self, committed_documents, committed_cases
    ):
        """37 pages, 49 questions -- and the corpus whose digest is recorded.

        Stated as the two counts plus the digest, because "37 pages and 49
        questions" is now what BOTH populations look like. The digest is the only
        thing left that says which corpus this is, and it is recorded in
        ``tests/real_wiki.py::CORPUS_DIGESTS`` next to the rows it selects.
        """
        assert len(committed_documents) == REDUCED.pages, (
            f"the committed corpus loads {len(committed_documents)} pages; the "
            f"reduced population is calibrated for {REDUCED.pages}."
        )
        assert len(committed_cases) == REDUCED.questions, (
            f"the committed corpus resolves {len(committed_cases)} of the "
            "labelled questions; the reduced population is "
            f"{REDUCED.questions}."
        )

    def test_this_corpus_is_the_one_the_reduced_row_was_measured_on(
        self, committed_documents
    ):
        """The digest, not the shape: same 37 pages, same 49 labels, other text.

        This is the assertion that replaced "the four untracked FAQ pages are
        really absent". Those pages were what made the two corpora differ in
        SHAPE, and commit ``efda998`` committed them: both sides now load 37
        pages, resolve 49 of 49 and chunk to 124, so the absence check could no
        longer distinguish them and its passing meant nothing.

        The digest does distinguish them, and it is the key every ``*_for``
        resolver in this suite uses. If this fails, one of three things happened:
        a ``wiki/*.md`` was committed or reverted (the working tree moved and the
        ``full`` row has to be re-measured), the loader changed what it serves
        (both rows have to be re-measured), or the committed corpus became
        identical to the working tree, in which case ``full`` and ``reduced``
        really are one population under two names and the rows must be collapsed
        rather than kept.
        """
        digest = corpus_digest(committed_documents)
        assert digest == CORPUS_DIGESTS["reduced"], (
            f"the corpus rebuilt from `git show HEAD:` digests to {digest}, but "
            f"tests/real_wiki.py::CORPUS_DIGESTS records "
            f"{CORPUS_DIGESTS['reduced']} for the `reduced` population. Every "
            "`*_for` resolver selects rows by this digest, so a mismatch means "
            "either that HEAD moved (re-measure the row) or that the loader "
            "serves something different than it did (re-measure both)."
        )
        assert digest != CORPUS_DIGESTS["full"], (
            "the committed corpus and the working tree are now the SAME corpus. "
            "`full` and `reduced` would be one population under two names, which "
            "is a figure nobody can check -- collapse the two rows instead of "
            "keeping both."
        )


# ── The figures ──────────────────────────────────────────────────────────────


class TestTheReducedRowDescribesTheCommittedCorpus:
    def test_the_recall_figures_are_the_committed_corpus_ones(
        self, measured, embedder_block
    ):
        """recall@1 / recall@3 / MRR@5 on the row labelled ``reduced``.

        The defect this pinned the first time, measured on a real clone of this
        branch: published 0.7317 / 0.8293 / 0.7935, measured on the committed
        corpus 0.7561 / 0.8537 / 0.8118. Same 33 pages, same 116 chunks, same 41
        questions -- different text, therefore a different ranking.

        It pins again, harder, on the 2026-10-04 corpus: published
        0.7561 / 0.8537 / 0.8118, measured 0.7551 / 0.8163 / 0.7980. Now both
        sides are 37 pages, 49 questions and 124 chunks, so ``recall@3`` agrees
        by coincidence while ``recall@1`` and ``MRR@5`` disagree by a question
        and by 0.0177 -- which is what makes this file a guard on the TEXT and
        not on the shape.
        """
        row = _population_row(embedder_block, REDUCED)

        for pattern, key in (
            (r"recall@1\s+([\d.]+)", "recall1"),
            (r"recall@3\s+([\d.]+)", "recall3"),
            (r"MRR@5\s+([\d.]+)", "mrr5"),
        ):
            stated = _figure(row, pattern)
            assert stated == pytest.approx(measured[key], abs=0.0005), (
                f"the comment's reduced {key} is {stated}; the COMMITTED corpus "
                f"measures {measured[key]:.4f}. This row is read by whoever "
                "clones, so it has to be measured on the committed corpus and "
                "not on a working tree whose 15 modified wiki/*.md files are "
                "never pushed. Re-measure here and update "
                "tests/real_wiki.py::COMMENT_FIGURES_REDUCED plus this comment "
                "-- do not edit the number to match a corpus nobody else has."
            )

    def test_the_top_k3_figure_is_the_committed_corpus_one(self, measured):
        """The shipped-top_k figure, on the corpus a clone actually gets.

        ``COMMENT_FIGURES_REDUCED.recall3_at_top3`` is the one figure in this
        module that could not be derived from the author's working tree: the
        lexical rescue fires only when the per-page cut binds, which depends on
        how many pages clear the threshold, and that differs between the two
        corpora. It is measured here for the same reason every other figure in
        this file is -- the reduced row is read by whoever clones, so it has to
        be measured on what is committed.
        """
        assert REDUCED.hits3_at_top3 == measured["hits3_at_top3"], (
            f"COMMENT_FIGURES_REDUCED records {REDUCED.hits3_at_top3}/"
            f"{REDUCED.questions} at top_k=3; the committed corpus serves the "
            f"gold page for {measured['hits3_at_top3']}/{measured['questions']}. "
            "Re-measure and update tests/real_wiki.py -- do not adjust the row "
            "to fit a corpus a clone does not have."
        )
        assert REDUCED.recall3_at_top3 == pytest.approx(
            measured["recall3_at_top3"], abs=0.0005
        )

    def test_the_corpus_shape_figures_are_the_committed_corpus_ones(
        self, measured, threshold_block
    ):
        """Chunks, matrix, and both halves of the filter's cost.

        Published 124 chunks and 851/1813/962; measured 124 chunks and
        851/1813/962. Every count here now agrees with the working tree's row
        too -- same 37 pages, same 124 chunks, same 1813 unfiltered results --
        and that is not corroboration, it is the reason this file exists: a
        count that stays right while the text behind it changes is the worst kind
        of corroboration, because it makes a wrong row look checked. The three
        results that SURVIVE the filter are the figure that moved (854 against
        851), and they only moved because fifteen pages are not the fifteen the
        author has modified.
        """
        shape_row = _population_row(threshold_block, REDUCED)
        assert int(_figure(shape_row, r"(\d+)\s+chunks")) == measured["chunks"], (
            f"the reduced chunk count is not this corpus's: measured "
            f"{measured['chunks']} chunks from the committed wiki/."
        )
        matrix = re.search(
            r"(\d+)\s*x\s*(\d+)\s*\(question,\s*chunk\)", shape_row
        )
        assert matrix is not None, (
            "the reduced row must name the matrix shape it measured over as "
            f"'{REDUCED.matrix} (question, chunk)'. Row was:\n{shape_row}"
        )
        assert int(matrix.group(1)) == measured["questions"]
        assert int(matrix.group(2)) == measured["chunks"]

        cost_row = shape_row
        quoted = re.search(
            r"discards\s+(\d+)\s+of\s+(?:the\s+)?(\d+)\s+results", cost_row
        )
        assert quoted is not None, (
            "the reduced row must state the filter's cost as 'discards N of the "
            f"M results'. Row was:\n{cost_row}"
        )
        assert int(quoted.group(1)) == measured["dropped"], (
            f"the reduced filter is said to discard {quoted.group(1)} results; "
            f"the committed corpus discards {measured['dropped']} "
            f"({measured['unfiltered']} unfiltered - {measured['filtered']} "
            "filtered)."
        )
        assert int(quoted.group(2)) == measured["unfiltered"], (
            f"the reduced row's denominator is {quoted.group(2)}; the "
            f"committed corpus returns {measured['unfiltered']} unfiltered."
        )

    def test_the_floor_record_and_the_comment_row_describe_the_same_corpus(
        self, measured
    ):
        """``Measurement`` and ``CommentFigures`` must agree ON THE COMMITTED CORPUS.

        ``MEASURED_REDUCED`` owns the floor, ``COMMENT_FIGURES_REDUCED`` owns
        the figures the comments print, and nothing kept them consistent but
        ``tests/test_recall_claims.py`` -- which compares them on whichever
        population the checkout happens to be. In the author's checkout that is
        the FULL population, so the reduced pair was never compared to each
        other at all; both drifted onto the working tree independently and the
        floor inherited a measurement of a corpus that a clone does not have.
        """
        assert MEASURED_REDUCED.questions == measured["questions"]
        assert MEASURED_REDUCED.hits == measured["hits3"], (
            f"MEASURED_REDUCED records {MEASURED_REDUCED.hits}/"
            f"{MEASURED_REDUCED.questions} but the committed corpus serves "
            f"{measured['hits3']}/{measured['questions']}. The floor is derived "
            "from `hits`, so a `hits` that describes the wrong corpus is a floor "
            "over the wrong measurement. Re-measure; do not adjust the floor to "
            "fit."
        )
        assert MEASURED_REDUCED.recall3 == pytest.approx(
            measured["recall3"], abs=0.0005
        ), (
            f"MEASURED_REDUCED.recall3 is {MEASURED_REDUCED.recall3:.4f}; the "
            f"committed corpus measures {measured['recall3']:.4f}."
        )
        assert COMMENT_FIGURES_REDUCED.recall3 == pytest.approx(
            MEASURED_REDUCED.recall3, abs=0.0005
        ), (
            "a reader who finds two different recall@3 values for the reduced "
            "population has been handed a coin flip."
        )


@pytest.fixture(scope="module")
def embedder_block() -> str:
    return _block(CONFIG_PY, EMBEDDER_ANCHOR)


@pytest.fixture(scope="module")
def threshold_block() -> str:
    return _block(RAG_PY, THRESHOLD_ANCHOR)