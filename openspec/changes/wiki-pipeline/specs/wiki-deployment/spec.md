# Wiki Deployment Specification

## Purpose

Define the end-to-end deploy path from validated wiki content to the running VPS service, plus the git hygiene that keeps personal data out of the public repo.

## Requirements

### Requirement: Ordered deploy pipeline

`scripts/deploy.sh` SHALL execute, in strict order: validate → compile → rsync `candidate/` to the VPS → `systemctl restart interviewtts.service`. It SHALL abort at the first failing step without executing later steps. It MAY assume SSH public-key auth, a sudo-capable user, and port 22.

#### Scenario: Compile failure stops before rsync

- GIVEN invalid wiki content
- WHEN deploy.sh runs
- THEN it exits non-zero after the validation step, and neither rsync nor the service restart occurs

### Requirement: Unconditional overwrite with server-side retention

Deploy SHALL overwrite VPS `candidate/` unconditionally (wiki wins from day one, including first deploy) without prompting. Before replacing, the script SHALL retain one prior version server-side (e.g., `candidate.prev/`) and print a summary of what it replaced.

#### Scenario: First deploy replaces divergent VPS state

- GIVEN the VPS holds a `candidate/` differing from freshly compiled output
- WHEN deploy runs to completion
- THEN the VPS matches compiled output exactly, a `candidate.prev/` snapshot of the prior state exists, and the summary lists replaced content

### Requirement: Git privacy hygiene

The repo MUST stop tracking `candidate/` (`git rm -r --cached candidate/`; files remain on disk; `git ls-files candidate/` returns empty). `.gitignore` SHALL include `wiki/`. A rollback anchor tag `pre/wiki-pipeline` SHALL exist on the last pre-change commit.

#### Scenario: Personal data leaves the index

- GIVEN the untracking commit is made
- WHEN `git ls-files candidate/` runs and `git status` is inspected
- THEN no `candidate/` paths are tracked and `wiki/` appears ignored

### Requirement: Documented backup workflow

The project docs SHALL document the edit → validate → compile → deploy loop and a manual push-after-edit backup workflow for `wiki/` into a private GitHub repo. No automation hook SHALL be built in this change.

#### Scenario: Operator can follow documented loop

- GIVEN the README/docs section exists
- WHEN Mikel edits the wiki and follows the documented steps
- THEN each step's command succeeds in order and the wiki backup is pushed manually after editing
