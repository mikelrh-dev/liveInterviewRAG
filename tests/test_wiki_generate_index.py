"""Strict-TDD tests for scripts/wiki/generate_index.py (RED first).

All tests run against copies of tests/fixtures/wiki/good placed in pytest's
tmp_path — never against the real wiki/.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "wiki"
GENERATE = REPO_ROOT / "scripts" / "wiki" / "generate_index.py"

# Fixed section order = design §5 table order (singular type -> plural folder).
SECTION_ORDER = [
    "profile",
    "projects",
    "experience",
    "skills",
    "stories",
    "opinions",
    "decisions",
    "faq",
]


def run_generate(wiki: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GENERATE), "--wiki", str(wiki)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def make_wiki(tmp_path: Path) -> Path:
    dest = tmp_path / "wiki"
    shutil.copytree(FIXTURES / "good", dest)
    return dest


def read_index(wiki: Path) -> str:
    return (wiki / "index.md").read_text(encoding="utf-8")


def snapshot_mtimes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.stat().st_mtime_ns
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def add_page(
    wiki: Path,
    folder: str,
    stem: str,
    *,
    updated: str,
    title: str | None = None,
    summary: str = "placeholder summary line",
) -> None:
    """Drop an extra CONVENCIONES-conformant page into a copied wiki."""
    title = title or stem
    text = (
        "---\n"
        f"type: {'profile' if folder == 'profile' else folder.rstrip('s')}\n"
        f"title: {title}\n"
        f"created: 2026-01-01\n"
        f"updated: {updated}\n"
        "confidence: high\n"
        "tags: [test]\n"
        "related: []\n"
        f"summary_1line: {summary}\n"
        "---\n"
        f"\n# {title}\n"
    )
    # faq is its own plural; singular map handles the rest
    if folder == "faq":
        text = text.replace("type: faq", "type: faq")
    elif folder == "skills":
        text = text.replace("type: skill", "type: skills")
    elif folder == "experience":
        pass
    (wiki / folder / f"{stem}.md").write_text(text, encoding="utf-8")


class TestIndexStructure:
    def test_all_8_type_sections_present_in_fixed_order(self, tmp_path):
        wiki = make_wiki(tmp_path)
        result = run_generate(wiki)
        assert result.returncode == 0, result.stdout + result.stderr
        index = read_index(wiki)
        positions = []
        for folder in SECTION_ORDER:
            assert f"{folder}/" in index, f"no entry linking {folder}/ in index"
            heading = f"## {folder.capitalize()}"
            assert heading in index, f"missing section heading {heading!r}"
            positions.append(index.index(heading))
        assert positions == sorted(positions), "sections not in §5 fixed order"

    def test_entry_line_format(self, tmp_path):
        wiki = make_wiki(tmp_path)
        assert run_generate(wiki).returncode == 0
        index = read_index(wiki)
        expected = (
            "- [mikel](profile/mikel.md) "
            "— Junior DAM developer with a retail-management past turned coder "
            "(updated 2026-08-20, confidence=high)"
        )
        assert expected in index

    def test_header_has_auto_generated_notice(self, tmp_path):
        wiki = make_wiki(tmp_path)
        assert run_generate(wiki).returncode == 0
        index = read_index(wiki)
        assert "AUTO-GENERATED" in index
        assert "do not edit" in index.lower()


class TestSorting:
    def test_sorted_by_updated_desc(self, tmp_path):
        wiki = make_wiki(tmp_path)
        add_page(
            wiki,
            "projects",
            "aaa-old",
            updated="2026-01-15",
            title="aaa-old",
            summary="older project page",
        )
        add_page(
            wiki,
            "projects",
            "zzz-new",
            updated="2026-12-01",
            title="zzz-new",
            summary="newer project page",
        )
        assert run_generate(wiki).returncode == 0
        index = read_index(wiki)
        pos_new = index.index("projects/zzz-new.md")
        pos_base = index.index("projects/interview-tts.md")  # updated 2026-08-20
        pos_old = index.index("projects/aaa-old.md")
        assert pos_new < pos_base < pos_old, "entries not sorted updated-desc"

    def test_tie_break_filename_ascending(self, tmp_path):
        wiki = make_wiki(tmp_path)
        add_page(wiki, "skills", "beta-same-day", updated="2026-08-20")
        add_page(wiki, "skills", "alpha-same-day", updated="2026-08-20")
        assert run_generate(wiki).returncode == 0
        index = read_index(wiki)
        assert index.index("skills/alpha-same-day.md") < index.index(
            "skills/beta-same-day.md"
        )


class TestDeterminismAndIsolation:
    def test_two_runs_byte_identical(self, tmp_path):
        wiki = make_wiki(tmp_path)
        assert run_generate(wiki).returncode == 0
        first = read_index(wiki)
        assert run_generate(wiki).returncode == 0
        assert read_index(wiki) == first, "regeneration is not byte-stable"

    def test_regenerates_unconditionally_overwriting_stale_content(self, tmp_path):
        wiki = make_wiki(tmp_path)
        (wiki / "index.md").write_text("STALE HAND-EDITED CONTENT", encoding="utf-8")
        assert run_generate(wiki).returncode == 0
        index = read_index(wiki)
        assert "STALE HAND-EDITED CONTENT" not in index
        assert "AUTO-GENERATED" in index

    def test_only_index_md_is_modified(self, tmp_path):
        wiki = make_wiki(tmp_path)
        (wiki / "index.md").write_text("pre-existing index", encoding="utf-8")
        before = snapshot_mtimes(wiki)
        assert run_generate(wiki).returncode == 0
        after = snapshot_mtimes(wiki)
        assert set(before) == set(after)
        changed = {name for name, ts in after.items() if ts != before[name]}
        assert changed == {"index.md"}, f"unexpected modified files: {changed}"


class TestExitCodes:
    def test_success_exit_zero(self, tmp_path):
        wiki = make_wiki(tmp_path)
        assert run_generate(wiki).returncode == 0

    def test_missing_wiki_dir_scan_error_exit_one(self, tmp_path):
        result = run_generate(tmp_path / "does-not-exist")
        assert result.returncode == 1
