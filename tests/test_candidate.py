"""Tests for candidate profile loader."""

import json
from pathlib import Path

import pytest

from backend.services.candidate import CandidateProfile


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
