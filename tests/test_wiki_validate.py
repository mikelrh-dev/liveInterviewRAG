"""Strict-TDD tests for scripts/wiki/validate.py (RED first).

All tests run against copies of tests/fixtures/wiki/* placed in pytest's
tmp_path — never against the real wiki/ or candidate/.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "wiki"
VALIDATE = REPO_ROOT / "scripts" / "wiki" / "validate.py"


def run_validate(wiki_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE), "--wiki", str(wiki_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def snapshot_mtimes(root: Path) -> dict[str, float]:
    return {
        str(p.relative_to(root)): p.stat().st_mtime
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def copy_fixture(name: str, tmp_path: Path) -> Path:
    src = FIXTURES / "good" if name == "good" else FIXTURES / "bad" / name
    dest = tmp_path / name
    shutil.copytree(src, dest)
    return dest


class TestGoodWiki:
    def test_good_wiki_exits_zero(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "[ERROR]" not in result.stdout

    def test_good_wiki_writes_nothing(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        before = snapshot_mtimes(wiki)
        result = run_validate(wiki)
        after = snapshot_mtimes(wiki)
        assert result.returncode == 0
        assert before == after, "validate must be strictly read-only"

    def test_summary_line_ok_format(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        result = run_validate(wiki)
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        assert lines[-1].startswith("OK: ")
        assert "files valid" in lines[-1]

    def test_skipped_files_not_flagged(self, tmp_path):
        """CONVENCIONES.md, index.md, templates/, README.md have no frontmatter
        but must be skipped silently."""
        wiki = copy_fixture("good", tmp_path)
        (wiki / "index.md").write_text("# no frontmatter here\n", encoding="utf-8")
        result = run_validate(wiki)
        assert result.returncode == 0, result.stdout


class TestErrorClasses:
    def test_missing_required_field_errors(self, tmp_path):
        wiki = copy_fixture("missing-field", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        assert "projects/missing-field.md" in result.stdout
        assert "confidence" in result.stdout.lower()

    def test_type_folder_mismatch_errors(self, tmp_path):
        wiki = copy_fixture("type-folder-mismatch", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        assert "projects/interview-tts.md" in result.stdout

    def test_broken_related_link_errors(self, tmp_path):
        wiki = copy_fixture("broken-link", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        assert "skills/nonexistent-file.md" in result.stdout.replace("\\", "/")

    def test_bad_date_errors(self, tmp_path):
        wiki = copy_fixture("bad-date", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        assert "profile/mikel.md" in result.stdout

    def test_long_summary_errors(self, tmp_path):
        wiki = copy_fixture("long-summary", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        assert "faq/long-summary.md" in result.stdout

    def test_failed_summary_line_format(self, tmp_path):
        wiki = copy_fixture("missing-field", tmp_path)
        result = run_validate(wiki)
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        assert lines[-1].startswith("Validation failed:")
        assert "errors," in lines[-1]
        assert "warnings" in lines[-1]


class TestWarningsNonBlocking:
    def test_asymmetric_links_warn_but_exit_zero(self, tmp_path):
        wiki = copy_fixture("asymmetric-links", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 0, result.stdout
        assert "[WARN]" in result.stdout

    def test_stale_low_confidence_warns_but_exits_zero(self, tmp_path):
        wiki = copy_fixture("stale-low-confidence", tmp_path)
        result = run_validate(wiki)
        assert result.returncode == 0, result.stdout
        assert "[WARN]" in result.stdout


class TestUsageAndIoErrors:
    def test_nonexistent_wiki_path_is_usage_error(self, tmp_path):
        result = run_validate(tmp_path / "does-not-exist")
        assert result.returncode == 2

    def test_wiki_pointing_at_file_is_usage_error(self, tmp_path):
        plain_file = tmp_path / "plain.md"
        plain_file.write_text("hello", encoding="utf-8")
        result = run_validate(plain_file)
        assert result.returncode == 2
