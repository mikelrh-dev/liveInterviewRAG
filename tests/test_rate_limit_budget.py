"""The rate limiter must budget the interview, not the interface that watches it.

THE DEFECT
----------
``RateLimitMiddleware`` charged *every* ``/api/`` request, but one turn of the
interview costs two of them: ``frontend/app.js`` POSTs the turn and then, on the
same turn, calls ``fetchContext`` → ``GET /api/conversation/{id}/context``.

At ``RATE_LIMIT_PER_MINUTE=10`` over a 60 s window that halves the sustainable
turn rate. The sliding window means the equilibrium sits at 10 requests / 2 per
turn = 5 turns per 60 s, i.e. one turn every 12 s, and a candidate answering in
8 s reaches the ceiling and starts collecting 429s mid-interview. The budget is
being spent on a read that renders the evidence panel, not on the recruiter's
question.

``GET /api/health`` is the same shape: a 60 s status poll that spends interview
budget on a dot in the status rail.

THE SECOND HALF: 429 WITHOUT A CLUE
------------------------------------
The 429 carried a sentence and no ``Retry-After``. The client already knows how
to obey one -- ``frontend/app.js:delayFor`` prefers a server-sent ``Retry-After``
over its local ladder -- so the server was withholding the only fact the client
would have used. It is the one thing the server knows and the client cannot
compute: when the oldest entry in the window expires.

WHAT IS DELIBERATELY NOT CHANGED
--------------------------------
The frontend retry policy. The 429 does not append to the limiter's timestamp
list (it returns before the append), so the ladder cannot amplify a rejection,
and the brief's instruction not to touch it is correct.
"""

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.conversation import _rate_limit_store
from backend.middleware import RateLimitMiddleware


@pytest.fixture(autouse=True)
def _clean_bucket():
    """The store is a process-wide dict; a leaked bucket is a false failure."""
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


def build_app(max_requests: int = 10) -> TestClient:
    """A real app wearing the real limiter, with one route per spend class.

    The route paths mirror ``backend/routers/`` exactly, because the exemption is
    a property of the PATH and a test that invented friendlier paths would pass
    against a production tree where the paths differ.
    """
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, max_requests=max_requests)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/config")
    def cfg():
        return {"tts_voice": "x"}

    @app.get("/api/conversation/{conversation_id}/context")
    def context(conversation_id: str):
        return {"chunks": []}

    @app.post("/api/conversation")
    def create():
        return {"conversation_id": "c"}

    @app.post("/api/conversation/{conversation_id}/message")
    def message(conversation_id: str):
        return {"answer": "ok"}

    @app.post("/api/conversation/{conversation_id}/message/stream")
    def stream(conversation_id: str):
        return {"answer": "ok"}

    return TestClient(app)


def one_turn(client: TestClient, n: int) -> list[int]:
    """What one turn actually costs on the wire: the POST plus its context read."""
    return [
        client.post(f"/api/conversation/c{n}/message").status_code,
        client.get(f"/api/conversation/c{n}/context").status_code,
    ]


class TestReadsDoNotSpendTheInterviewsBudget:
    def test_health_does_not_consume_the_budget(self):
        client = build_app(max_requests=3)
        for _ in range(20):
            assert client.get("/api/health").status_code == 200
        assert _rate_limit_store == {} or all(
            len(v) == 0 for v in _rate_limit_store.values()
        ), (
            "the health poll spent interview budget: "
            f"{ {k: len(v) for k, v in _rate_limit_store.items()} }"
        )

    def test_the_context_read_does_not_consume_the_budget(self):
        client = build_app(max_requests=3)
        for index in range(20):
            assert client.get(f"/api/conversation/c{index}/context").status_code == 200
        assert all(
            len(v) == 0 for v in _rate_limit_store.values()
        ), (
            "GET /context spent interview budget: "
            f"{ {k: len(v) for k, v in _rate_limit_store.items()} }"
        )

    def test_config_does_not_consume_the_budget(self):
        client = build_app(max_requests=3)
        for _ in range(20):
            assert client.get("/api/config").status_code == 200
        assert all(len(v) == 0 for v in _rate_limit_store.values())


class TestTheExemptionIsNotOverBroad:
    """The turn itself must still cost, or the limiter protects nothing.

    This is the guard on the fix, not a detail of it: exempting the
    ``/api/conversation/`` *prefix* would have freed ``POST .../message`` too,
    because the context read and the turn share a prefix. The limiter would then
    only ever see health and config traffic -- every read-only request in the
    application -- and no question a recruiter actually asks.
    """

    def test_the_turn_post_still_consumes_the_budget(self):
        client = build_app(max_requests=3)
        for index in range(3):
            assert client.post(f"/api/conversation/c{index}/message").status_code == 200
        assert client.post("/api/conversation/c9/message").status_code == 429

    def test_the_streaming_turn_still_consumes_the_budget(self):
        client = build_app(max_requests=3)
        for index in range(3):
            assert (
                client.post(f"/api/conversation/c{index}/message/stream").status_code
                == 200
            )
        assert client.post("/api/conversation/c9/message/stream").status_code == 429

    def test_creating_a_conversation_still_consumes_the_budget(self):
        client = build_app(max_requests=2)
        assert client.post("/api/conversation").status_code == 200
        assert client.post("/api/conversation").status_code == 200
        assert client.post("/api/conversation").status_code == 429


class TestRapidTurnsAreNoLongerRejected:
    def test_ten_turns_at_the_configured_limit_are_all_served(self):
        """The regression itself: a full window of turns, with their context reads.

        Ten turns is exactly ``RATE_LIMIT_PER_MINUTE``. Charged two units per
        turn, the sixth turn is already rejected -- which is the measured
        defect. Charged one unit per turn, the tenth is the last one served and
        nothing is rejected.
        """
        client = build_app(max_requests=10)
        statuses = [code for n in range(10) for code in one_turn(client, n)]
        assert statuses == [200] * 20, (
            f"ten turns with their context reads were not all served: {statuses}"
        )

    def test_reads_after_the_budget_is_spent_are_still_served(self):
        """Once the turns are done, the evidence panel must not go dark.

        The panel is what the candidate reads AFTER answering. If a full window
        of turns still starved it, the exemption would have moved the problem
        rather than solved it.
        """
        client = build_app(max_requests=5)
        for n in range(5):
            one_turn(client, n)
        assert client.get("/api/conversation/c0/context").status_code == 200
        assert client.get("/api/health").status_code == 200


class TestTheRejectionSaysWhenToComeBack:
    """A 429 the client cannot schedule against is a dead end.

    ``frontend/app.js:delayFor`` reads ``Retry-After`` and prefers it over the
    local ladder. The server is the only party that knows when its window
    empties, so withholding the value discards the one fact the client needed.
    """

    def test_a_rejection_carries_retry_after(self):
        client = build_app(max_requests=2)
        client.post("/api/conversation/a/message")
        client.post("/api/conversation/b/message")
        response = client.post("/api/conversation/c/message")
        assert response.status_code == 429
        assert response.headers.get("Retry-After") is not None, (
            "the 429 carries no Retry-After, so the client falls back to guessing"
        )

    def test_retry_after_is_the_seconds_until_the_oldest_entry_expires(
        self, monkeypatch
    ):
        """Not a fixed number: it is derived from the window's oldest entry.

        Two entries 10 s and 50 s old, in a 60 s window: the first frees a slot
        in 50 s, so that is what the header has to say. The 10 s one is irrelevant
        to the retry, and answering 10 would invite a rejection.
        """
        clock = {"now": 1_000.0}
        monkeypatch.setattr("backend.middleware.time.time", lambda: clock["now"])

        client = build_app(max_requests=2)
        assert client.post("/api/conversation/a/message").status_code == 200
        clock["now"] += 10
        assert client.post("/api/conversation/b/message").status_code == 200
        clock["now"] += 40  # entry "a" is 50 s old, "b" is 40 s old

        response = client.post("/api/conversation/c/message")
        assert response.status_code == 429
        stated = int(response.headers["Retry-After"])
        assert stated == 10, (
            f"Retry-After says {stated}s; the oldest entry is 50 s old in a 60 s "
            "window, so a slot frees in 10 s"
        )

    def test_retry_after_never_reports_zero_or_negative(self):
        """A 0 invites an immediate retry, which is a guaranteed second 429."""
        client = build_app(max_requests=1)
        client.post("/api/conversation/a/message")
        response = client.post("/api/conversation/b/message")
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 1

    def test_the_value_is_rounded_up_so_the_slot_really_exists(self):
        """Sub-second remainders must not be truncated into a premature retry."""
        clock = {"now": 5_000.0}
        client = build_app(max_requests=1)
        import backend.middleware as mw

        original = mw.time.time
        try:
            mw.time.time = lambda: clock["now"]
            client.post("/api/conversation/a/message")
            clock["now"] += 59.5  # 0.5 s left in the window
            response = client.post("/api/conversation/b/message")
        finally:
            mw.time.time = original

        assert response.status_code == 429
        stated = int(response.headers["Retry-After"])
        assert stated == math.ceil(0.5)
        assert stated >= 1
