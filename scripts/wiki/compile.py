#!/usr/bin/env python3
"""compile.py — turn the validated wiki into the deployed candidate/ tree.

Usage: python scripts/wiki/compile.py [--wiki PATH] [--out PATH]
Exit codes: 0 success, 1 validation error, 2 IO/usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    TYPE_TO_FOLDER,
    iter_wiki_files,
)
from validate import collect_findings  # noqa: E402

PROFILE_REL = "profile/mikel.md"
BOLD_LABEL_RE = re.compile(r"^-\s*\*\*(.+?):\*\*\s*(.*)$")
TIMELINE_RE = re.compile(r"^-\s*\*\*(.+?):\*\*\s*(.*)$")


class CompileError(Exception):
    """Actionable body-shape failure inside profile/mikel.md."""


def _section_lines(body: str, heading: str) -> list[str]:
    """Stripped lines under a `## <heading>` H2 (case-insensitive), until the
    next heading of any level. Empty when the section is absent."""
    wanted = f"## {heading}".lower()
    lines: list[str] = []
    inside = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            if stripped.lower() == wanted:
                inside = True
                continue
            if inside:
                break
            continue
        if inside and stripped:
            lines.append(stripped)
    return lines


def _label_value(lines: list[str], label: str) -> str | None:
    for line in lines:
        match = BOLD_LABEL_RE.match(line)
        if match and match.group(1).strip().lower() == label.lower():
            return match.group(2).strip()
    return None


def _parse_identity(body: str, relpath: str) -> tuple[str, str]:
    identity = _section_lines(body, "Identity")
    name = _label_value(identity, "Name")
    role = _label_value(identity, "Role")
    if not name:
        raise CompileError(f"{relpath}: '## Identity' is missing a '- **Name:**' entry")
    if not role:
        raise CompileError(f"{relpath}: '## Identity' is missing a '- **Role:**' entry")
    return name, role


def _parse_skills(body: str, relpath: str) -> list[str]:
    skills: list[str] = []
    for line in _section_lines(body, "Top skills (summary)"):
        match = BOLD_LABEL_RE.match(line)
        if not match:
            continue
        items = match.group(2).split(",")
        skills.extend(item.strip() for item in items if item.strip())
    if not skills:
        raise CompileError(
            f"{relpath}: '## Top skills (summary)' has no '- **Domain:** a, b' bullets"
        )
    return skills


def _parse_experience(body: str, relpath: str) -> list[dict]:
    experience: list[dict] = []
    for line in _section_lines(body, "Career timeline (corrected)"):
        match = TIMELINE_RE.match(line)
        if not match:
            continue
        period, remainder = match.group(1).strip(), match.group(2).strip()
        if "," in remainder:
            role, company = (part.strip() for part in remainder.split(",", 1))
        else:
            role, company = remainder, ""
        experience.append(
            {"role": role, "company": company, "period": period, "highlights": []}
        )
    if not experience:
        raise CompileError(
            f"{relpath}: '## Career timeline (corrected)' has no "
            f"'- **PERIOD:** Role, Company' bullets"
        )
    return experience


def _story_fields(body: str) -> dict:
    story = {}
    found_any = False
    for key in ("situation", "task", "action", "result"):
        lines = _section_lines(body, key.capitalize())
        story[key] = "\n".join(lines)
        if lines:
            found_any = True
    if not found_any:
        story["situation"] = "\n".join(
            line.strip() for line in body.splitlines() if line.strip()
        )
        story["task"] = story["action"] = story["result"] = ""
    return story


def _build_profile(pages: list[tuple[str, str, dict, str]]) -> dict:
    meta_by_rel = {rel: (meta, body) for rel, folder, meta, body in pages}
    if PROFILE_REL not in meta_by_rel:
        raise CompileError(f"{PROFILE_REL} not found in wiki")

    meta, body = meta_by_rel[PROFILE_REL]
    name, title = _parse_identity(body, PROFILE_REL)
    summary = meta.get("summary_1line", "")

    projects = []
    for rel, folder, pmeta, pbody in sorted(pages, key=lambda page: page[0]):
        if folder != "projects":
            continue
        stem = Path(rel).stem
        projects.append(
            {
                "name": stem,
                "description": pmeta.get("summary_1line", ""),
                "technologies": list(pmeta.get("tags") or []),
                "highlights": [
                    line.lstrip("-").strip() for line in _bullet_and_text_lines(pbody)
                ],
            }
        )

    stories = []
    for _rel, folder, _smeta, sbody in sorted(pages, key=lambda page: page[0]):
        if folder != "stories":
            continue
        stories.append(_story_fields(sbody))

    return {
        "name": name,
        "title": title,
        "summary": summary,
        "skills": _parse_skills(body, PROFILE_REL),
        "experience": _parse_experience(body, PROFILE_REL),
        "projects": projects,
        "stories": stories,
    }


def _bullet_and_text_lines(body: str) -> list[str]:
    """Bullet lines from body, excluding headings (design §4 projects highlights)."""
    out = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", "---")):
            continue
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
    return out


def build(wiki_root: Path, dest: Path) -> tuple[int, list[str]]:
    """Build the full candidate tree into existing dir `dest`.

    Returns (file_count, skipped_relpaths). Skips type/folder mismatches with a
    log entry — defense in depth; unreachable when the CLI gate runs first.
    """
    pages: list[tuple[str, str, dict, str]] = []
    skipped: list[str] = []
    for relpath, folder, meta, body in iter_wiki_files(wiki_root):
        if meta is None:
            continue
        ftype = meta.get("type")
        if ftype in TYPE_TO_FOLDER and TYPE_TO_FOLDER[ftype] != folder:
            skipped.append(relpath)
            continue
        pages.append((relpath, folder, meta, body))

    dest.mkdir(parents=True, exist_ok=True)
    docs_dir = dest / "docs"
    docs_dir.mkdir()

    profile = _build_profile(pages)
    (dest / "profile.json").write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    count = 1  # profile.json
    for relpath, folder, meta, body in sorted(pages, key=lambda page: page[0]):
        if folder == "profile":
            continue  # synthesizes profile.json, never emitted as a doc
        doc_name = f"{meta['type']}-{Path(relpath).stem}.md"
        (docs_dir / doc_name).write_text(body, encoding="utf-8")
        count += 1
    return count, skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile wiki/ into the candidate/ deployment tree."
    )
    parser.add_argument("--wiki", default="wiki", help="Wiki root (default: wiki)")
    parser.add_argument(
        "--out", default="candidate", help="Output dir (default: candidate)"
    )
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    out = Path(args.out).resolve()
    if not wiki_root.is_dir():
        print(f"[ERROR] wiki path is not a directory: {wiki_root}", file=sys.stderr)
        return 2

    # Step 1: internal validation gate (D4) — zero writes on any error.
    try:
        errors, warnings, n_files = collect_findings(wiki_root)
    except OSError as exc:
        print(f"[ERROR] cannot read wiki: {exc}", file=sys.stderr)
        return 2
    for relpath, msg in errors:
        print(f"[ERROR] {relpath}: {msg}")
    if errors:
        print(f"Validation failed: {len(errors)} errors, {len(warnings)} warnings")
        return 1
    for relpath, msg in warnings:
        print(f"[WARN] {relpath}: {msg}")

    # Step 2: build complete tree in <out>.tmp-<pid>/.
    tmp = Path(str(out) + f".tmp-{os.getpid()}")
    try:
        tmp.mkdir(parents=True)
    except OSError as exc:
        print(f"[ERROR] cannot create temp dir {tmp}: {exc}", file=sys.stderr)
        return 2
    try:
        n_built, skipped = build(wiki_root, tmp)
    except CompileError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[ERROR] build failed: {exc}", file=sys.stderr)
        return 2
    for relpath in skipped:
        print(f"[SKIP] {relpath}: type/folder mismatch (defense-in-depth skip)")

    # Step 3: two-rename swap (D3): out -> prev, tmp -> out, rmtree(prev).
    prev = Path(str(out) + ".prev")
    had_prev_out = False
    try:
        if out.exists():
            if prev.exists():
                shutil.rmtree(prev)
            os.replace(out, prev)
            had_prev_out = True
        os.replace(tmp, out)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        print(
            f"[ERROR] atomic swap failed (prior state may be at {prev}): {exc}",
            file=sys.stderr,
        )
        return 2
    if had_prev_out:
        try:
            shutil.rmtree(prev)
        except OSError:
            print(f"[WARN] could not remove {prev}", file=sys.stderr)

    print(
        f"Replaced: {n_built} files written to {out} "
        f"(from {n_files} wiki files, {len(skipped)} skipped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
