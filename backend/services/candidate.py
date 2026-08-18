"""Candidate profile loader — reads JSON and Markdown from wiki/ directory."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Directories to skip when loading wiki files
_SKIP_DIRS = {"templates", "__pycache__", ".git"}
_SKIP_FILES = {"README.md", "CONVENCIONES.md"}


class CandidateProfile:
    """Loads and provides access to candidate profile data."""

    def __init__(self, candidate_dir: str | Path, wiki_dir: str | Path | None = None):
        self.candidate_dir = Path(candidate_dir)
        self.wiki_dir = Path(wiki_dir) if wiki_dir else None
        self.profile_data: Optional[Dict] = None
        self.documents: Dict[str, str] = {}  # filename -> content

    def load(self) -> None:
        """Load profile.json and Markdown documents from wiki/ (preferred) or candidate/."""
        self._load_profile_json()
        if self.wiki_dir and self.wiki_dir.exists():
            self._load_wiki_docs()
        else:
            self._load_markdown_docs()

    def _load_profile_json(self) -> None:
        """Load the main profile.json file."""
        profile_path = self.candidate_dir / "profile.json"
        if not profile_path.exists():
            logger.warning("profile.json not found in %s", self.candidate_dir)
            return

        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                self.profile_data = json.load(f)
            logger.info("Loaded profile for: %s", self.profile_data.get("name", "Unknown"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load profile.json: %s", e)

    def _load_wiki_docs(self) -> None:
        """Load all Markdown files from wiki/ directory (excluding templates and READMEs)."""
        if not self.wiki_dir:
            return

        loaded = 0
        skipped = 0

        for md_file in sorted(self.wiki_dir.rglob("*.md")):
            # Skip template and README files
            if md_file.name in _SKIP_FILES:
                skipped += 1
                continue
            if any(skip_dir in md_file.parts for skip_dir in _SKIP_DIRS):
                skipped += 1
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
                # Use relative path as key for better traceability
                rel_path = md_file.relative_to(self.wiki_dir)
                self.documents[str(rel_path)] = content
                loaded += 1
                logger.info("Loaded wiki document: %s (%d chars)", rel_path, len(content))
            except OSError as e:
                logger.error("Failed to load %s: %s", md_file, e)

        logger.info("Loaded %d wiki documents, skipped %d", loaded, skipped)

    def _load_markdown_docs(self) -> None:
        """Load all Markdown files from candidate/docs/ directory (fallback)."""
        docs_dir = self.candidate_dir / "docs"
        if not docs_dir.exists():
            logger.warning("candidate/docs/ directory not found at %s", docs_dir)
            return

        found_sections = []
        missing_sections = ["cv.md", "projects.md", "skills.md", "stories.md"]

        for md_file in sorted(docs_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                self.documents[md_file.name] = content
                found_sections.append(md_file.name)
                if md_file.name in missing_sections:
                    missing_sections.remove(md_file.name)
                logger.info("Loaded document: %s (%d chars)", md_file.name, len(content))
            except OSError as e:
                logger.error("Failed to load %s: %s", md_file, e)

        logger.info("Loaded %d documents, missing: %s", len(found_sections), missing_sections or "none")

    def get_context_string(self) -> str:
        """Get all candidate data as a single context string for the system prompt.

        Returns:
            Combined context from profile.json sections and Markdown documents.
        """
        parts = []

        if self.profile_data:
            # Add structured profile data
            parts.append(f"Name: {self.profile_data.get('name', 'Unknown')}")
            parts.append(f"Title: {self.profile_data.get('title', 'Unknown')}")
            parts.append(f"Summary: {self.profile_data.get('summary', '')}")

            if self.profile_data.get("skills"):
                parts.append(f"Skills: {', '.join(self.profile_data['skills'])}")

            for exp in self.profile_data.get("experience", []):
                parts.append(f"Experience at {exp.get('company', '?')} as {exp.get('role', '?')} ({exp.get('period', '?')}):")
                for highlight in exp.get("highlights", []):
                    parts.append(f"  - {highlight}")

            for proj in self.profile_data.get("projects", []):
                parts.append(f"Project: {proj.get('name', '?')}")
                parts.append(f"  Description: {proj.get('description', '')}")
                parts.append(f"  Technologies: {', '.join(proj.get('technologies', []))}")
                for highlight in proj.get("highlights", []):
                    parts.append(f"  - {highlight}")

            for story in self.profile_data.get("stories", []):
                parts.append(f"Story:")
                parts.append(f"  Situation: {story.get('situation', '')}")
                parts.append(f"  Task: {story.get('task', '')}")
                parts.append(f"  Action: {story.get('action', '')}")
                parts.append(f"  Result: {story.get('result', '')}")

        # Add raw Markdown documents
        for filename, content in self.documents.items():
            parts.append(f"\n--- {filename} ---\n{content}")

        return "\n".join(parts)
