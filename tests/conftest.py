"""Suite-wide redirection of the product's own write targets.

A test run used to write into ``audio/``, ``reports/`` and
``data/interviewtts.db`` -- the directories the running product owns. Nothing
failed, so the debris just accumulated: 1253 report directories and 1253
matching ``reports`` rows, growing 12 directories and ~90 rows per run, and one
test driving the real eviction sweep deleted real conversations rather than
merely adding to the pile.

What moves here is the *target*, not the logic. The report renderer, the
persistence write path, the eviction sweep and the ``/audio`` mount are all the
real code doing the real work; they are simply pointed at a directory pytest
deletes. Nothing here replaces a service with a double, so a test that needed
``ReportService.generate`` to really write a file still gets a real file --
under ``tmp_path`` instead of under the repository.

Four read sites hold a path, and all four have to move. Missing one is silent:
``config.AUDIO_DIR`` is read per request, but the mount and the three service
singletons captured their directories when ``backend.main`` was imported, and
the import happens once per process. Redirecting the config alone therefore
leaves the app writing to production audio, and the sweep still pointed at the
real database.
"""

from types import SimpleNamespace

import pytest

from backend.config import config


def _redirect_audio_mount(app, directory, monkeypatch) -> None:
    """Point the ``/audio`` ``StaticFiles`` mount at ``directory``, in place.

    ``app.mount`` resolved ``config.AUDIO_DIR`` at import time. Rebuilding the
    route list would not help: Starlette matches routes in registration order,
    so a replacement mount would sit behind the original and never be reached,
    and a test asserting "the generated URL is fetchable" would keep passing
    against the real tree -- the exact failure this exists to prevent.

    ``StaticFiles`` reads ``directory`` and the ``all_directories`` list derived
    from it on every request, so updating both in place redirects the mount
    without touching routing. ``monkeypatch`` restores both on teardown.
    """
    from fastapi.staticfiles import StaticFiles

    mounts = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/audio"
        and isinstance(getattr(route, "app", None), StaticFiles)
    ]
    if len(mounts) != 1:
        raise AssertionError(
            f"expected exactly one /audio StaticFiles mount, found {len(mounts)}. "
            "Isolation cannot redirect a mount it cannot find; a route was "
            "probably renamed or re-registered."
        )
    static = mounts[0].app
    monkeypatch.setattr(static, "directory", str(directory))
    monkeypatch.setattr(
        static,
        "all_directories",
        static.get_directories(str(directory), static.packages),
    )


@pytest.fixture(autouse=True)
def isolated_write_targets(tmp_path, monkeypatch):
    """Point every production-shaped write target at this test's ``tmp_path``.

    Autouse because the escape is silent: a test that forgets to opt in does
    not fail, it just quietly writes another report directory. The returned
    namespace is what a test should name when it needs to assert on a file the
    product wrote -- ``isolated_write_targets.audio / name`` rather than
    ``config.AUDIO_DIR / name``, which reads the same and stops being isolated
    the moment the redirection is removed.
    """
    import backend.main as main_mod

    # Namespaced under a subdirectory rather than placed directly in
    # ``tmp_path``. Several tests build their own throwaway targets at the
    # obvious names -- ``tmp_path / "audio"``, ``tmp_path / "reports"`` -- and
    # mkdir them without ``exist_ok``; squatting on those names turned a working
    # suite into eight setup errors. A test that wants its own targets keeps
    # them, and the two never collide.
    root = tmp_path / "isolated"
    audio = root / "audio"
    reports = root / "reports"
    data = root / "data"
    database = data / "interviewtts.db"
    for directory in (audio, reports, data):
        directory.mkdir(parents=True, exist_ok=True)

    # Read at request time by the streaming pipeline, so this is the read site
    # that decides where synthesised audio lands.
    monkeypatch.setattr(config, "AUDIO_DIR", audio)
    monkeypatch.setattr(config, "REPORTS_DIR", reports)
    monkeypatch.setattr(config, "DB_PATH", database)

    # Captured at import time, one per process, and therefore invisible to a
    # config patch.
    _redirect_audio_mount(main_mod.app, audio, monkeypatch)
    monkeypatch.setattr(main_mod.tts_service, "output_dir", audio)
    monkeypatch.setattr(main_mod.report_service, "output_dir", reports)
    monkeypatch.setattr(main_mod.persistence, "db_path", database)
    monkeypatch.setattr(main_mod.semantic_cache, "db_path", database)

    # Both stores cache "I have applied the DDL" as a per-instance flag, and
    # they set it against the *previous* path while the app was starting up.
    # Repointing ``db_path`` without clearing it hands every write a fresh file
    # with no tables in it, and the failure surfaces as
    # ``no such table: conversations`` -- which reads like a product defect
    # rather than like a mis-pointed test. Clearing the flag lets the lazy
    # ``_ensure_schema`` on the next write build the new file.
    monkeypatch.setattr(main_mod.persistence, "_schema_ready", False)
    monkeypatch.setattr(main_mod.semantic_cache, "_schema_ready", False)

    return SimpleNamespace(
        root=tmp_path, audio=audio, reports=reports, data=data, db=database
    )


@pytest.fixture
def fixture_corpus_targets(tmp_path, monkeypatch):
    """Point every corpus-shaped config at this test's ``tmp_path``.

    Layered on the autouse ``isolated_write_targets``, which already moves
    ``AUDIO_DIR`` / ``REPORTS_DIR`` / ``DB_PATH``. What is left are the three
    paths that name the candidate's own data, and they are the ones that let
    a test silently fall back to the developer's real checkout:

    ``WIKI_DIR``
        read by ``CandidateProfile`` and by the RAG retrieval tests. Left
        pointing at the repository, a test passes only on the machine whose
        owner happens to have the gitignored ``wiki/`` present — which is
        exactly the failure this fixture exists to remove.
    ``CANDIDATE_DIR``
        the compiled profile written by ``scripts/wiki/compile.py``, also
        gitignored.
    ``RAG_CACHE_DIR``
        the embedding cache. The pipeline's own default is ``cache_dir=None``,
        so nothing here writes it, but patching it means a future test that
        DOES pass a cache dir cannot reach the real one by omission.

    Returns the tmp root, so a test that needs a writable copy of the corpus
    says ``fixture_corpus_targets / "wiki"`` rather than reaching for
    ``config.WIKI_DIR``, which reads the same and stops being isolated the
    moment this fixture is removed.
    """
    from backend.config import config

    root = tmp_path / "fixture_corpus"
    candidate = root / "candidate"
    cache = root / "rag_cache"
    for directory in (root, candidate, cache):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "WIKI_DIR", root / "wiki")
    monkeypatch.setattr(config, "CANDIDATE_DIR", candidate)
    monkeypatch.setattr(config, "RAG_CACHE_DIR", cache)

    return root
