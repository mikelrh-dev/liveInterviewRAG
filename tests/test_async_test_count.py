"""The number of async tests is a fact about this repository, and two files
state it. This keeps them stating the fact.

THE DEFECT
----------
``RUNBOOK.md`` told a developer that setting ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``
makes "los 25 tests async" fail falsely. ``.github/workflows/tests.yml`` told a
reader that the same variable makes "8 async tests" fail. They disagreed with
each other, and neither agreed with the repository, which has 28.

Neither number was ever measured. The 8 came from an audit of 2026-09-27, taken
before eleven async tests existed. The 25 was nobody's measurement either: it is
what you get by counting one of the five files. Both were written down, both
compile, and a reader who trusted either would conclude the environment
variable is less dangerous than it is -- which is the one conclusion this
number must never support, because the failure it describes is 28 tests
failing with "async def functions are not natively supported", an error that
names the plugin rather than the cause.

HOW THE NUMBER IS COUNTED
-------------------------
Statically, with :mod:`ast`, over every ``tests/**/test_*.py``: the count is
the number of ``async def test_*`` functions, at module level or inside a
class. That is the same population pytest-asyncio has to run, and it is derived
rather than transcribed, so re-running this is instant and needs no interpreter
with the plugin disabled.

Cross-checked against the real thing before it was written down. With
``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` and the plugin therefore never
auto-loaded, the full suite reports exactly 28 failures and every one of them
carries "async def functions are not natively supported"; the other 974 pass.
Twenty-eight is also what the AST finds, so the two agree and the number below
is not a transcription of one method's opinion.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: Where the number is stated and how to read it out. The prose differs per
#: file -- one is English, one is Spanish -- so the pattern is per row rather
#: than one clever expression that matches both and neither.
STATED_IN = (
    (
        ".github/workflows/tests.yml",
        r"(\d+)\s+async tests fail",
        "the CI environment trap that reproduces this on purpose",
    ),
    (
        "RUNBOOK.md",
        r"los\s+(\d+)\s+tests async",
        "the developer-facing version of the same trap",
    ),
)


def _async_test_functions() -> int:
    """Every ``async def test_*`` under ``tests/``, module level or in a class.

    Parsed rather than grepped because the decorator is not the signal: the
    repository sets ``asyncio_mode = "auto"``, so an async test needs no marker,
    and counting ``@pytest.mark.asyncio`` would count a subset.
    """
    total = 0
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test"):
                total += 1
            elif isinstance(node, ast.ClassDef):
                total += sum(
                    1
                    for member in node.body
                    if isinstance(member, ast.AsyncFunctionDef)
                    and member.name.startswith("test")
                )
    return total


class TestTheAsyncTestCountIsStatedWhereItIsWarnedAbout:
    def test_there_are_async_tests_for_the_number_to_be_about(self):
        """Non-vacuity. A count of zero would make both rows trivially true.

        Asserted on the AST count rather than on the documents, so the two
        cannot be wrong together.
        """
        assert _async_test_functions() > 0, (
            "no async test functions were found under tests/, so a document "
            "claiming '0 async tests fail' would be right. Either the traversal "
            "is broken or every async test was deleted -- and the warning these "
            "two files carry is the only thing telling a developer why their "
            "suite is red."
        )

    def test_each_document_states_the_real_count(self):
        real = _async_test_functions()
        wrong = []
        for relative, pattern, why in STATED_IN:
            path = REPO_ROOT / relative
            assert path.is_file(), f"{relative} does not exist"
            match = re.search(pattern, path.read_text(encoding="utf-8"))
            if match is None:
                wrong.append(
                    f"{relative} no longer states the count in the form "
                    f"{pattern!r} ({why}). Re-word it and keep the number, or "
                    "drop the row -- but do not leave a warning with no number."
                )
                continue
            stated = int(match.group(1))
            if stated != real:
                wrong.append(
                    f"{relative} says {stated} async tests fail without "
                    f"pytest-asyncio; there are {real} ({why}). A reader who "
                    f"trusts {stated} expects {real - stated} failures they "
                    "never see and has no way to know the rest are not missing."
                )

        assert not wrong, "\n".join([""] + wrong)

    def test_the_two_documents_do_not_disagree_with_each_other(self):
        """The defect was a contradiction before it was a wrong number.

        Checked separately from the count so that a future edit making the two
        files disagree is reported as a disagreement, which is the cheaper
        thing to diagnose than as two wrong numbers.
        """
        stated = {}
        for relative, pattern, _ in STATED_IN:
            match = re.search(pattern, (REPO_ROOT / relative).read_text(encoding="utf-8"))
            stated[relative] = int(match.group(1)) if match else None

        values = {v for v in stated.values() if v is not None}
        assert len(values) == 1, (
            "these files warn about the same environment variable and state "
            f"different counts for it: {stated}. A runbook and a CI workflow "
            "that disagree about what a broken environment looks like means "
            "neither can be used to recognise one."
        )
