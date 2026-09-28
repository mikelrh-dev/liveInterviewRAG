"""Tests for conversation memory (rolling summary + recent turns).

The conversation memory system keeps the LLM aware of prior turns:
- Older turns get compressed into a rolling summary (capped at ~1500 chars)
- Recent turns (default 3) are passed in full text
- This gives the LLM unlimited conversation length with bounded token cost
"""

import pytest

from fastapi.testclient import TestClient

from backend.main import (
    update_conversation_summary,
    build_conversation_context,
    conversations,
)


@pytest.fixture(autouse=True)
def clear_conversations():
    """Clear conversations store before each test."""
    conversations.clear()


# ─── update_conversation_summary ────────────────────────


def test_update_summary_first_turn():
    """First turn creates a summary entry."""
    conv_id = "test-mem-1"
    conversations[conv_id] = {"turns": []}
    turn = {"n": 0, "user_text": "¿Cuál es tu stack?", "assistant_text": "Python y FastAPI"}

    update_conversation_summary(conv_id, turn)

    summary = conversations[conv_id]["summary"]
    assert "¿Cuál es tu stack" in summary
    assert "Python y FastAPI" in summary
    assert "P:" in summary  # format marker


def test_update_summary_appends_multiple_turns():
    """Multiple turns accumulate in the summary."""
    conv_id = "test-mem-2"
    conversations[conv_id] = {"turns": []}

    update_conversation_summary(conv_id, {"n": 0, "user_text": "Q1", "assistant_text": "A1"})
    update_conversation_summary(conv_id, {"n": 1, "user_text": "Q2", "assistant_text": "A2"})
    update_conversation_summary(conv_id, {"n": 2, "user_text": "Q3", "assistant_text": "A3"})

    summary = conversations[conv_id]["summary"]
    assert "Q1" in summary
    assert "Q2" in summary
    assert "Q3" in summary
    # Most recent should appear at the END of the summary
    assert summary.rindex("Q1") < summary.rindex("Q2") < summary.rindex("Q3")


def test_update_summary_truncates_when_too_long():
    """When summary exceeds max chars, oldest entries are dropped."""
    conv_id = "test-mem-3"
    conversations[conv_id] = {"turns": []}

    # Generate 20 long turns to exceed MAX_SUMMARY_CHARS (1500)
    for i in range(20):
        long_q = f"Pregunta número {i} " + "x" * 80
        long_a = f"Respuesta número {i} " + "y" * 80
        update_conversation_summary(conv_id, {
            "n": i, "user_text": long_q, "assistant_text": long_a,
        })

    summary = conversations[conv_id]["summary"]
    # Should be bounded (allow some buffer for the "oldest omitted" prefix)
    assert len(summary) < 2500, f"Summary too long: {len(summary)} chars"
    # Oldest turns should be dropped
    assert "Pregunta número 0 " not in summary
    assert "Pregunta número 1 " not in summary
    # Most recent should be there
    assert "Pregunta número 19" in summary


def test_update_summary_preserves_recent_after_truncation():
    """Most recent turn is always present after truncation."""
    conv_id = "test-mem-4"
    conversations[conv_id] = {"turns": []}

    for i in range(20):
        update_conversation_summary(conv_id, {
            "n": i, "user_text": f"Q{i}", "assistant_text": f"A{i}",
        })

    summary = conversations[conv_id]["summary"]
    # The most recent (Q19, A19) should be present
    assert "Q19" in summary
    assert "A19" in summary


def test_update_summary_handles_empty_assistant_text():
    """Streaming failures (empty assistant) don't break the summary."""
    conv_id = "test-mem-5"
    conversations[conv_id] = {"turns": []}

    update_conversation_summary(conv_id, {
        "n": 0, "user_text": "Test question", "assistant_text": "",
    })

    # Should not raise; entry is still created
    summary = conversations[conv_id]["summary"]
    assert "Test question" in summary


def test_update_summary_missing_conversation():
    """If conversation doesn't exist, function does not raise."""
    conv_id = "test-mem-nonexistent"
    if conv_id in conversations:
        del conversations[conv_id]

    # Should silently no-op (or create empty) — not raise
    update_conversation_summary(conv_id, {"n": 0, "user_text": "Q", "assistant_text": "A"})


# ─── build_conversation_context ──────────────────────────


def test_build_context_empty_conversation():
    """No turns → empty context string."""
    conv_id = "test-mem-ctx-1"
    conversations[conv_id] = {"turns": []}

    context = build_conversation_context(conv_id, recent_count=3)
    assert context == ""


def test_build_context_only_recent_no_summary():
    """With 2 turns (less than recent_count=3), no summary section."""
    conv_id = "test-mem-ctx-2"
    conversations[conv_id] = {
        "turns": [
            {"n": 0, "user_text": "Q1", "assistant_text": "A1"},
            {"n": 1, "user_text": "Q2", "assistant_text": "A2"},
        ],
        "summary": "",
    }

    context = build_conversation_context(conv_id, recent_count=3)
    assert "Q1" in context
    assert "Q2" in context
    assert "[Resumen" not in context  # no summary section when < recent_count


def test_build_context_with_summary_and_recent():
    """6 turns: 3 older in summary, 3 recent in full text."""
    conv_id = "test-mem-ctx-3"
    conversations[conv_id] = {
        "turns": [
            {"n": 0, "user_text": "Q0", "assistant_text": "A0"},
            {"n": 1, "user_text": "Q1", "assistant_text": "A1"},
            {"n": 2, "user_text": "Q2", "assistant_text": "A2"},
            {"n": 3, "user_text": "Q3", "assistant_text": "A3"},
            {"n": 4, "user_text": "Q4", "assistant_text": "A4"},
            {"n": 5, "user_text": "Q5", "assistant_text": "A5"},
        ],
        "summary": "Q0 → A0\nQ1 → A1\nQ2 → A2",
    }

    context = build_conversation_context(conv_id, recent_count=3)

    # Summary section: 3 older turns
    assert "[Resumen" in context
    assert "Q0" in context
    assert "Q2" in context
    # Recent section: last 3 turns in full
    assert "[Últimos 3 turnos" in context
    assert "Q3" in context
    assert "Q4" in context
    assert "Q5" in context


def test_build_context_truncates_long_turns():
    """Long user/assistant text gets truncated to 200 chars."""
    conv_id = "test-mem-ctx-4"
    long_text = "x" * 500
    conversations[conv_id] = {
        "turns": [
            {"n": 0, "user_text": long_text, "assistant_text": long_text},
        ],
        "summary": "",
    }

    context = build_conversation_context(conv_id, recent_count=3)
    # 201+ x's should not appear (truncated to 200)
    assert "x" * 201 not in context, "Long text was not truncated"


def test_build_context_token_efficiency():
    """With many turns, context should not blow up in size."""
    conv_id = "test-mem-ctx-5"
    conversations[conv_id] = {
        "turns": [
            {"n": i, "user_text": f"Q{i}", "assistant_text": f"A{i} " + "x" * 50}
            for i in range(15)
        ],
        "summary": "\n".join(f"Q{i} → A{i}" for i in range(12)),
    }

    context = build_conversation_context(conv_id, recent_count=3)
    # Should contain: summary (12) + 3 recent = compact
    # Most recent (Q14) should be there
    assert "Q14" in context
    # But the full 15 turns in text form would be larger than this
    # Cap at a reasonable size
    assert len(context) < 2000, f"Context too large: {len(context)} chars"


def test_build_context_custom_recent_count():
    """recent_count parameter controls how many recent turns are detailed."""
    conv_id = "test-mem-ctx-6"
    conversations[conv_id] = {
        "turns": [
            {"n": i, "user_text": f"Q{i}", "assistant_text": f"A{i}"}
            for i in range(10)
        ],
        "summary": "summary here",
    }

    # With recent_count=2, only Q8, Q9 in full, rest in summary
    context = build_conversation_context(conv_id, recent_count=2)
    assert "[Últimos 2 turnos" in context
    assert "Q8" in context
    assert "Q9" in context
    # Q5 should NOT be in the recent section
    assert "Q5" not in context or "Q5" in context.split("[Últimos")[1] is False


def test_build_context_missing_conversation():
    """If conversation doesn't exist, return empty string."""
    conv_id = "test-mem-ctx-nonexistent"
    if conv_id in conversations:
        del conversations[conv_id]

    context = build_conversation_context(conv_id, recent_count=3)
    assert context == ""


# ─── Integration with build_system_prompt ──────────────


def test_system_prompt_accepts_conversation_context():
    """build_system_prompt accepts a conversation_context parameter."""
    from backend.prompts.candidate import build_system_prompt

    conv_context = "[Últimos 2 turnos]\n- P: stack?\n  R: Python."
    prompt = build_system_prompt("", conversation_context=conv_context)

    assert "stack?" in prompt
    assert "Python." in prompt
    assert "Últimos 2 turnos" in prompt


def test_system_prompt_without_conversation_context():
    """build_system_prompt works fine without conversation context."""
    from backend.prompts.candidate import build_system_prompt

    prompt = build_system_prompt("", conversation_context=None)
    assert "Mikel" in prompt  # base prompt intact

    prompt = build_system_prompt("")  # default behavior unchanged
    assert "Mikel" in prompt


# ─── last_activity_at ────────────────────────────────────


def test_last_activity_at_on_creation():
    """Conversation creation sets last_activity_at ISO datetime."""
    from backend.main import app
    client = TestClient(app)
    resp = client.post("/api/conversation")
    assert resp.status_code == 200
    conv_id = resp.json()["conversation_id"]
    assert conv_id in conversations
    assert "last_activity_at" in conversations[conv_id]
    # Verify it's ISO 8601 (can parse fromisoformat)
    from datetime import datetime
    dt = datetime.fromisoformat(conversations[conv_id]["last_activity_at"])
    assert dt.tzinfo is None  # UTC, no timezone


def test_last_activity_at_updated_on_message():
    """Send-message updates last_activity_at."""
    from unittest.mock import patch, MagicMock
    from backend.main import app

    # Mock services via patch
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:
        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = "Hello"
        mock_llm.generate.return_value = "Hi there!"
        mock_rag.get_context_string.return_value = ""
        mock_rag.chunks = []
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {}

        async def mock_synth(text, output_path=None):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.touch()
            return output_path
        mock_tts.synthesize = mock_synth

        client = TestClient(app)
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        original = conversations[conv_id]["last_activity_at"]

        resp = client.post(
            f"/api/conversation/{conv_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )
        assert resp.status_code == 200

        assert conversations[conv_id]["last_activity_at"] != original


# ─── store_is_configured: ask the store, don't reach into it ───


def _install_store(monkeypatch, store):
    """Point the composition root at ``store`` for the duration of a test."""
    import backend.main as main_mod

    monkeypatch.setattr(main_mod, "persistence", store)


class TestStoreIsConfigured:
    """``store_is_configured`` asks a public question about a service it does
    not own.

    It used to answer that question with ``getattr(store, "_enabled",
    config.PERSISTENCE_ENABLED)``. Two things were wrong with that:

    * the private attribute was the store's business, and renaming it inside
      ``persistence.py`` would have silently changed the caller's semantics
      while every test stayed green (the ``getattr`` default just starts
      answering instead);
    * the ``getattr`` default contradicted the docstring above it. The
      docstring said an unfamiliar store is treated as *live*; the default was
      ``config.PERSISTENCE_ENABLED``, which is a claim about the instance built
      at import time, not about the object actually installed. With
      ``PERSISTENCE_ENABLED=false`` in the environment the two disagree and the
      code took the branch its own docstring did not describe.

    The fallback is now stated once, in code, and matches the prose.
    """

    def test_answers_false_for_a_genuinely_disabled_store(self, monkeypatch):
        from pathlib import Path

        from backend.conversation import store_is_configured
        from backend.services.persistence import PersistenceService

        _install_store(monkeypatch, PersistenceService(Path("off.db"), enabled=False))

        assert store_is_configured() is False

    def test_answers_true_for_a_live_store(self, monkeypatch, tmp_path):
        from backend.conversation import store_is_configured
        from backend.services.persistence import PersistenceService

        _install_store(monkeypatch, PersistenceService(tmp_path / "live.db"))

        assert store_is_configured() is True

    def test_the_store_outranks_the_import_time_config_flag(self, monkeypatch, tmp_path):
        """A disabled store is disabled even when the flag says persistence is on.

        The flag describes the instance ``backend.main`` built at import time.
        ``persistence`` is a swappable module global, so the two agree in
        production and disagree the moment the store is replaced -- which is
        exactly when guessing wrong silently eats turns.
        """
        from pathlib import Path

        from backend.config import config
        from backend.conversation import store_is_configured
        from backend.services.persistence import PersistenceService

        monkeypatch.setattr(config, "PERSISTENCE_ENABLED", True, raising=False)
        _install_store(monkeypatch, PersistenceService(Path("off.db"), enabled=False))

        assert store_is_configured() is False, (
            "the config flag is describing a different instance; the store that "
            "is actually installed is the only thing that can answer"
        )

    def test_a_store_that_cannot_answer_counts_as_configured(self, monkeypatch):
        """The deliberate fallback: an unfamiliar store is treated as live.

        Not an accident, and not ``config.PERSISTENCE_ENABLED`` -- the flag is
        forced to ``False`` here, which is precisely the case the old
        ``getattr`` default got wrong. It took the "switched off" branch and
        so contradicted the docstring sitting directly above it.

        The two error directions are not symmetric. Guessing "disabled" for a
        store that really is live makes memory claim a turn that is not on
        disk, and the divergence stays invisible until a restart forgets the
        exchange. Guessing "configured" is the conservative branch -- it only
        costs a turn in the corner case of a store that is *both* unfamiliar
        and genuinely switched off, which no real implementation is.
        """
        from backend.config import config
        from backend.conversation import store_is_configured

        monkeypatch.setattr(config, "PERSISTENCE_ENABLED", False, raising=False)

        class UnfamiliarStore:
            """A store from before the accessor existed. It answers nothing."""

            def record_turn(self, cid, turn, message):
                return None

        _install_store(monkeypatch, UnfamiliarStore())

        assert store_is_configured() is True, (
            "an unfamiliar store must read as live; the config flag describes "
            "the instance built at import time and says nothing about this one"
        )

    def test_a_mock_store_counts_as_configured(self, monkeypatch):
        """A test double answers the accessor, and its answer wins.

        ``MagicMock`` returns a truthy mock for every attribute, so this pins
        that the accessor's value goes through ``bool`` rather than being
        returned raw: the store's own answer is never a bool, and the caller
        should not have to know that. The flag is forced to ``False`` so a
        pass cannot be an accident of the import-time default.
        """
        from unittest.mock import MagicMock

        from backend.config import config
        from backend.conversation import store_is_configured

        monkeypatch.setattr(config, "PERSISTENCE_ENABLED", False, raising=False)
        _install_store(monkeypatch, MagicMock())

        assert store_is_configured() is True

    def test_conversation_never_names_a_private_store_attribute(self):
        """STRUCTURAL, not behavioural: a source assertion, and honestly so.

        A rename of ``_enabled`` cannot be observed from outside the class, so
        there is no behavioural test for "still works after a rename". What
        *is* observable is the coupling itself, and this is the test that keeps
        it out: if ``conversation.py`` names a private attribute of another
        object's, the two are coupled again and the next rename is a silent
        behaviour change. It is a text check on purpose, and the only kind that
        can catch this class of defect.
        """
        from pathlib import Path

        import backend.conversation as conversation_mod

        source = Path(conversation_mod.__file__).read_text(encoding="utf-8")

        assert '"_enabled"' not in source and "'_enabled'" not in source, (
            "conversation.py reaches into PersistenceService._enabled; ask the "
            "public is_enabled() accessor instead so a rename cannot change "
            "this function's meaning"
        )
        assert "is_enabled" in source, (
            "store_is_configured must go through the public accessor; the "
            "config flag only describes the instance built at import time"
        )


# ─── periodic_cleanup eviction + rate-limit pruning ─────

#: Sentinel TTL handed to the sweep. Deliberately different from the ambient
#: ``SESSION_TTL_HOURS`` so that patching it is not a no-op, and so the two
#: conversations below sit on *opposite* sides of it.
SWEEP_TTL_HOURS = 6

#: How far the patched clock is moved, in seconds. An hour dwarfs the 60s
#: window ``periodic_cleanup`` prunes the rate-limit store with, so the
#: rate-limit outcomes depend on the patched clock rather than on how long the
#: test took to run. One test shifts it forwards and one backwards, because a
#: shift that only ever prunes proves nothing about the keep path.
CLOCK_SHIFT_SECONDS = 3600


class SweepRun:
    """One ``periodic_cleanup`` tick, and the record of what it asked for.

    Every field exists to answer "was the patch actually consulted?", which a
    green assertion otherwise cannot tell you. A patch that is renamed but
    stops intercepting is the same defect with a new name: the test still
    passes, and it is passing for the wrong reason.
    """

    def __init__(self, services, sleeps, clock_calls, ttl_seen):
        self.services = services
        self.sleeps = sleeps
        self.clock_calls = clock_calls
        self.ttl_seen = ttl_seen

    def assert_hermetic(self):
        """Every real target the sweep can reach was replaced by a double.

        Replacement, not per-tick reach: a sweep that grew a new call site
        would still be isolated, because the isolation is a property of what
        the container hands out rather than of how many times each double was
        called. The stubs that run on *every* tick are also asserted as
        consulted, which is what proves the tick really executed the code under
        test instead of short-circuiting past it.

        The semantic cache is not in this list because it no longer exists. It
        used to be a fourth per-tick target, and its presence here is what made
        a new call site in ``periodic_cleanup`` visible: the count of stubs and
        the count of real targets have to move together.
        """
        from backend import container

        assert container.report_service() is self.services.report
        assert container.persistence() is self.services.store
        self.services.report.cleanup_expired.assert_called()
        self.services.store.prune_reports.assert_called()
        self.services.store.prune_conversations.assert_called()

    def assert_sleep_was_intercepted(self, interval_seconds):
        """The sweep awaited the patched ``asyncio.sleep``, with these delays.

        The initial delay is a literal in the function and the second one is
        its argument, so a record of both proves the patched module is the one
        under test. Had the patch stopped intercepting, the real sleep would
        have blocked here instead and the test would hang rather than lie --
        but a passing assertion on the recorded delays settles it either way.
        """
        assert self.sleeps == [30, interval_seconds], (
            f"the sweep slept {self.sleeps}, expected the patched sleep to be "
            f"awaited with the initial 30s delay and then the interval"
        )

    def assert_clock_was_intercepted(self):
        assert self.clock_calls, (
            "time.time() was never called through the patched clock, so the "
            "patch is not intercepting anything"
        )

    def assert_ttl_reached_the_store(self):
        assert self.ttl_seen == [SWEEP_TTL_HOURS], (
            f"prune_conversations was asked about {self.ttl_seen}, not the "
            f"patched TTL {SWEEP_TTL_HOURS}: the config patch is not reaching "
            f"the sweep"
        )


@pytest.fixture
def sweep(monkeypatch):
    """Run the real ``periodic_cleanup`` against hermetic targets.

    The function is not stubbed -- these tests are about what it does. What
    *is* stubbed is everything it does it to, because the real targets are the
    production-shaped ones: this sweep reached into the real ``audio/``
    directory and deleted real rows from ``data/interviewtts.db``. Measured,
    not assumed: a seeded 400-day-old conversation was gone after one run of
    these four tests.

    The database is protected by the persistence double and by nothing else.
    Redirecting ``config.DB_PATH`` would be theatre -- ``PersistenceService``
    captured its path when it was built, so patching the config attribute
    afterwards changes nothing that the service reads.
    """
    import asyncio
    import time
    from datetime import datetime
    from unittest.mock import MagicMock

    from backend import container
    from backend.config import config
    from backend.conversation import _rate_limit_store
    from backend.maintenance import periodic_cleanup

    # ── Isolation: the services the sweep reaches ──────────────────
    services = MagicMock()
    services.report.generate.return_value = None  # no report file is written
    services.report.cleanup_expired.return_value = 0
    services.store.prune_reports.return_value = 0

    ttl_seen = []
    services.store.prune_conversations.side_effect = lambda ttl: (
        ttl_seen.append(ttl) or 0
    )

    # Read before the patch: this is the value the sweep would have used, and
    # comparing it to the sentinel is what proves the patch is not a no-op.
    ambient_ttl = config.SESSION_TTL_HOURS
    monkeypatch.setattr(
        "backend.maintenance.config.SESSION_TTL_HOURS",
        SWEEP_TTL_HOURS,
    )

    monkeypatch.setattr(container, "report_service", lambda: services.report)
    monkeypatch.setattr(container, "persistence", lambda: services.store)
    # The sweep calls `container.cleanup_stale_audio()()`, i.e. it resolves a
    # factory and then calls what the factory returned. That is the indirection
    # that keeps the real audio directory out of the test's reach.
    audio_sweep = MagicMock(name="cleanup_stale_audio")
    monkeypatch.setattr(container, "cleanup_stale_audio", lambda: audio_sweep)

    # ── Interception: the three patches, made observable ────────────
    sleeps = []
    clock_calls = []
    clock_offset = {"seconds": 0}

    # Captured here, not looked up at call time: the patch replaces the
    # attribute on the shared ``time`` module, so a fake that called
    # ``time.time()`` would be calling itself.
    real_time = time.time

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            # The interval sleep is the end of the tick; cancelling there is
            # how one pass of the loop is expressed without a real timer.
            raise asyncio.CancelledError()

    def fake_time():
        clock_calls.append(1)
        return real_time() + clock_offset["seconds"]

    monkeypatch.setattr(
        "backend.maintenance.asyncio.sleep", fake_sleep, raising=True
    )

    run = SweepRun(services, sleeps, clock_calls, ttl_seen)

    async def tick(*, interval_seconds=4242, clock_offset_seconds=0):
        """One tick. ``interval_seconds`` and the clock offset are distinctive
        on purpose: the assertions read them back, so a patch that stopped
        intercepting cannot pass by accident."""
        clock_offset["seconds"] = clock_offset_seconds
        with monkeypatch.context() as patcher:
            patcher.setattr("backend.maintenance.time.time", fake_time)
            with pytest.raises(asyncio.CancelledError):
                await periodic_cleanup(interval_seconds=interval_seconds)
        return run

    run.tick = tick
    run.ambient_ttl = ambient_ttl
    # Exposed so the tests can seed the rate-limit store the sweep prunes.
    # The seeds must come from the epoch clock the sweep prunes against, not
    # from `datetime.utcnow().timestamp()`: that one reads a naive UTC value as
    # local time, so it is silently offset by the machine's UTC offset and the
    # 60s window stops meaning anything.
    run.rate_limits = _rate_limit_store
    run.real_time = real_time
    run.now = datetime.utcnow
    run.services_stub = services
    run.audio_sweep = audio_sweep
    return run


@pytest.mark.asyncio
async def test_conversation_eviction(sweep):
    """A conversation older than the *patched* TTL is evicted by the sweep.

    Twenty-four hours is past the patched six-hour TTL, and the eviction
    reaches the store: the report is written for the doomed conversation first
    and the rows are evicted after, which is the ordering the maintenance
    module calls load-bearing and the reason both have to be doubles.
    ``assert_ttl_reached_the_store`` is the direct proof that the patched TTL
    was the one consulted.
    """
    from datetime import timedelta

    from backend.main import conversations

    conv_id = "test-evict-1"
    conversations[conv_id] = {
        "id": conv_id,
        "last_activity_at": (sweep.now() - timedelta(hours=24)).isoformat(),
        "messages": [],
        "turns": [],
        "summary": "",
        "created_at": "",
    }

    assert sweep.ambient_ttl != SWEEP_TTL_HOURS, (
        f"the sentinel TTL ({SWEEP_TTL_HOURS}h) equals the ambient "
        f"SESSION_TTL_HOURS, so patching it is a no-op and this test would "
        f"pass without proving the patch intercepts"
    )

    await sweep.tick()

    assert conv_id not in conversations, (
        f"a conversation 24h old is past a {SWEEP_TTL_HOURS}h TTL and must "
        f"be evicted; it survived"
    )
    # Eviction reaches the store, and the report is written for the doomed
    # conversation first -- the ordering the module docstring calls load-bearing,
    # and the reason both of these have to be doubles.
    assert sweep.services.report.generate.call_args[0][0] == conv_id
    sweep.services.store.evict_conversation.assert_called_once_with(conv_id)
    sweep.assert_sleep_was_intercepted(4242)
    sweep.assert_ttl_reached_the_store()
    sweep.assert_hermetic()


@pytest.mark.asyncio
async def test_recent_conversation_not_evicted(sweep):
    """A conversation inside the *patched* TTL survives the sweep.

    "Recent" means recent with respect to the configured TTL, which is the
    question the sweep actually asks -- and here the two disagree: three hours
    is stale against the ambient default and current against the patched one.
    So this test passes only while the config patch is intercepting, and fails
    by evicting if it ever stops.
    """
    from datetime import timedelta

    from backend.main import conversations

    conv_id = "test-keep-1"
    conversations[conv_id] = {
        "id": conv_id,
        "last_activity_at": (sweep.now() - timedelta(hours=3)).isoformat(),
        "messages": [],
        "turns": [],
        "summary": "",
        "created_at": "",
    }
    assert sweep.ambient_ttl != SWEEP_TTL_HOURS, (
        f"the sentinel TTL ({SWEEP_TTL_HOURS}h) equals the ambient "
        f"SESSION_TTL_HOURS, so patching it is a no-op and this test would "
        f"pass without proving the patch intercepts"
    )

    await sweep.tick()

    assert conv_id in conversations, (
        f"three hours is inside a {SWEEP_TTL_HOURS}h TTL and must survive; "
        f"the sweep evicted it, so the TTL patch is not reaching the code"
    )
    sweep.assert_ttl_reached_the_store()
    sweep.assert_hermetic()


@pytest.mark.asyncio
async def test_rate_limit_pruning(sweep):
    """An entry older than the 60s window is pruned, judged on the patched clock.

    The stored timestamps are seconds old to the *real* clock -- comfortably
    inside the 60s window -- and the patched clock is an hour ahead, so to the
    sweep they are an hour stale and the entry goes. Unpatched, the same data
    is fresh and the entry would survive, which is what makes the clock patch
    load-bearing rather than decorative.
    """
    sweep.rate_limits.clear()
    real_now = sweep.real_time()
    sweep.rate_limits["stale-ip"] = [real_now - 5, real_now - 2]

    await sweep.tick(clock_offset_seconds=CLOCK_SHIFT_SECONDS)

    assert "stale-ip" not in sweep.rate_limits, (
        "an hour-old entry to the patched clock is outside the 60s window and "
        "must be pruned; it survived, so the clock patch is not reaching the "
        "rate-limit pruning"
    )
    sweep.assert_clock_was_intercepted()
    sweep.assert_hermetic()


@pytest.mark.asyncio
async def test_active_rate_limit_entry_not_pruned(sweep):
    """An entry inside the 60s window survives, judged on the patched clock.

    The mirror image of the test above: the patched clock is an hour *behind*
    the stored timestamps, so they are 30s old to the sweep. Against the real
    clock they are an hour old, so with the clock patch broken this entry
    would be pruned and the assertion would fail.
    """
    sweep.rate_limits.clear()
    fake_now = sweep.real_time() - CLOCK_SHIFT_SECONDS
    sweep.rate_limits["active-ip"] = [fake_now - 10, fake_now - 5]

    await sweep.tick(clock_offset_seconds=-CLOCK_SHIFT_SECONDS)

    assert "active-ip" in sweep.rate_limits, (
        "an entry 10s old to the patched clock is inside the 60s window and "
        "must survive; it was pruned, so the clock patch is not reaching the "
        "rate-limit pruning"
    )
    sweep.assert_clock_was_intercepted()
    sweep.assert_hermetic()


def _config_ttl():
    from backend.config import config

    return config.SESSION_TTL_HOURS


@pytest.mark.asyncio
async def test_the_sweep_never_reaches_the_real_filesystem(sweep):
    """The isolation is total, and that is asserted rather than promised.

    The four tests above depend on the sweep being harmless, which is a claim
    about every call site rather than about the four assertions they happen to
    make. This one says it directly: the audio sweep, the report writer, the
    report pruner and the store are all doubles, so the real ``audio/``,
    ``reports/`` and ``data/interviewtts.db`` cannot be reached -- including by
    a call site added after this test was written.
    """
    await sweep.tick()

    sweep.audio_sweep.assert_called()
    # Nothing was stale in this tick, so the report writer and the store's
    # eviction were never asked to do anything -- the four always-run stubs
    # were, which is what shows the tick executed the code under test.
    assert sweep.services_stub.report.generate.call_count == 0
    assert sweep.services_stub.store.evict_conversation.call_count == 0
    sweep.assert_hermetic()

