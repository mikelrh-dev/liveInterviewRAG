"""A ``file:line`` citation is a claim about code. This makes the ones in this
repository checkable.

THE PROBLEM
-----------
Six of these citations pointed at the wrong code, and none of them could fail.
The repository already guards what a citation SAYS -- ``tests/test_packaging.py``
asserts that ``scikit-learn`` is declared in the manifest, and
``backend/requirements.txt`` says WHY with the line number -- but nothing ever
opened the cited line and looked. A citation is a promise that a reader who
follows it lands on the code being described. That promise was being made
without evidence:

  * ``backend/services/rag.py:421`` for the TF-IDF fallback import, in three
    places, while the import sat at 648. Line 421 was blank.
  * ``frontend/app.js:1728`` for the SSE line filter, in two places, while the
    filter was at 2092. Line 1728 was inside an audio-playback ``.catch()``.
  * ``tests.yml:174`` for the ``pip install -e ".[dev]"`` CI performs, while the
    command was at 198. Line 174 was a comment explaining why the previous
    approach was wrong.
  * ``backend/config.py:41-42`` for ``WHISPER_DEVICE=cpu``, while the Whisper
    settings were at 60-62. Lines 41-42 were prose about empty environment
    variables.
  * ``backend/config.py:167`` for a comment about ``os.getenv("CORS_ORIGINS")``,
    which is in ``backend/main.py``, not ``config.py`` at all.

Each was individually harmless and collectively load-bearing: they are the
mechanism by which a reader navigates to the code instead of searching for it.

THE DESIGN, AND WHY THE LINE IS NOT IN THE TABLE
------------------------------------------------
Each row stores the citation as a PATTERN that captures its own line, plus what
that line must contain. The line is read out of the citing file, never pinned
here. That choice is the whole test:

  * Pinning the line in the table would make the table a second copy of the
    claim, and fixing a citation would mean editing the test -- a guard you
    update in the same commit as the thing it guards is a guard that cannot fail.
  * Reading it means the guard asks the only question that matters: does the
    line this file NAMES hold the code this file SAYS it holds? A citation
    edited to a wrong line fails. A line that drifts away from its citation
    fails, which is also correct: the citation is now wrong.

So the guard is not brittle to line drift in the annoying way. It is strict
about the one thing worth being strict about.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not parse Markdown. ``README.md`` and ``README_ES.md`` carry citations
too, and those were wrong as well; a prose file is reviewed by a human reading
it, and a regex over Markdown is a Markdown parser with none of the parts that
matter.

It does not cover the Node test files, whose ``app.js:NNNN`` references are
defect NARRATIVES -- where a bug used to be -- rather than claims about where
code is now. ``tests/frontend/turn_narration.test.mjs:11`` cites ``app.js:1477``
for "the status was frozen at", and 1477 is the ``mediaRecorder.stop()``
condition in ``stopRecording()``; there is no line to re-derive, because the
sentence is about a bug that no longer exists at that address. Pinning it would
be pinning a rumour.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Citation:
    """One claim: ``citing`` names some lines of ``resolved``; here is the proof."""

    citing: str
    """Path of the file that makes the claim, relative to the repository root."""

    pattern: str
    """The citation as written, with the line number CAPTURED.

    Every occurrence in the citing file is checked, so a file that repeats a
    citation in two docstrings cannot have one of them fixed and the other left
    pointing at nothing.
    """

    resolved: str
    """Repository-relative path the citation is about."""

    markers: Tuple[str, ...]
    """Any one of these appearing in the cited lines proves the claim.

    "Any", not "all", because a cited line is prose or code and pinning every
    word of it would make the guard a change detector rather than a check.
    """

    why: str
    """What the citation claims, restated for the failure message."""


#: The table. Order is by citing file so a diff to it reads as a sweep.
CITATIONS: Tuple[Citation, ...] = (
    Citation(
        citing=".env.example",
        pattern=r"deployment/interviewtts\.service:(\d+)",
        resolved="deployment/interviewtts.service",
        markers=("ExecStart=", "--host 127.0.0.1"),
        why="the deployed service binds loopback, which is why .env.example declares no HOST",
    ),
    Citation(
        citing="backend/config.py",
        pattern=r"deployment/interviewtts\.service:(\d+)",
        resolved="deployment/interviewtts.service",
        markers=("ExecStart=", "--host 127.0.0.1"),
        why="config does not read the bind address, so it points at where it IS read",
    ),
    Citation(
        citing="backend/requirements.txt",
        pattern=r"backend/services/rag\.py:(\d+)(?:-(\d+))?",
        resolved="backend/services/rag.py",
        markers=("import yaml", "TfidfVectorizer"),
        why=(
            "the two third-party imports of rag.py that the manifest declares: "
            "pyyaml at module level and scikit-learn for the TF-IDF fallback"
        ),
    ),
    Citation(
        citing="pyproject.toml",
        pattern=r"backend/services/rag\.py:(\d+)(?:-(\d+))?",
        resolved="backend/services/rag.py",
        markers=("import yaml", "TfidfVectorizer"),
        why=(
            "the two third-party imports of rag.py that the manifest declares: "
            "pyyaml at module level and scikit-learn for the TF-IDF fallback"
        ),
    ),
    Citation(
        citing=".github/workflows/tests.yml",
        pattern=r"backend/services/rag\.py:(\d+)(?:-(\d+))?",
        resolved="backend/services/rag.py",
        markers=("import yaml", "TfidfVectorizer"),
        why=(
            "the two third-party imports of rag.py that the manifest declares: "
            "pyyaml at module level and scikit-learn for the TF-IDF fallback"
        ),
    ),
    Citation(
        citing="backend/requirements.txt",
        pattern=r"backend/middleware\.py:(\d+)",
        resolved="backend/middleware.py",
        markers=("from fastapi",),
        why="fastapi is a module-level import of middleware.py",
    ),
    Citation(
        citing="pyproject.toml",
        pattern=r"backend/middleware\.py:(\d+)",
        resolved="backend/middleware.py",
        markers=("from fastapi",),
        why="fastapi is a module-level import of middleware.py",
    ),
    Citation(
        citing=".github/workflows/tests.yml",
        pattern=r"backend/middleware\.py:(\d+)",
        resolved="backend/middleware.py",
        markers=("from fastapi",),
        why="fastapi is a module-level import of middleware.py",
    ),
    Citation(
        citing="backend/sse.py",
        pattern=r"frontend/app\.js:(\d+)",
        resolved="frontend/app.js",
        markers=('startsWith("data: ")',),
        why="the keepalive frame is skipped by the page's SSE line filter",
    ),
    Citation(
        citing="backend/sse.py",
        pattern=r"streaming\.py:(\d+)-(\d+)",
        resolved="backend/turns/streaming.py",
        markers=("finally:", "temp_audio.unlink"),
        why="the finally that reaps in-flight synthesis and unlinks the staged upload",
    ),
    Citation(
        citing="backend/services/response_cache.py",
        pattern=r"streaming\.py:(\d+)",
        resolved="backend/turns/streaming.py",
        markers=("source != LLM",),
        why="where a cache hit returns before the RAG stage runs",
    ),
    Citation(
        citing="scripts/deploy.sh",
        pattern=r"backend/maintenance\.py:(\d+)(?:-(\d+))?",
        resolved="backend/maintenance.py",
        markers=("periodic_cleanup",),
        why="the sweep that unlinks audio/, so audio/ is created and never rsynced",
    ),
    Citation(
        citing="deployment/interviewtts.service",
        pattern=r"backend/services/rag\.py:(\d+)(?:-(\d+))?",
        resolved="backend/services/rag.py",
        markers=("Failed to save embedding cache",),
        why="an unwritable rag cache/ is a warning nobody reads, not a boot failure",
    ),
    Citation(
        citing="tests/test_sse_keepalive.py",
        pattern=r"backend/config\.py:(\d+)(?:-(\d+))?",
        resolved="backend/config.py",
        markers=("WHISPER_DEVICE",),
        why="the shipped Whisper settings that make the quiet window the whole of STT",
    ),
    Citation(
        citing="tests/test_sse_keepalive.py",
        pattern=r"frontend/app\.js:(\d+)(?:-(\d+))?",
        resolved="frontend/app.js",
        markers=('startsWith("data: ")',),
        why="the exact line filter the keepalive frame has to survive",
    ),
    Citation(
        citing="tests/test_config_surface.py",
        pattern=r"backend/main\.py:(\d+)",
        resolved="backend/main.py",
        markers=('os.getenv("CORS_ORIGINS")',),
        why=(
            "the comment that mentions a key in prose, which is why this module "
            "parses with ast instead of a regex"
        ),
    ),
    Citation(
        citing="tests/test_deploy_path.py",
        pattern=r"backend/config\.py:(\d+)(?:-(\d+))?",
        resolved="backend/config.py",
        markers=("RAG_CACHE_DIR",),
        why="the single source of truth the unit file's path is derived from",
    ),
    Citation(
        citing="tests/test_deploy_path.py",
        pattern=r"(?<!backend/)config\.py:(\d+)",
        resolved="backend/config.py",
        markers=("self.BASE_DIR",),
        why="BASE_DIR is the repository root, which is what makes the derivation work",
    ),
    Citation(
        citing="tests/test_deploy_path.py",
        pattern=r"backend/maintenance\.py:(\d+)(?:-(\d+))?",
        resolved="backend/maintenance.py",
        markers=("periodic_cleanup",),
        why="the sweep that unlinks audio/",
    ),
    Citation(
        citing="tests/test_deploy_path.py",
        pattern=r"backend/services/rag\.py:(\d+)(?:-(\d+))?",
        resolved="backend/services/rag.py",
        markers=("Failed to save embedding cache",),
        why="the cache write failure that is only a warning",
    ),
    Citation(
        citing="tests/test_response_cache_precision.py",
        pattern=r"(?:backend/turns/)?streaming\.py:(\d+)",
        resolved="backend/turns/streaming.py",
        markers=("source != LLM",),
        why="where a cache hit short-circuits the LLM",
    ),
    Citation(
        citing="tests/test_report_service.py",
        pattern=r"backend/main\.py:(\d+)",
        resolved="backend/main.py",
        markers=("cleanup_expired",),
        why="a cleanup_expired caller that passes no days, so the zero-day window is test-only",
    ),
    Citation(
        citing="tests/test_report_service.py",
        pattern=r"backend/maintenance\.py:(\d+)",
        resolved="backend/maintenance.py",
        markers=("cleanup_expired",),
        why="the only other cleanup_expired caller, which also passes no days",
    ),
)


def _lines_of(relative: str) -> list:
    path = REPO_ROOT / relative
    assert path.is_file(), f"{relative} does not exist"
    return path.read_text(encoding="utf-8").splitlines()


def _occurrences(citation: Citation) -> list:
    """Every ``(citing line, cited line range)`` this citation currently makes."""
    found = []
    for number, line in enumerate(_lines_of(citation.citing), 1):
        match = re.search(citation.pattern, line)
        if match:
            numbers = [int(n) for n in match.groups() if n]
            found.append((number, min(numbers), max(numbers)))
    return found


def _cited_text(citation: Citation, start: int, end: int) -> str:
    lines = _lines_of(citation.resolved)
    return "\n".join(line for line in lines[start - 1 : end] if line.strip())


def _all_offenders() -> Tuple[str, ...]:
    """Every row, every occurrence, every way it can be wrong."""
    problems = []
    for citation in CITATIONS:
        occurrences = _occurrences(citation)
        if not occurrences:
            problems.append(
                f"{citation.citing} no longer contains {citation.pattern!r}, so "
                f"this row guards nothing. It was cited for {citation.why}."
            )
            continue
        for citing_line, start, end in occurrences:
            target = _lines_of(citation.resolved)
            if start < 1 or end > len(target):
                problems.append(
                    f"{citation.citing}:{citing_line} cites {citation.resolved}:"
                    f"{start}{'-' + str(end) if end != start else ''}, which is "
                    f"outside the file's {len(target)} lines. It claims {citation.why}."
                )
                continue
            cited = _cited_text(citation, start, end)
            if not any(marker in cited for marker in citation.markers):
                problems.append(
                    f"{citation.citing}:{citing_line} cites {citation.resolved}:"
                    f"{start}{'-' + str(end) if end != start else ''} for "
                    f"{citation.why}, but that line says:\n"
                    f"      {cited.strip()[:180]}\n"
                    f"    expected one of: {', '.join(citation.markers)}"
                )
    return tuple(problems)


class TestEveryFileLineCitationLandsOnTheCodeItClaims:
    def test_each_cited_line_holds_what_the_citation_is_about(self):
        offenders = _all_offenders()

        assert not offenders, (
            f"{len(offenders)} citation(s) point at code that is not the code "
            "they are about:\n  "
            + "\n  ".join(offenders)
            + "\nRe-derive each line by opening the file, then update the "
            "citation. Do not relax the marker: a citation nobody can follow is "
            "a comment shaped like a pointer."
        )

    def test_every_row_is_written_in_its_citing_file(self):
        """The half that stops the table rotting.

        Without it, deleting every citation in the repository would leave this
        guard passing -- it would be checking rows whose text is gone.
        """
        orphans = [
            f"{c.citing} does not contain {c.pattern!r}"
            for c in CITATIONS
            if not _occurrences(c)
        ]
        assert not orphans, (
            "these rows point at citations that are not in the files they name:\n  "
            + "\n  ".join(orphans)
            + "\nEither the citation moved to another file, or it was deleted and "
            "the row should go with it."
        )

    def test_the_table_has_no_duplicate_rows(self):
        seen = set()
        duplicates = []
        for citation in CITATIONS:
            key = (citation.citing, citation.pattern, citation.resolved, citation.markers)
            if key in seen:
                duplicates.append(f"{citation.citing} + {citation.pattern!r}")
            seen.add(key)
        assert not duplicates, (
            "the table checks the same claim twice, which inflates the number of "
            "guarded citations:\n  " + "\n  ".join(duplicates)
        )

    def test_every_marker_is_non_empty(self):
        for citation in CITATIONS:
            assert citation.markers, f"{citation.pattern!r} has no marker"
            for marker in citation.markers:
                assert marker.strip(), (
                    f"{citation.citing} + {citation.pattern!r} has a blank "
                    "marker, which every line matches. A blank marker is the "
                    "guard this file replaces: it always passes."
                )
