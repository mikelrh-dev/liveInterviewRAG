# Synthetic retrieval corpus

**Every file in this tree is invented.** No sentence, heading, person,
employer, project, date, metric or skill claim in this tree was copied,
quoted, paraphrased or derived from the repository's real `wiki/`.

The persona is **Nuria Belvis Ferran**, a fictional production engineer
in a fictional ceramic-and-glass manufacturing sector. The employers
(`Cerámica Vinalar`, `Vidrio Almendro`, `Talleres Ribagorda`), the
projects (`Horno Siete`, `Ceniza`, `Aguja`, `Vitro`) and every
anecdote are invented. The stack named in the pages is generic
professional technology, which is not personal data.

## Why this exists

The retrieval tests used to read the owner's real `wiki/`, which is
gitignored (`.gitignore:77` → `/wiki/`) and backed up to a private
repository. Seven retrieval-quality tests carried floors measured on
that corpus, so the suite was green only on one machine. This tree
gives them a corpus that is committed, reviewable and legally free of
personal data.

## Shape

39 pages under the eight `type:` values, plus a generated `index.md`, a
`CONVENCIONES.md`, a `templates/faq-template.md` and per-folder
`README.md` stubs. The loader drops `index.md`, `README.md`,
`CONVENCIONES.md` and the `templates/` folder, so 39 pages are
ingested. The shape mirrors the real corpus: a section per page, a
handful of `## Fuentes` / `## Ver tambien` / `## Ver también` /
`## See also` link sections, FAQ pages whose H1 is the interviewer's
own question, narrative pages whose H1 is a role-and-years title, some
`[TODO` markers, one `confidence: low` page and several
`confidence: medium` pages.

## Prove the isolation

```console
python -m tests.fixture_corpus --verify-no-derivation
```

Runs the mechanical check: every multi-word phrase (2+ tokens) in every
fixture file, compared against every multi-word phrase in every real
wiki file, normalised for case and punctuation. Exits non-zero on any
shared phrase. Skips (exit 0) when `wiki/` is absent, which is the
normal state in a clean clone.
