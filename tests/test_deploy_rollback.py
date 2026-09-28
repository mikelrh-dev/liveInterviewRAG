"""The documented rollback must not be able to destroy the live tree.

THE DEFECT
----------
``scripts/deploy.sh`` ended by printing, and ``README.md`` repeated, a one-liner
for the operator to paste::

    mv candidate candidate.broken && mv candidate.prev candidate

The failure is in the sequencing, not in either ``mv``. If ``candidate.prev``
does not exist, the first ``mv`` still succeeds — it only needs ``candidate/`` —
and the second then fails. The live tree has been renamed out of the way, the
path the service is serving no longer exists, and the command reports failure
after having destroyed exactly the content it was invoked to restore. The
``&&`` looks like a guard and is not one.

This is not a hypothetical. The backup is created by step 3 of the same script,
which drops the previous ``candidate.prev`` *before* moving the live tree into
place, so a deploy that aborts between the two leaves no backup at all — which
is precisely the state a reader reaches for the rollback command in, and the
worst possible moment to run it.

WHY A SHELL TEST AND NOT A STRING ASSERTION
-------------------------------------------
``tests/test_nginx_config.py`` proves its point by parsing a config file, and
that is the right call for nginx. It is the wrong call here: the claim is about
what a sequence of ``mv`` invocations does to a directory when the second one
fails, and the only honest way to establish that is to run it. These tests
source the real ``deploy.sh``, point ``REMOTE_DIR`` at a tmp_path, and replace
``ssh_cmd`` with a local shell. The function under test is the shipped one, not
a transcription of it.

``bash`` is the one hard requirement. Where it is absent the suite skips rather
than pretends, because a test that quietly degrades into a string match is the
exact failure mode this file exists to avoid.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"

#: What a ``sudo systemctl restart interviewtts.service`` line is rewritten to.
#: The restart is not what is under test, and a test machine has no such unit.
RESTART_TO_NOOP = ("sudo systemctl restart interviewtts.service", ":")

#: Git Bash first, and deliberately before ``shutil.which``: on Windows
#: ``C:\Windows\system32\bash.exe`` is the WSL launcher, which cannot see this
#: checkout at all and fails with a bare "No such file or directory" that reads
#: like a bug in the script under test. An honest skip is better than that.
BASH = next(
    (
        p
        for p in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            shutil.which("bash"),
        )
        if p and Path(p).exists()
    ),
    None,
)

pytestmark = pytest.mark.skipif(
    BASH is None, reason="bash is required to execute deploy.sh's rollback"
)


def _run_bash(script: str, remote: Path) -> subprocess.CompletedProcess:
    """Run bash with ``deploy.sh`` sourced and ``ssh_cmd`` faked to be local.

    ``REMOTE_DIR`` is set to ``remote`` itself, so the paths inside the shipped
    commands are already correct and nothing has to be rewritten but the
    systemctl line. The fake is faithful in the only way that matters: the
    commands run as real ``mv`` calls against a real directory.
    """
    program = f"""
set -uo pipefail
export REMOTE_DIR='{remote.as_posix()}'
source '{DEPLOY_SH.as_posix()}'
ssh_cmd() {{
    local cmd="${{1//{RESTART_TO_NOOP[0]}/{RESTART_TO_NOOP[1]}}}"
    bash -c "$cmd"
}}
{script}
"""
    return subprocess.run(
        [BASH, "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A remote tree with a live candidate/ and a previous good copy."""
    root = tmp_path / "opt" / "interviewtts"
    (root / "candidate" / "docs").mkdir(parents=True)
    (root / "candidate" / "docs" / "live.md").write_text("LIVE", encoding="utf-8")
    (root / "candidate.prev" / "docs").mkdir(parents=True)
    (root / "candidate.prev" / "docs" / "live.md").write_text("PREVIOUS", encoding="utf-8")
    return root


def _body(root: Path, which: str = "candidate") -> str:
    path = root / which / "docs" / "live.md"
    return path.read_text(encoding="utf-8") if path.exists() else "<absent>"


class TestAMissingBackupLeavesTheLiveTreeIntact:
    def test_the_rollback_refuses_to_run_at_all(self, remote):
        # The state a deploy aborted between "drop the stale backup" and "move
        # the live tree aside" leaves behind: candidate/ live, no candidate.prev/.
        shutil.rmtree(remote / "candidate.prev")

        result = _run_bash("rollback; echo \"exit=$?\"", remote)

        assert "exit=0" not in result.stdout, (
            "rollback reported success with no backup to roll back to:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    def test_candidate_is_still_there_afterwards(self, remote):
        """The assertion that matters: nothing moved.

        Checked on the directory tree, not only on the command's exit code,
        because the original defect exits non-zero AND destroys the content --
        an exit-code check alone would have called that a pass.
        """
        shutil.rmtree(remote / "candidate.prev")

        result = _run_bash("rollback || true", remote)

        assert _body(remote) == "LIVE", (
            f"the live content is {_body(remote)!r} after a refused rollback; it "
            "should still be 'LIVE'. Nothing may be renamed until the backup is "
            f"known to exist.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not (remote / "candidate.broken").exists(), (
            "candidate.broken/ was created, so candidate/ WAS renamed -- which is "
            "the first half of the original defect"
        )

    def test_it_says_what_is_missing_rather_than_only_failing(self, remote):
        shutil.rmtree(remote / "candidate.prev")

        result = _run_bash("rollback || true", remote)

        assert "candidate.prev" in result.stderr, (
            f"the abort must name the directory it could not find, or the "
            f"operator is left reading 'ABORT' with no idea what to fix.\n"
            f"stderr was: {result.stderr!r}"
        )
        assert "Nothing was moved" in result.stderr, (
            "the abort must state that nothing was touched. That sentence is the "
            f"whole difference between a refused rollback and a destructive one.\n"
            f"stderr was: {result.stderr!r}"
        )


class TestTheGuardIsAboutExistenceNotAboutOrder:
    def test_a_backup_that_is_a_file_not_a_directory_is_refused(self, remote):
        """`test -d` on purpose.

        A zero-byte `candidate.prev` left by a failed rsync is the shape a
        half-finished deploy takes. `mv` would happily move a file over the
        live directory; only the directory test catches it.
        """
        shutil.rmtree(remote / "candidate.prev")
        (remote / "candidate.prev").write_text("", encoding="utf-8")

        result = _run_bash("rollback || true", remote)

        assert _body(remote) == "LIVE", (
            "a file named candidate.prev was treated as a usable backup"
        )

    def test_a_missing_live_tree_is_refused_too(self, remote):
        """The guard is symmetric, and deliberately so.

        Without it the swap would `rm -rf candidate.broken` and then fail on
        `mv candidate candidate.broken`, having destroyed the only other copy of
        anything -- the previous previous.
        """
        shutil.rmtree(remote / "candidate")

        result = _run_bash("rollback || true", remote)

        assert _body(remote, "candidate.prev") == "PREVIOUS", (
            "with no live tree, the backup must be left exactly where it was; "
            f"it is now {_body(remote, 'candidate.prev')!r}"
        )


class TestTheHappyPathStillWorks:
    def test_a_real_rollback_restores_the_previous_content(self, remote):
        result = _run_bash("rollback", remote)

        assert result.returncode == 0, f"rollback failed: {result.stderr}"
        assert _body(remote) == "PREVIOUS", (
            f"after a successful rollback the live tree is {_body(remote)!r}, "
            "expected 'PREVIOUS'"
        )
        assert not (remote / "candidate.prev").exists(), (
            "the backup is consumed by a rollback, so a second rollback would "
            "find nothing to restore -- which is exactly the state the guard "
            "above is for"
        )
        assert _body(remote, "candidate.broken") == "LIVE", (
            "the tree that was live before the rollback must be kept under "
            "candidate.broken/, not deleted: it is the only way back"
        )

    def test_the_documented_rollback_is_the_tested_one(self):
        """The script and the README must not offer two different rollbacks.

        A guard that only the script has is a guard the operator does not get,
        because the README is where they will look. Only fenced code blocks are
        scanned: the README explains WHY the two-``mv`` form is destructive, and
        quoting it in prose is the opposite of removing it.
        """
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        assert "deploy.sh rollback" in readme, (
            "README.md must document the rollback the script actually ships. A "
            "hand-written `mv candidate candidate.broken && mv candidate.prev "
            "candidate` pasted into the README is the unguarded version, and it "
            "is the one a reader will copy."
        )

        blocks = re.findall(r"```[a-z]*\n(.*?)```", readme, re.DOTALL)
        assert blocks, "no fenced code block found in README.md; the scan is blind"
        for block in blocks:
            assert not re.search(
                r"mv\s+\S*candidate\S*\s+\S*candidate\.broken", block
            ), (
                "README.md still shows the unguarded two-`mv` rollback in a "
                f"code block a reader can copy:\n{block}"
            )


class TestTheScriptIsWellFormed:
    def test_it_parses(self):
        result = subprocess.run(
            [BASH, "-n", str(DEPLOY_SH)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, f"deploy.sh is not valid bash: {result.stderr}"

    def test_sourcing_it_does_not_deploy(self):
        """It is sourced by the tests above; it must not run the pipeline then.

        A script whose top level performs the deployment cannot be unit tested
        at all, which is how an unguarded rollback survives for months.
        """
        result = subprocess.run(
            [BASH, "-c", f"source '{DEPLOY_SH.as_posix()}' && echo SOURCED-CLEAN"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "SOURCED-CLEAN" in result.stdout, (
            f"sourcing deploy.sh produced no clean marker:\n{result.stdout}\n{result.stderr}"
        )
        assert "Validating wiki" not in result.stdout, (
            "sourcing deploy.sh ran the deploy pipeline"
        )
