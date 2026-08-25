"""Strict-TDD tests for scripts/wiki/compile.py (RED first).

All tests run against copies of tests/fixtures/wiki/* placed in pytest's
tmp_path — never against the real wiki/ or candidate/.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "wiki"
COMPILE = REPO_ROOT / "scripts" / "wiki" / "compile.py"


def run_compile(wiki: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COMPILE), "--wiki", str(wiki), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def copy_fixture(name: str, tmp_path: Path) -> Path:
    src = FIXTURES / "good" if name == "good" else FIXTURES / "bad" / name
    dest = tmp_path / name
    shutil.copytree(src, dest)
    return dest


def read_profile(out: Path) -> dict:
    return json.loads((out / "profile.json").read_text(encoding="utf-8"))


EXPECTED_DOCS = {
    "project-interview-tts.md",
    "experience-gerente-mercadona-2019-2025.md",
    "skills-backend.md",
    "story-gestion-tiempo-mercadona.md",
    "opinion-remoto-presencial-frameworks.md",
    "decision-por-que-interviewtts.md",
    "faq-presentacion-30-segundos.md",
}


class TestProfileMapping:
    def test_identity_fields(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        result = run_compile(wiki, tmp_path / "candidate")
        assert result.returncode == 0, result.stdout + result.stderr
        profile = read_profile(tmp_path / "candidate")
        assert profile["name"] == "Mikel Romero Homobono"
        assert profile["title"] == "Software Developer (Junior)"

    def test_summary_verbatim_from_frontmatter(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        profile = read_profile(tmp_path / "candidate")
        assert (
            profile["summary"]
            == "Junior DAM developer with a retail-management past turned coder"
        )

    def test_skills_flattened_in_order(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        profile = read_profile(tmp_path / "candidate")
        assert profile["skills"] == [
            "Python (FastAPI)", "Java", "SQL", "JavaScript", "HTML", "CSS",
        ]

    def test_experience_parsed_from_timeline(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        profile = read_profile(tmp_path / "candidate")
        assert profile["experience"] == [
            {"role": "Frutero", "company": "BM Supermercados",
             "period": "2015\u20132016", "highlights": []},
            {"role": "Encargado", "company": "BM Supermercados",
             "period": "2016\u20132019", "highlights": []},
            {"role": "Gerente B", "company": "Mercadona",
             "period": "2019\u2013Nov 2025", "highlights": []},
        ]

    def test_projects_from_projects_folder(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        profile = read_profile(tmp_path / "candidate")
        assert profile["projects"] == [{
            "name": "interview-tts",
            "description":
                "Voice-based AI interview digital twin for recruiter conversations",
            "technologies": ["project", "voice", "fullstack"],
            "highlights": [
                "Full-stack solo build: FastAPI backend plus vanilla JS frontend",
                "Faster Whisper STT wired to an LLM and Edge TTS output",
                "RAG retrieval over candidate documents for grounded answers",
            ],
        }]

    def test_stories_star_from_h2_sections(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        profile = read_profile(tmp_path / "candidate")
        assert profile["stories"] == [{
            "situation":
                "En el d\u00eda a d\u00eda de Mercadona cada d\u00eda era "
                "diferente y surg\u00edan imprevistos.",
            "task": "Garantizar que todo estuviera planificado sin perder flexibilidad.",
            "action": (
                "- Planificaci\u00f3n del d\u00eda previo por escrito\n"
                "- Priorizaci\u00f3n bajo presi\u00f3n reasignando tareas"
            ),
            "result":
                "El equipo arrancaba con claridad y los imprevistos no frenaban "
                "la operaci\u00f3n.",
        }]

    def test_key_order_and_no_documents_key(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        raw = (tmp_path / "candidate" / "profile.json").read_text(encoding="utf-8")
        profile = json.loads(raw)
        assert list(profile.keys()) == [
            "name", "title", "summary", "skills",
            "experience", "projects", "stories",
        ]
        assert "documents" not in profile

    def test_json_serialization_exact(self, tmp_path):
        """indent=2, ensure_ascii=False, trailing newline."""
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        raw = (tmp_path / "candidate" / "profile.json").read_text(encoding="utf-8")
        profile = json.loads(raw)
        assert raw == json.dumps(profile, indent=2, ensure_ascii=False) + "\n"

    def test_missing_identity_field_is_actionable_failure(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        mikel = wiki / "profile" / "mikel.md"
        text = mikel.read_text(encoding="utf-8")
        mikel.write_text(text.replace("- **Role:** Software Developer (Junior)\n", ""),
                         encoding="utf-8")
        result = run_compile(wiki, tmp_path / "candidate")
        assert result.returncode != 0
        assert "Role" in (result.stdout + result.stderr)


class TestDocsOutput:
    def test_one_doc_per_file_with_type_prefix(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        result = run_compile(wiki, tmp_path / "candidate")
        assert result.returncode == 0, result.stdout + result.stderr
        docs = {p.name for p in (tmp_path / "candidate" / "docs").glob("*.md")}
        assert docs == EXPECTED_DOCS

    def test_frontmatter_stripped_body_verbatim(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "candidate").returncode == 0
        body = (tmp_path / "candidate" / "docs" / "skills-backend.md").read_text(
            encoding="utf-8")
        assert not body.startswith("---")
        assert "# Backend" in body
        assert "confidence" not in body.split("# Backend")[0]


class TestBlockingGate:
    def test_invalid_wiki_aborts_with_zero_writes(self, tmp_path):
        wiki = copy_fixture("missing-field", tmp_path)
        out = tmp_path / "candidate"
        out.mkdir()
        (out / "profile.json").write_text('{"sentinel": true}', encoding="utf-8")
        before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        result = run_compile(wiki, out)
        assert result.returncode == 1
        assert "[ERROR]" in result.stdout
        after = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        assert before == after, "gate failure must leave candidate untouched"
        assert not list(tmp_path.glob("candidate.tmp-*")), \
            "temp dir must never be created on gate failure"

    def test_type_folder_mismatch_rejected_by_cli_gate(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        (wiki / "projects" / "extra-story.md").write_text(
            "---\ntype: story\ntitle: extra-story\ncreated: 2026-01-01\n"
            "updated: 2026-01-01\nconfidence: high\ntags: [x]\nrelated: []\n"
            "summary_1line: mismatched\n---\n\nbody\n",
            encoding="utf-8",
        )
        result = run_compile(wiki, tmp_path / "candidate")
        assert result.returncode == 1


class TestAtomicSwapAndIdempotency:
    def test_two_runs_are_byte_identical(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        assert run_compile(wiki, tmp_path / "c1").returncode == 0
        assert run_compile(wiki, tmp_path / "c2").returncode == 0
        files1 = {p.relative_to(tmp_path / "c1"): p.read_bytes()
                  for p in sorted((tmp_path / "c1").rglob("*")) if p.is_file()}
        files2 = {p.relative_to(tmp_path / "c2"): p.read_bytes()
                  for p in sorted((tmp_path / "c2").rglob("*")) if p.is_file()}
        assert files1 == files2

    def test_rerun_into_existing_dir_swaps_atomically(self, tmp_path):
        wiki = copy_fixture("good", tmp_path)
        out = tmp_path / "candidate"
        assert run_compile(wiki, out).returncode == 0
        first = {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        # mutate the deployed tree, then recompile over it
        (out / "profile.json").write_text("corrupted", encoding="utf-8")
        result = run_compile(wiki, out)
        assert result.returncode == 0, result.stdout + result.stderr
        second = {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        assert second == first, "rerun must restore identical bytes"
        assert not list(tmp_path.glob("candidate.tmp-*")), "no tmp residue"
        assert not (tmp_path / "candidate.prev").exists(), "prev cleaned up"
        assert "Replaced" in result.stdout

    def test_mismatch_skipped_when_build_called_directly(self, tmp_path):
        """Defense in depth: build() itself skips mismatches with a log entry
        even though the CLI gate rejects them earlier (design §5)."""
        wiki = copy_fixture("good", tmp_path)
        (wiki / "projects" / "extra-story.md").write_text(
            "---\ntype: story\ntitle: extra-story\ncreated: 2026-01-01\n"
            "updated: 2026-01-01\nconfidence: high\ntags: [x]\nrelated: []\n"
            "summary_1line: mismatched\n---\n\nbody\n",
            encoding="utf-8",
        )
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wiki_compile", REPO_ROOT / "scripts" / "wiki" / "compile.py")
        assert spec is not None and spec.loader is not None
        compile_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compile_mod)
        dest = tmp_path / "built"
        dest.mkdir()
        n_files, skipped = compile_mod.build(wiki, dest)
        assert "projects/extra-story.md" in skipped
        assert not (dest / "docs" / "story-extra-story.md").exists()
