"""Tests for candidate profile loader."""

import json
from pathlib import Path

import pytest

from backend.services.candidate import CandidateProfile
from tests.real_wiki import WIKI_ROOT, load_documents


@pytest.fixture
def sample_candidate_dir(tmp_path):
    """Create a temporary candidate directory with sample files."""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()

    # Create profile.json
    profile = {
        "name": "Test Candidate",
        "title": "Developer",
        "summary": "A test candidate profile",
        "skills": ["Python", "FastAPI"],
        "experience": [
            {
                "company": "Test Corp",
                "role": "Developer",
                "period": "2024",
                "highlights": ["Built stuff"]
            }
        ],
        "projects": [
            {
                "name": "TestProject",
                "description": "A test project",
                "technologies": ["Python"],
                "highlights": ["Did things"]
            }
        ],
        "stories": [
            {
                "situation": "A problem existed",
                "task": "Fix it",
                "action": "I coded",
                "result": "It works"
            }
        ],
    }
    (candidate_dir / "profile.json").write_text(json.dumps(profile), encoding="utf-8")

    # Create docs directory with markdown files
    docs_dir = candidate_dir / "docs"
    docs_dir.mkdir()

    (docs_dir / "cv.md").write_text("# CV\n\n## Experience\n\nWorked at Test Corp.", encoding="utf-8")
    (docs_dir / "projects.md").write_text("# Projects\n\n## TestProject\n\nBuilt with Python.", encoding="utf-8")
    (docs_dir / "skills.md").write_text("# Skills\n\nPython, FastAPI", encoding="utf-8")

    return candidate_dir


class TestCandidateProfile:
    """Tests for CandidateProfile loader."""

    def test_load_profile(self, sample_candidate_dir):
        """Profile loads from directory successfully."""
        profile = CandidateProfile(sample_candidate_dir)
        profile.load()

        assert profile.profile_data is not None
        assert profile.profile_data["name"] == "Test Candidate"
        assert len(profile.documents) == 3

    def test_load_missing_directory(self, tmp_path):
        """Profile handles missing directory gracefully."""
        profile = CandidateProfile(tmp_path / "nonexistent")
        profile.load()

        assert profile.profile_data is None
        assert len(profile.documents) == 0

    def test_load_empty_docs(self, tmp_path):
        """Profile handles empty docs directory."""
        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        docs_dir = candidate_dir / "docs"
        docs_dir.mkdir()

        profile = CandidateProfile(candidate_dir)
        profile.load()

        assert len(profile.documents) == 0

    def test_get_context_string(self, sample_candidate_dir):
        """Context string includes profile and document data."""
        profile = CandidateProfile(sample_candidate_dir)
        profile.load()
        context = profile.get_context_string()

        assert "Test Candidate" in context
        assert "Developer" in context
        assert "Python" in context
        assert "TestProject" in context
        assert "--- cv.md ---" in context

    def test_get_context_empty(self, tmp_path):
        """Context string is empty when no data loaded."""
        profile = CandidateProfile(tmp_path / "empty")
        profile.load()
        context = profile.get_context_string()
        assert context == ""

    def test_load_corrupt_json(self, tmp_path):
        """Profile handles corrupt JSON gracefully."""
        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        (candidate_dir / "profile.json").write_text("not valid json {{{", encoding="utf-8")

        profile = CandidateProfile(candidate_dir)
        profile.load()

        assert profile.profile_data is None


class TestGeneratedIndexIsNotACandidateDocument:
    """``index.md`` is a build artifact, not answer content.

    ``scripts/wiki/generate_index.py`` rewrites it on demand and its own header
    says "AUTO-GENERATED ... do not edit". It is a table of links whose text is
    every other document's ``summary_1line``, so it reads as keyword-dense
    across every topic in the corpus while containing no answer at all. Loaded
    as a candidate document it contributed 10 chunks to the default retrieval
    pool on the real wiki.

    CORPUS: the two end-to-end tests below run against the real ``wiki/``,
    which is 46 tracked files and therefore present on a clean clone and in CI.
    They used to run against an invented stand-in on the stated ground that
    the real wiki was private and untracked, which was false.

    The page count they assert is DERIVED from the checkout rather than pinned:
    four FAQ pages are on disk but untracked, so a clean clone serves 33 and
    this checkout serves 37. The loader behaviour being pinned — index, README
    and CONVENCIONES skipped, templates skipped, everything else loaded — is
    the part that is not a property of one machine.
    """

    def _wiki_with_index(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "faq").mkdir(parents=True)
        (wiki / "faq" / "nivel-ingles.md").write_text(
            "---\ntype: faq\nsummary_1line: Ingles B2\n---\n\n"
            "# Como es tu nivel de ingles\n\nMe desenvuelvo bien.\n",
            encoding="utf-8",
        )
        (wiki / "index.md").write_text(
            "<!-- AUTO-GENERATED by scripts/wiki/generate_index.py "
            "- do not edit. -->\n\n# Wiki index\n\n"
            "## faq\n- [nivel-ingles](faq/nivel-ingles.md) - Ingles B2\n",
            encoding="utf-8",
        )
        return wiki

    def test_index_md_is_not_loaded(self, tmp_path):
        """The defect: index.md sits in the unfiltered candidate pool."""
        profile = CandidateProfile(tmp_path / "candidate", wiki_dir=self._wiki_with_index(tmp_path))
        profile.load()
        assert "index.md" not in profile.documents, (
            f"index.md was loaded as candidate content: {list(profile.documents)}"
        )

    def test_the_real_pages_are_still_loaded(self, tmp_path):
        """Dropping the index must not drop the content it points at."""
        profile = CandidateProfile(tmp_path / "candidate", wiki_dir=self._wiki_with_index(tmp_path))
        profile.load()
        # Keys are str(Path.relative_to(...)), so the separator is the host's.
        loaded = {k.replace("\\", "/") for k in profile.documents}
        assert "faq/nivel-ingles.md" in loaded

    def test_the_generated_index_is_generated_not_authored(self):
        """Prove the skipped artifact is a build artifact, from the repo itself.

        CORPUS: the real ``wiki/``. ``wiki/index.md`` is produced on demand by
        ``scripts/wiki/generate_index.py``; it is not tracked, which is why a
        clean clone does not have it, and the loader must skip it whether or
        not it is present.
        """
        index = WIKI_ROOT / "index.md"
        if not index.exists():
            pytest.skip(
                "wiki/index.md has not been generated in this checkout; it is a "
                "build artifact, not tracked, and the loader's skip of it is "
                "pinned by test_index_md_is_not_loaded above."
            )
        head = index.read_text(encoding="utf-8")[:400]
        assert "AUTO-GENERATED" in head and "do not edit" in head.lower(), (
            "wiki/index.md no longer declares itself generated — "
            "re-check whether it is still safe to skip"
        )

    def test_the_real_index_contributes_no_documents(self, wiki_targets):
        """End-to-end through the real loader, on the real corpus."""
        documents = load_documents()

        assert "index.md" not in documents
        assert "CONVENCIONES.md" not in documents, (
            "the conventions sheet is documentation for the author, not an "
            "answer; loading it makes the model recite house style"
        )
        assert not [k for k in documents if "README" in k], (
            "folder READMEs explain the folder; they are not interview answers"
        )
        assert not [k for k in documents if k.replace("\\", "/").startswith("templates/")], (
            "a template page is a blank form, not an answer: "
            f"{sorted(k for k in documents if 'templates' in k)}"
        )
        # DERIVED, not pinned: a hard-coded page count would be a property of
        # one machine's checkout, which is the mistake this test class used to
        # make. Four FAQ pages here are on disk but untracked, so a clean clone
        # loads 33 and this checkout loads 37. What must hold on every machine
        # is that the loader serves exactly the Markdown that is neither a
        # README/CONVENCIONES/index nor a template form.
        on_disk = {
            p.relative_to(WIKI_ROOT).as_posix()
            for p in WIKI_ROOT.rglob("*.md")
            if p.name not in {"README.md", "CONVENCIONES.md", "index.md"}
            and "templates" not in p.parts
        }
        assert {k.replace("\\", "/") for k in documents} == on_disk, (
            f"the loader serves {len(documents)} pages but {len(on_disk)} are "
            f"eligible on disk. Missing: "
            f"{sorted(on_disk - {k.replace(chr(92), '/') for k in documents})}. "
            f"Extra: "
            f"{sorted({k.replace(chr(92), '/') for k in documents} - on_disk)}."
        )
