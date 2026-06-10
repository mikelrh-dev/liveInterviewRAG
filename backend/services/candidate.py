"""Candidate profile loader — reads JSON and Markdown from candidate/ directory."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CandidateProfile:
    """Loads and provides access to candidate profile data."""

    def __init__(self, candidate_dir: str | Path):
        self.candidate_dir = Path(candidate_dir)
        self.profile_data: Optional[Dict] = None
        self.documents: Dict[str, str] = {}  # filename -> content

    def load(self) -> None:
        """Load profile.json and all Markdown documents from candidate/ directory."""
        self._load_profile_json()
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

    def _load_markdown_docs(self) -> None:
        """Load all Markdown files from candidate/docs/ directory."""
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
                logger.error("Failed to load %s: %s", md_file.name, e)

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
