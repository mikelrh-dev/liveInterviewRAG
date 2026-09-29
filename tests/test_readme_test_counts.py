"""The READMEs' test counts must agree with the suites they describe.

THE DEFECT
----------
``README.md`` stated 690 Python tests in five places (lines 49, 193, 482, 500
and 510) while the suite ran 695. The number was written before the last few
commits landed and nothing noticed, because a hardcoded count in prose is not
a fact that can go out of date -- it is a fact that quietly becomes a lie.

``README_ES.md`` meanwhile said 695, so the two files of the same project
disagreed with each other and only one of them was wrong.

WHY THIS IS A TEST AND NOT A DOCUMENTATION FIX
----------------------------------------------
The number cannot be removed: "716 Python tests" carries real information
about the size of the suite, and replacing it with "the Python suite" would
trade a checkable claim for a vague one. A count in a static document is
inherently perishable, so the honest move is not to fight that -- it is to
make the drift LOUD instead of silent.

WHY THE COUNT IS MEASURED, NOT CONSTANTED
-----------------------------------------
The first version of this file pinned the expected number in a module
constant and compared the READMEs against it. It passed, and it was worthless:
a constant nobody re-derives is a second hardcoded count. Adding a test
without touching the constant left the READMEs, the constant and each other
all still in agreement, and the suite green -- the drift had simply moved
into the file whose job was to catch it.

So the number comes from ``pytest --collect-only``, run as a subprocess. It
costs about a second and it makes the assertion falsifiable: the next commit
that adds a test turns this file red and names the numbers to update, which
is the only moment anyone was ever going to look.

WHAT IS MEASURED AND WHAT IS STILL A CONSTANT
---------------------------------------------
The collected total is measured. ``XFAILED`` is a hand-maintained constant,
because the collector does not report how many collected tests are expected
to fail -- that number is only visible in a real run. So an added ``xfail`` is
caught by the transcript assertion (it changes ``passed`` without changing
``collected``), but a *removed* one is not. That is the residual gap, and it
is small and one-directional.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READMES = (REPO_ROOT / "README.md", REPO_ROOT / "README_ES.md")

#: How many collected tests are expected to fail. See the module docstring:
#: the collector cannot know this, so it is asserted rather than measured.
#:
#: IT WAS 2 AND IT WAS FALSE. The suite runs 0 xfailed, and because
#: ``passing = collected - XFAILED`` below DERIVED the passing total from this
#: constant, the two errors cancelled: the READMEs and this file agreed with
#: each other, both went green, and both were wrong by the same two. That is the
#: specific failure this module exists to prevent, reproduced inside the module
#: that exists to prevent it -- a false constant plus a derived expectation is
#: self-certifying and nothing downstream can detect it.
#:
#: It is 0 as of 2026-09-29, measured on a real run. The honest cost of 0 is
#: that the transcript regex below has to accept a transcript with no xfailed
#: clause, because ``pytest -q`` does not print one when nothing is expected to
#: fail -- "734 passed in 313.51s" is what a clean run actually looks like, and
#: a README showing "0 xfailed" would be showing something pytest never printed.
XFAILED = 0

#: What `node --test "tests/frontend/*.test.mjs"` reports. This IS a constant,
#: because measuring it means running a Node subprocess from the Python suite
#: for a count the Node side already prints on its own CI run. Regenerate with
#:   node --test "tests/frontend/*.test.mjs"
NODE_TESTS = 263

#: "716 Python tests", "716 tests de Python".
_PYTHON_COUNT_RE = re.compile(r"(\d+)\s+(?:Python\s+tests|tests\s+de\s+Python)", re.I)

#: The sample terminal transcript, e.g. "# -> 736 passed, 2 xfailed".
#:
#: The xfailed clause is OPTIONAL and that is load-bearing, not laxity. pytest
#: prints no xfailed clause at all when nothing is expected to fail, so a
#: transcript of a run with XFAILED == 0 reads "734 passed in 313.51s". Requiring
#: the clause would make it impossible to quote a clean run truthfully, which
#: would push an author into inventing a clause pytest never emitted -- trading a
#: checkable transcript for an unfalsifiable one. When the clause IS present it
#: is checked against XFAILED exactly as before.
_TRANSCRIPT_RE = re.compile(r"(\d+)\s+passed(?:,\s*(\d+)\s+xfailed)?", re.I)

#: "263 Node tests", "263 tests de Node", "263 de Node".
#: The third form is why this is a regex and not a phrase: the Spanish file
#: abbreviates it to a bare "de Node" in the project-structure tree, and a
#: narrower pattern would have missed that occurrence and let it drift alone.
_NODE_COUNT_RE = re.compile(r"(\d+)\s+(?:Node\s+tests|tests\s+de\s+Node|de\s+Node)", re.I)

_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")


@pytest.fixture(scope="module")
def collected() -> int:
    """How many tests pytest actually collects, measured rather than declared.

    ``--collect-only`` does not execute anything, so this cannot recurse into
    the suite and cannot be slowed down by it. The venv interpreter is the one
    running the test, and plugin autoload is disabled to match how the suite is
    invoked, so a plugin installed locally cannot change the collected total.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
         "-p", "no:cacheprovider", "--collect-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={**_env_without_plugin_autoload()},
    )
    match = _COLLECTED_RE.search(result.stdout)
    assert match is not None, (
        f"could not read a collected-test count from the collector; it must "
        f"have failed or changed its output format.\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    return int(match.group(1))


def _env_without_plugin_autoload() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTEST_ADDOPTS"] = ""
    return env


@pytest.fixture(params=READMES, ids=lambda path: path.name)
def readme(request: pytest.FixtureRequest) -> str:
    return request.param.read_text(encoding="utf-8")


def test_every_python_test_count_in_the_readme_is_the_passing_total(
    readme: str, collected: int
):
    """Each stated Python count equals how many tests PASS, in every document.

    The prose count is the passing total, not the collected one: the transcript
    in the same document reads "<n> passed, 2 xfailed", and a document that
    called the collected total "N Python tests" while its own transcript said
    "N-2 passed" would be stating two different things with the same words.
    So both are compared against the measured collection, the prose against
    ``collected - XFAILED``.

    Scans every occurrence rather than asserting one location, so the five
    places the English README repeated the number cannot drift apart from each
    other -- which is the failure that made this invisible, since 690 in one
    spot and 695 in another reads as a typo rather than as a stale number.
    """
    stated = [int(n) for n in _PYTHON_COUNT_RE.findall(readme)]
    assert stated, (
        "no Python test count found in this README; the scan is blind, so it "
        "would pass a document that had stopped making the claim at all"
    )
    passing = collected - XFAILED
    assert set(stated) == {passing}, (
        f"this README states Python test counts {sorted(set(stated))} but the "
        f"suite runs {passing} passing tests ({collected} collected, "
        f"{XFAILED} xfailed)"
    )


def test_the_sample_output_transcript_matches_the_suite(readme: str, collected: int):
    """The pasted terminal output must be output this repository produces.

    A transcript in a README is the one place a reader is most likely to
    believe, because it looks measured rather than written. So it is checked
    against both numbers it claims, not just the passing one.
    """
    transcripts = _TRANSCRIPT_RE.findall(readme)
    assert transcripts, (
        "no '<n> passed[, <n> xfailed]' transcript found in this README; the "
        "scan is blind"
    )
    for passed, xfailed in transcripts:
        # An absent clause means the run reported no expected failures, which is
        # only true when XFAILED is 0. A transcript that omits the clause while
        # tests ARE expected to fail is the README being quietly wrong, and it
        # is the one direction this optional clause could have let through.
        stated_xfailed = int(xfailed) if xfailed else 0
        assert stated_xfailed == XFAILED, (
            f"this README shows a transcript of {passed} passed with no xfailed "
            f"clause, but the suite is expected to have {XFAILED} xfailed. pytest "
            f"prints the clause when there are any, so its absence is a claim "
            f"that there are none."
        )
        assert int(passed) == collected - XFAILED, (
            f"this README shows a transcript of {passed} passed; the suite "
            f"collects {collected} with {XFAILED} expected failures, i.e. "
            f"{collected - XFAILED} passing"
        )


def test_the_node_test_count_is_current(readme: str):
    """Same treatment for the frontend suite, which has its own runner.

    Kept separate from the Python count because it is produced by a different
    command, so the two are regenerated independently -- and because a single
    assertion over both would report which number is wrong by omission.
    """
    stated = [int(n) for n in _NODE_COUNT_RE.findall(readme)]
    assert stated, "no Node test count found in this README; the scan is blind"
    assert set(stated) == {NODE_TESTS}, (
        f"this README states Node test counts {sorted(set(stated))} but "
        f"`node --test` reports {NODE_TESTS} pass"
    )


def test_the_two_readmes_agree_with_each_other():
    """The disagreement itself was a defect, so it is asserted directly.

    The English file said 690 and the Spanish file said 695. Each was
    internally consistent and they were both wrong in different directions,
    which is the shape of drift nobody notices.
    """
    counts = {
        path.name: {
            int(n)
            for n in _PYTHON_COUNT_RE.findall(path.read_text(encoding="utf-8"))
        }
        for path in READMES
    }
    assert len({frozenset(found) for found in counts.values()}) == 1, (
        f"the READMEs state different Python test counts: {counts}"
    )
