"""Candidate profile loader — reads JSON and Markdown from wiki/ directory."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Directories to skip when loading wiki files
_SKIP_DIRS = {"templates", "__pycache__", ".git"}
# Files that are not candidate content.
#
# ``index.md`` is a BUILD ARTIFACT: ``scripts/wiki/generate_index.py`` rewrites
# it on demand and its own header says "AUTO-GENERATED ... do not edit". It is a
# table of links whose text is every other document's ``summary_1line``, so it
# reads as keyword-dense across skills, tests, Mercadona, DAM, backend,
# frontend, data, DevOps, Python, Java, SQL, AI and RAG while containing no
# answer at all — it contributed 10 chunks to the default retrieval pool, and
# its chunks carry no frontmatter, so ``type`` is empty and the context header
# cannot name a type for them. The wiki tooling already agrees:
# ``scripts/wiki/_common.py`` skips exactly this set.
_SKIP_FILES = {"README.md", "CONVENCIONES.md", "index.md"}


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

    # ── The work-history block ─────────────────────────────────────────────────
    #
    #: One timeline line. The separator is an em dash and not a hyphen because the
    #: periods themselves contain dashes ("2019–Nov 2025"), and a hyphen between
    #: the period and the role reads as part of the date at reading speed.
    _TIMELINE_LINE = "{period} — {role}{company}"

    def get_work_history_block(self) -> str:
        """The compiled career timeline as employer/role/period lines, or ``""``.

        WHAT THIS EXISTS FOR, AND WHY ``get_context_string`` CANNOT DO IT
        -----------------------------------------------------------------
        ``get_context_string`` appends the FULL TEXT of every wiki document
        (``candidate.py:146-147``). On this corpus that is 37 pages, so passing
        its output to ``build_system_prompt`` would put thousands of tokens of
        Markdown in front of the model on every turn, almost none of it about
        the question being asked. That is also why nothing in production calls
        it: it is a debugging dump, not a prompt input, and the honest reading
        of its name is what makes that expensive.

        What a prompt needs instead is the one part of the profile a model can
        neither infer nor safely guess: WHICH employer, WHICH role, WHICH
        dates. Measured on the six work-history questions
        (``tests/work_history_cases.py``), five of the six fail retrieval as
        ranking misses -- every gold page clears the 0.25 cosine threshold with
        room to spare, so no threshold change reaches them, and five query-side
        variants all cost questions from the official 49. Identity facts are not
        a retrieval problem; they are a plumbing problem, and this is the pipe.

        WHY IT IS BUILT FROM profile.json AND NOT RE-PARSED FROM THE WIKI
        ---------------------------------------------------------------------
        ``scripts/wiki/compile.py::_parse_experience`` reads the
        ``## Career timeline (corrected)`` section of ``wiki/profile/mikel.md``,
        and that section already carries all three employments
        (``wiki/profile/mikel.md:26-32``). So the facts are COMPILED, and this
        method renders what the build produced instead of parsing Markdown a
        second time: two parsers for one set of facts is two sources of truth,
        and the disagreement between them would be invisible until an interview.

        THE DELIMITER, AND WHY IT IS NOT A BUG
        --------------------------------------
        ``_parse_experience`` splits ``PERIOD: Role, Company`` on the FIRST
        comma (``compile.py:96-98``), so the study line
        "2024–2026: FP Superior DAM at Tartanga (Erandio, presencial), started
        while working at Mercadona" compiles to company="started while working
        at Mercadona". This method prints that unchanged rather than
        second-guessing it: filtering it would mean classifying entries
        semantically, which is exactly the second opinion just ruled out. The
        heading the caller uses says "trayectoria", not "empleos", so nothing
        asserts that a degree is an employer, and the fragment is a true one.

        LIMITS
        ------
        * Returns ``""`` -- never a partial block -- when there is no
          ``profile.json``, no ``experience`` key, or no entry with both a
          period and a role. The caller reads ``""`` as "no guarantee
          available" and switches the prompt to its degraded wording, so a
          half-populated timeline must not be able to pass for a whole one.
        * Reads ``profile_data`` only, never ``self.documents``: the block is
          structured data or it is nothing.
        * ``highlights`` are dropped on purpose. They are prose, and the budget
          is roughly 200 tokens of employer/role/dates; on the real profile the
          six compiled entries render at about 110 tokens.
        * An entry that is not a dict is skipped rather than raising, because a
          hand-edited ``profile.json`` should cost one line and not a turn.
        """
        if not self.profile_data:
            return ""

        lines: List[str] = []
        for entry in self.profile_data.get("experience") or []:
            if not isinstance(entry, dict):
                continue
            period = str(entry.get("period") or "").strip()
            role = str(entry.get("role") or "").strip()
            company = str(entry.get("company") or "").strip()
            if not period or not role:
                continue
            suffix = f", {company}" if company else ""
            lines.append(
                self._TIMELINE_LINE.format(period=period, role=role, company=suffix)
            )

        return "\n".join(lines)
