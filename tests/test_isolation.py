"""The suite must not write to the production audio / reports / data directories.

A regression net for a defect that never failed a single test. The streaming
doubles synthesise into the real ``config.AUDIO_DIR``, the farewell path renders
a report through the real ``ReportService`` and records a row through the real
``PersistenceService``, and one test drove the real eviction sweep against the
real database. A full run therefore left ``reports/`` and ``audio/`` growing and
``data/interviewtts.db`` byte-changed -- measured at 12 report directories, 45
audio files and 90 database rows per run, on top of 1253 report directories that
had already accumulated.

The assertions are on the production tree itself rather than on a fixture's
configuration, so they fail if the redirection is removed, narrowed to some
targets but not others, or bypassed by a new write site. Only the four external
services are doubled: the report renderer, the persistence write path, the
eviction sweep and the ``/audio`` mount are the real code, exercised for real.
"""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import config

#: Trips ``detect_farewell`` and is not a cached question, so the turn takes the
#: branch that synthesises audio, renders a report AND records rows -- the whole
#: defect surface in a single request.
FAREWELL_INPUT = "Muchas gracias, eso es todo"

#: The directories the running product writes to. A test that lands a byte in
#: any of them has escaped isolation.
PRODUCTION_DIRS = ("audio", "reports", "data")


# ─── Fingerprinting the production tree ────────────────────────────────────


def _tree_fingerprint(directory: Path) -> set[tuple[str, int, int]]:
    """``(relative path, size, mtime_ns)`` for every file under ``directory``.

    Size and mtime rather than a content hash on purpose: the question is
    "did anything appear or change", and hashing ~1.5k files twice per test
    would cost more than the test is worth. An in-place rewrite moves the
    mtime, so a truncated report or an overwritten mp3 is still caught.
    """
    if not directory.is_dir():
        return set()
    return {
        (str(p.relative_to(directory)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in directory.rglob("*")
        if p.is_file()
    }


def _db_fingerprint() -> dict[str, str]:
    """A digest of every row of every table in the real database.

    Counts alone would miss an in-place ``UPDATE``; a digest over the ordered
    row contents cannot.

    The database is in WAL mode, and a read-only connection cannot always see
    un-checkpointed writes -- it would happily digest the main file and report
    a table as unchanged while a ``DELETE`` sits in the sidecar. So the three
    files are copied first and the digest is taken on the copy. That reads
    production and writes only to ``tmp_path``; the real database is never
    opened for writing, not even to checkpoint it.
    """
    db = config.BASE_DIR / "data" / "interviewtts.db"
    if not db.exists():
        return {}
    with tempfile.TemporaryDirectory() as staging:
        for suffix in ("", "-wal", "-shm"):
            src = db.with_name(db.name + suffix)
            if src.exists():
                shutil.copy2(src, Path(staging) / src.name)
        con = sqlite3.connect(str(Path(staging) / db.name))
        try:
            out = {}
            for (table,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ):
                digest = hashlib.sha256()
                for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                    digest.update(repr(row).encode("utf-8"))
                out[table] = digest.hexdigest()
            return out
        finally:
            con.close()


def _production_fingerprint() -> dict[str, object]:
    """Everything the product owns, as one comparable value."""
    return {
        name: _tree_fingerprint(config.BASE_DIR / name) for name in PRODUCTION_DIRS
    } | {"_db": _db_fingerprint()}


def _describe(before: dict, after: dict) -> str:
    """Name exactly what changed, so a failure is diagnosable without a rerun."""
    lines = []
    for name in PRODUCTION_DIRS:
        b, a = {p: rest for p, *rest in before[name]}, {p: rest for p, *rest in after[name]}
        for path in sorted(a.keys() - b.keys()):
            lines.append(f"  created:  {name}/{path}")
        for path in sorted(b.keys() - a.keys()):
            lines.append(f"  deleted:  {name}/{path}")
        for path in sorted(a.keys() & b.keys()):
            if a[path] != b[path]:
                lines.append(
                    f"  modified: {name}/{path} "
                    f"(size {b[path][0]}->{a[path][0]}, "
                    f"mtime changed: {b[path][1] != a[path][1]})"
                )
    for table in sorted(set(before["_db"]) | set(after["_db"])):
        if before["_db"].get(table) != after["_db"].get(table):
            lines.append(f"  db rows changed in table: {table}")
    return "\n".join(lines) or "(no per-file detail)"


# ─── Doubles: the four external services, nothing else ─────────────────────


@pytest.fixture
def streaming_doubles():
    """STT / LLM / TTS / RAG / profile doubled; everything else real.

    ``synthesize`` writes a real (empty) file at the path the pipeline asked
    for, so the report renderer, the persistence layer and the ``/audio`` mount
    all do their genuine work against whatever directory the pipeline names.
    That is the point: the test proves the pipeline names a disposable
    directory, rather than proving it names nothing.
    """
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = FAREWELL_INPUT
        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]
        mock_llm.generate.return_value = "I built InterviewTTS using Python."
        mock_llm.generate_stream_with_context.return_value = (iter(["Hi."]), [])

        async def mock_synthesize(text, output_path=None):
            # No CWD-relative fallback: writing to "audio/test.mp3" would land
            # in the real tree whenever a caller omitted output_path.
            path = Path(output_path) if output_path else config.AUDIO_DIR / "orphan.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

        mock_tts.synthesize = mock_synthesize
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {"cv.md": "content"}
        yield mock_tts


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()
    from backend.main import conversations

    conversations.clear()


@pytest.fixture
def client():
    from backend.main import app

    return TestClient(app)


def _stream(client, conversation_id) -> list[dict]:
    with client.stream(
        "POST",
        f"/api/conversation/{conversation_id}/message/stream",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    ) as response:
        assert response.status_code == 200
        body = "".join(
            f"{line}\n"
            for line in response.iter_lines()
            if line and line.startswith("data: ")
        )
    return [json.loads(line[6:]) for line in body.splitlines() if line.strip()]


# ─── The guard ──────────────────────────────────────────────────────────────


class TestProductionStateIsUntouched:
    """A test run must leave the running product's own state exactly as found."""

    def test_a_complete_interview_turn_writes_nothing_to_production(
        self, client, streaming_doubles
    ):
        """One farewell turn touches audio, a report file and four DB tables.

        The farewell branch is the only place where all three land on a single
        request -- it synthesises the goodbye, renders the transcript through
        the real ``ReportService`` and records the conversation, turn, message
        and report rows. Any of the three still pointing at production shows up
        here as a named file or a named table.
        """
        before = _production_fingerprint()

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream(client, conversation_id)
        types = [e["event"] for e in events]

        # The turn must actually have done the work, or the test proves nothing.
        assert "interview_end" in types, f"the turn never completed: {types}"
        assert any(e["event"] == "audio_url" for e in events), (
            f"no audio was queued, so the audio write path was never exercised: {types}"
        )

        after = _production_fingerprint()
        assert after == before, (
            "the suite wrote to production-shaped state:\n"
            f"{_describe(before, after)}\n"
            "Point the audio, reports and database targets at a tmp directory."
        )

    def test_the_eviction_sweep_prunes_the_isolated_store_not_production(
        self, monkeypatch, streaming_doubles
    ):
        """The sweep really prunes, and what it prunes is this test's own store.

        ``prune_conversations`` deletes every conversation older than
        ``SESSION_TTL_HOURS``. Driving the real sweep used to point it at the
        real database, so a run deleted *real* conversations rather than merely
        adding debris -- measured at 57 rows in a single file-level run, which
        is why the store's oldest timestamp crept forward to "everything is
        newer than the TTL" and the damage silently stopped re-arming until a
        real interview aged past two hours.

        Both halves of the assertion are load-bearing. A test that seeded a
        stale row and watched it survive would pass even if the sweep did
        nothing at all, so the stale row has to be *gone* from the isolated
        store afterwards: the sweep is real, and it is pointed at tmp.

        The store is checked before anything is seeded. While it pointed at
        production, seeding the row the sweep deletes would have been the very
        destruction under test, so the test refuses to arm and says why.
        """
        import asyncio

        import backend.main as main_mod

        target = Path(main_mod.persistence.db_path).resolve()
        if target.is_relative_to(config.BASE_DIR):
            pytest.fail(
                "the eviction sweep is pointed at the real database "
                f"({target}). Seeding a stale row here would delete production "
                "data, so this test refuses to arm. Redirect persistence.db_path "
                "to a tmp database."
            )

        stale = "0" * 32
        seeded = main_mod.persistence.__class__(target)
        seeded.initialize()
        con = sqlite3.connect(str(target))
        try:
            con.execute(
                "INSERT INTO conversations (id, summary, created_at, last_activity_at)"
                " VALUES (?, '', '2020-01-01T00:00:00', '2020-01-01T00:00:00')",
                (stale,),
            )
            con.commit()
            assert con.execute(
                "SELECT COUNT(*) FROM conversations WHERE id = ?", (stale,)
            ).fetchone()[0] == 1, "the stale conversation was never seeded"

            main_mod.conversations[stale] = {
                "messages": [
                    {"user_text": "hola", "response_text": "hola!", "audio_url": ""}
                ],
                "created_at": "2020-01-01T00:00:00",
                "last_activity_at": "2020-01-01T00:00:00",
            }
            # Silence the two branches that are not under test, and make the
            # tick finite. The persistence target is deliberately NOT patched:
            # the point is that nothing has to be to make the sweep safe.
            monkeypatch.setattr(
                main_mod.report_service, "generate", lambda *a, **k: None
            )
            monkeypatch.setattr(main_mod, "cleanup_stale_audio", lambda: None)

            real_sleep = asyncio.sleep
            ticks = {"n": 0}

            async def fake_sleep(_seconds):
                ticks["n"] += 1
                if ticks["n"] >= 2:
                    raise GeneratorExit
                await real_sleep(0)

            monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)

            before = _production_fingerprint()
            with pytest.raises((GeneratorExit, RuntimeError, StopAsyncIteration)):
                asyncio.run(main_mod.periodic_cleanup(interval_seconds=0))
            after = _production_fingerprint()

            assert con.execute(
                "SELECT COUNT(*) FROM conversations WHERE id = ?", (stale,)
            ).fetchone()[0] == 0, (
                "the sweep did not prune the stale conversation from the store "
                "it was pointed at, so this test proves nothing about isolation"
            )
        finally:
            con.close()

        assert after == before, (
            "the eviction sweep mutated production state:\n"
            f"{_describe(before, after)}\n"
            "Redirect the persistence target to a tmp database."
        )


class TestRedirectionWiring:
    """The targets themselves, asserted individually.

    The behavioural test above proves the *effect*; this proves the *mechanism*
    survives an edit, and names the target when it is moved back.
    """

    def test_audio_reports_and_database_are_outside_the_project_tree(
        self, isolated_write_targets
    ):
        from backend.main import persistence, report_service, tts_service

        targets = {
            "config.AUDIO_DIR": config.AUDIO_DIR,
            "config.REPORTS_DIR": config.REPORTS_DIR,
            "config.DB_PATH": config.DB_PATH,
            "tts_service.output_dir": tts_service.output_dir,
            "report_service.output_dir": report_service.output_dir,
            "persistence.db_path": persistence.db_path,
        }

        for name, target in targets.items():
            resolved = Path(target).resolve()
            assert not resolved.is_relative_to(config.BASE_DIR), (
                f"{name} still points inside the project: {resolved}"
            )
            assert resolved.is_relative_to(isolated_write_targets.root), (
                f"{name} is not under this test's tmp_path: {resolved}"
            )

    def test_the_audio_mount_serves_the_isolated_directory(self):
        """The ``/audio`` mount captured the real directory at import time.

        Redirecting ``config.AUDIO_DIR`` alone leaves the app serving production
        audio, so a generated URL resolves against the real tree and a test that
        only checks "the URL is fetchable" passes for the wrong reason.
        """
        from fastapi.staticfiles import StaticFiles

        from backend.main import app

        mounts = [
            r for r in app.router.routes
            if getattr(r, "path", None) == "/audio" and isinstance(r.app, StaticFiles)
        ]
        assert len(mounts) == 1, f"expected exactly one /audio mount, got {len(mounts)}"
        served = Path(mounts[0].app.directory).resolve()
        assert served == config.AUDIO_DIR.resolve(), (
            f"/audio serves {served} but the pipeline writes to "
            f"{config.AUDIO_DIR.resolve()}"
        )
        assert not served.is_relative_to(config.BASE_DIR), (
            f"/audio still serves production audio: {served}"
        )
