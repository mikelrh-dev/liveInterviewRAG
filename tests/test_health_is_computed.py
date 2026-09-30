"""``/api/health`` must compute its status, not assert it.

THE DEFECT
----------
``backend/routers/system.py`` returned ``{"status": "ok", ...}`` as a literal.
Nothing in the process decided it, so nothing could contradict it:

* ``PersistenceService.initialize`` catches ``sqlite3.DatabaseError``, renames
  the file aside, recreates it, and if THAT also fails, logs ERROR and
  **returns** -- no exception ever reaches a caller. A store that cannot be
  opened therefore costs the operator a log line and nothing else.
* An empty RAG index reports ``rag_chunks: 0`` and still ``status: "ok"``,
  because a count of zero is not a field anybody checks.
* Every other field is invariant under the TF-IDF fallback, which is why
  ``rag_mode`` had to be added before the page could see that failure at all.

A monitor polling this endpoint has nothing to interpret: ``ok`` is a string
the code chose, and the code chose it before looking at anything.

WHAT IS ASSERTED HERE
---------------------
* ``status`` is derived from what the service already knows. A healthy service
  says ``ok``; every degradation below says ``degraded`` and is not ``ok``.
* The degradations the brief names: no RAG chunks, the TF-IDF fallback, an
  uninitialised pipeline, an unopenable store.
* The endpoint is still a 200 publishing the fields it always published, so the
  rate limiter, the page and any external monitor keep working.
"""

import asyncio
import contextlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from backend import container
from backend.routers import system
from backend.services.persistence import PersistenceService

HEALTHY = {
    "whisper_loaded": True,
    "candidate_loaded": True,
    "rag_chunks": 124,
    "rag_mode": "embeddings",
    "persistence": "ok",
}


@contextlib.contextmanager
def _deployment(**overrides):
    """A container shaped like a real deployment, or like a broken one."""
    fields = {**HEALTHY, **overrides}

    rag = MagicMock()
    rag.chunks = [object()] * fields["rag_chunks"]
    rag.mode = fields["rag_mode"]

    stt = MagicMock()
    stt.is_loaded = fields["whisper_loaded"]

    profile = MagicMock()
    profile.profile_data = {"name": "x"} if fields["candidate_loaded"] else None

    store = MagicMock()
    store.health.return_value = fields["persistence"]

    with contextlib.ExitStack() as stack:
        for name, replacement in {
            "rag_pipeline": lambda: rag,
            "stt_service": lambda: stt,
            "candidate_profile": lambda: profile,
            "persistence": lambda: store,
        }.items():
            stack.enter_context(patch.object(container, name, replacement))
        yield store


def _health(**overrides) -> dict:
    with _deployment(**overrides):
        return asyncio.run(system.health_check())


# ─── A healthy service still says ok ───────────────────────────────────────


class TestAHealthyService:
    def test_the_status_is_ok(self):
        payload = _health()
        assert payload["status"] == "ok", (
            f"a fully loaded service did not report ok: {payload}"
        )

    def test_no_problems_are_reported(self):
        assert _health()["problems"] == []

    def test_the_previous_fields_are_still_published(self):
        """The shape is a compatibility surface: middleware, page, monitors."""
        payload = _health()
        for field in (
            "whisper_loaded",
            "rag_chunks",
            "rag_mode",
            "candidate_loaded",
        ):
            assert field in payload, f"/api/health stopped publishing {field}"

    def test_it_is_still_a_200(self):
        """Degraded is a body, not a status code.

        A 503 here would tell nginx the app is down, and every other field in
        the payload is still true. The candidate's page is served by the same
        process, so an unreachable /api/health is an unreachable interview.
        """
        from fastapi.testclient import TestClient

        from backend.main import app

        with _deployment(rag_mode="tfidf"):
            response = TestClient(app).get("/api/health")

        assert response.status_code == 200, (
            f"/api/health answered {response.status_code}; a degraded body is "
            "not an outage"
        )
        assert response.json()["status"] == "degraded"


# ─── Each degradation, and none of them is "ok" ─────────────────────────────


class TestDegradationsAreVisible:
    @pytest.mark.parametrize(
        "field,value,why",
        [
            pytest.param(
                "rag_chunks", 0, "an empty index retrieves nothing", id="no-chunks"
            ),
            pytest.param(
                "rag_mode", "tfidf", "the fallback is not the embedder", id="tfidf"
            ),
            pytest.param(
                "rag_mode", "uninitialized", "no model has been loaded",
                id="uninitialised",
            ),
            pytest.param(
                "whisper_loaded", False, "nothing can be transcribed", id="no-whisper"
            ),
            pytest.param(
                "candidate_loaded", False, "there is no profile to answer from",
                id="no-profile",
            ),
            pytest.param(
                "persistence", "error", "turns are not reaching the store",
                id="store-down",
            ),
        ],
    )
    def test_it_is_not_ok(self, field, value, why):
        payload = _health(**{field: value})
        assert payload["status"] != "ok", (
            f"{field}={value!r} ({why}) still reported ok: {payload}"
        )
        assert payload["status"] == "degraded", payload

    def test_every_degradation_names_itself(self):
        payload = _health(rag_chunks=0, rag_mode="tfidf", persistence="error")
        assert set(payload["problems"]) == {"rag_chunks", "rag_mode", "persistence"}, (
            f"a degraded body does not say what is wrong: {payload}"
        )

    def test_a_store_that_is_switched_off_is_not_a_failure(self):
        """``disabled`` is a decision, not an outage.

        The deployment has told this process not to write, so nothing it would
        have written is missing. Reporting that as degraded would paint a rail
        amber over a working instance every time an operator turns a flag off.
        """
        assert _health(persistence="disabled")["status"] == "ok"


# ─── An unreachable store is reflected, not swallowed ───────────────────────


class TestTheStoreIsActuallyReachable:
    """A live probe, not a cached flag.

    ``PersistenceService.initialize`` never raises: it logs ERROR and returns
    when recovery also fails. So the only way health can learn the store is
    dead is by asking it, and the answer has to come from the filesystem rather
    than from a flag somebody forgot to clear.
    """

    def test_a_writable_store_reports_ok(self, tmp_path):
        store = PersistenceService(tmp_path / "interviewtts.db")
        store.initialize()
        assert store.health() == "ok"

    def test_a_store_that_cannot_be_opened_reports_error(self, tmp_path):
        """A directory where the database file belongs: sqlite cannot open it."""
        blocked = tmp_path / "interviewtts.db"
        blocked.mkdir()
        store = PersistenceService(blocked)
        assert store.health() == "error"

    def test_a_disabled_store_says_so(self, tmp_path):
        store = PersistenceService(tmp_path / "interviewtts.db", enabled=False)
        assert store.health() == "disabled"

    def test_health_never_raises(self, tmp_path):
        """This runs inside a GET that a browser and a monitor both poll.

        A health endpoint that can 500 is worse than one that lies: it turns
        the honest signal into a connection error nobody can act on.
        """
        store = PersistenceService(tmp_path / "interviewtts.db")
        store.initialize()
        assert store.health() == "ok"

    def test_a_dead_store_shows_up_in_the_health_body(self, tmp_path):
        """The end-to-end claim, with the real store and no mock in the way.

        The path is built so ``initialize`` CANNOT rescue it: the parent of the
        database file is a regular file, so there is nowhere to create the
        directory sqlite needs and nothing to quarantine. A store pointed at a
        directory that looks like a database file would be renamed aside and
        rebuilt, which is the recovery working, not a dead store.
        """
        parent = tmp_path / "not-a-directory"
        parent.write_text("a regular file", encoding="utf-8")
        store = PersistenceService(parent / "interviewtts.db")
        store.initialize()
        assert store.health() == "error", (
            "the store recovered, so this test is not exercising a dead store"
        )

        with _deployment(persistence="ok"):
            with patch.object(container, "persistence", lambda: store):
                payload = asyncio.run(system.health_check())

        assert payload["persistence"] == "error", payload
        assert payload["status"] == "degraded", payload
        assert "persistence" in payload["problems"], payload


class TestTheEndpointResistsAWrongAnswer:
    def test_a_store_that_answers_nonsense_is_not_assumed_healthy(self):
        """A store that cannot name its own state is not evidence of health.

        Same reasoning as ``conversation.store_is_configured``: the two errors
        are not symmetric. Answering "error" for an unfamiliar store costs one
        amber rail; answering "ok" paints green over a store nobody has heard
        from.
        """
        payload = _health(persistence="<MagicMock id=140234...>")
        assert payload["status"] == "degraded", payload

    def test_a_store_that_raises_is_reported_rather_than_propagated(self):
        store = MagicMock()
        store.health.side_effect = sqlite3.DatabaseError("file is not a database")

        with patch.object(container, "persistence", lambda: store):
            payload = asyncio.run(system.health_check())

        assert payload["persistence"] == "error", payload
        assert payload["status"] == "degraded", payload
