#!/usr/bin/env python3
"""validate.py — read-only CONVENCIONES quality gate for the person wiki.

Usage: python scripts/wiki/validate.py [--wiki PATH]
Exit codes: 0 clean-or-warnings-only, 1 any error, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    CONFIDENCE_ENUM,
    KEBAB_RE,
    REQUIRED_FIELDS,
    TYPE_TO_FOLDER,
    coerce_date,
    days_since,
    iter_wiki_files,
    normalize_related,
    valid_date,
)

STALE_DAYS = 7
MAX_SUMMARY = 80


def collect_findings(wiki_root: Path):
    """Pure check used by validate.py AND imported by compile.py (design D4).

    Returns (errors, warnings, n_files): each finding is (relpath, message).
    """
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    pages: dict[str, dict] = {}  # relpath -> meta, for cross-file warnings

    for relpath, folder, meta, _body in iter_wiki_files(wiki_root):
        if meta is None:
            errors.append((relpath, "missing or invalid YAML frontmatter"))
            continue
        n_before = len(errors)
        _check_fields(relpath, folder, meta, errors)
        if len(errors) == n_before:
            pages[relpath] = meta

    for relpath, meta in sorted(pages.items()):
        for raw in meta.get("related") or []:
            target = normalize_related(str(raw))
            if not (wiki_root / target).is_file():
                errors.append((relpath, f"related link does not resolve: {raw}"))

    _check_link_symmetry(pages, warnings)
    _check_stale_low_confidence(pages, warnings)
    return errors, warnings, len(list(iter_files(wiki_root)))


def iter_files(wiki_root: Path):

    for p in sorted(wiki_root.rglob("*.md")):
        if any(part in {"templates"} for part in p.relative_to(wiki_root).parts):
            continue
        if p.name in {"CONVENCIONES.md", "index.md", "README.md"}:
            continue
        yield p


def _check_fields(relpath, folder, meta, errors):
    def err(msg):
        errors.append((relpath, msg))

    missing = [f for f in REQUIRED_FIELDS if f not in meta]
    if missing:
        err(f"missing required field(s): {', '.join(missing)}")

    ftype = meta.get("type")
    if ftype not in TYPE_TO_FOLDER:
        err(f"type '{ftype}' is not one of {sorted(TYPE_TO_FOLDER)}")
    elif not folder or TYPE_TO_FOLDER[ftype] != folder:
        err(f"type '{ftype}' does not match parent folder '{folder}/' "
            f"(expected '{TYPE_TO_FOLDER[ftype]}/')")

    title = meta.get("title")
    stem = Path(relpath).stem
    if title is not None and (not isinstance(title, str) or not KEBAB_RE.match(title)):
        err(f"title '{title}' must be kebab-case")
    elif title != stem:
        err(f"title '{title}' must equal filename stem '{stem}'")

    for field in ("created", "updated"):
        value = meta.get(field)
        if value is not None and not valid_date(value):
            err(f"{field} '{value}' must be a valid YYYY-MM-DD date")
    confidence = meta.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_ENUM:
        err(f"confidence '{confidence}' must be high|medium|low")

    tags = meta.get("tags")
    if tags is not None and (not isinstance(tags, list) or len(tags) == 0):
        err("tags must be a non-empty list")

    summary = meta.get("summary_1line")
    if summary is not None and (
        not isinstance(summary, str) or len(summary) > MAX_SUMMARY
    ):
        err(f"summary_1line exceeds {MAX_SUMMARY} chars")


def _check_link_symmetry(pages, warnings):
    for relpath, meta in sorted(pages.items()):
        for raw in meta.get("related") or []:
            target = normalize_related(str(raw))
            if target in pages:
                back = [normalize_related(str(r)) for r in pages[target].get("related") or []]
                if relpath not in back:
                    warnings.append(
                        (target, f"link asymmetry: '{relpath}' relates here but no reciprocal entry")
                    )


def _check_stale_low_confidence(pages, warnings):
    for relpath, meta in sorted(pages.items()):
        if meta.get("confidence") == "low":
            iso = coerce_date(meta.get("updated"))
            if iso is None:
                continue
            try:
                age = days_since(iso)
            except ValueError:
                continue
            if age > STALE_DAYS:
                warnings.append((
                    relpath,
                    f"stale content: confidence=low and updated {age} days ago (> {STALE_DAYS})",
                ))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate wiki/ against CONVENCIONES.")
    parser.add_argument("--wiki", default="wiki", help="Path to the wiki root (default: wiki)")
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not wiki_root.is_dir():
        print(f"[ERROR] wiki path is not a directory: {wiki_root}", file=sys.stderr)
        return 2

    try:
        errors, warnings, n_files = collect_findings(wiki_root)
    except OSError as exc:
        print(f"[ERROR] cannot read wiki: {exc}", file=sys.stderr)
        return 2

    for relpath, msg in errors:
        print(f"[ERROR] {relpath}: {msg}")
    for relpath, msg in warnings:
        print(f"[WARN] {relpath}: {msg}")

    if errors:
        print(f"Validation failed: {len(errors)} errors, {len(warnings)} warnings")
        return 1
    print(f"OK: {n_files} files valid, {len(warnings)} warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
