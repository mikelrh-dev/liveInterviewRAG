"""Shared helpers for the wiki scripts (validate / compile / generate_index).

Conventions source of truth: wiki/CONVENCIONES.md
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import yaml

# Singular `type:` value -> plural parent folder (design §5).
TYPE_TO_FOLDER = {
    "profile": "profile",
    "project": "projects",
    "experience": "experience",
    "skills": "skills",
    "story": "stories",
    "opinion": "opinions",
    "decision": "decisions",
    "faq": "faq",
}
FOLDER_TO_TYPE = {folder: t for t, folder in TYPE_TO_FOLDER.items()}

REQUIRED_FIELDS = (
    "type",
    "title",
    "created",
    "updated",
    "confidence",
    "tags",
    "related",
    "summary_1line",
)
CONFIDENCE_ENUM = {"high", "medium", "low"}
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Never scanned as content pages (mirrors CONVENCIONES layout).
SKIP_FILES = {"CONVENCIONES.md", "index.md", "README.md"}
SKIP_DIRS = {"templates"}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str):
    """Return (meta_dict|None, body_str). meta is None when absent/invalid."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    try:
        meta = yaml.safe_load(match.group(1))
    except (yaml.YAMLError, ValueError):
        # ValueError: PyYAML raises it for impossible dates (e.g. 2024-13-45)
        return None, text
    if not isinstance(meta, dict):
        return None, text
    return meta, text[match.end() :]


def normalize_related(entry: str) -> str:
    """Tolerate [[...]]-style links and extension-less entries."""
    cleaned = entry.strip().strip("[]")
    if not cleaned.endswith(".md"):
        cleaned += ".md"
    return cleaned.replace("\\", "/")


def valid_date(value) -> bool:
    value = coerce_date(value)
    if value is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def coerce_date(value):
    """Return the canonical YYYY-MM-DD string for str/date inputs, else None."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and DATE_RE.match(value):
        return value
    return None


def iter_wiki_files(wiki_root: Path):
    """Yield (relpath_posix, folder_name, meta, body) sorted by relpath,
    skipping SKIP_FILES/SKIP_DIRS everywhere."""
    for path in sorted(wiki_root.rglob("*.md")):
        rel = path.relative_to(wiki_root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        yield rel.as_posix(), rel.parts[0] if len(rel.parts) > 1 else "", meta, body


def days_since(date_str: str, today: date | None = None) -> int:
    ref = today or date.today()
    parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (ref - parsed).days
