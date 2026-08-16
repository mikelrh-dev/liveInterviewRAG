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

        async def mock_synth(text, output_path=None, conversation_id=None):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.touch()
            return output_path, "microsoft"
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


# ─── periodic_cleanup eviction + rate-limit pruning ─────


@pytest.mark.asyncio
async def test_conversation_eviction():
    """Conversation with stale last_activity_at is evicted by periodic_cleanup."""
    import asyncio
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from backend.main import periodic_cleanup

    conv_id = "test-evict-1"
    conversations[conv_id] = {
        "id": conv_id,
        "last_activity_at": (datetime.utcnow() - timedelta(hours=3)).isoformat(),
        "messages": [],
        "turns": [],
        "summary": "",
        "created_at": "",
    }

    sleep_count = [0]

    async def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:  # Let initial 30s pass, cancel at interval sleep
            raise asyncio.CancelledError()

    with patch("backend.main.asyncio.sleep", mock_sleep), \
         patch("backend.main.config.SESSION_TTL_HOURS", 2):
        try:
            await periodic_cleanup(interval_seconds=999)
        except asyncio.CancelledError:
            pass

    assert conv_id not in conversations


@pytest.mark.asyncio
async def test_recent_conversation_not_evicted():
    """Active conversation with recent last_activity_at is NOT evicted."""
    import asyncio
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from backend.main import periodic_cleanup

    conv_id = "test-keep-1"
    conversations[conv_id] = {
        "id": conv_id,
        "last_activity_at": datetime.utcnow().isoformat(),
        "messages": [],
        "turns": [],
        "summary": "",
        "created_at": "",
    }

    sleep_count = [0]

    async def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    with patch("backend.main.asyncio.sleep", mock_sleep), \
         patch("backend.main.config.SESSION_TTL_HOURS", 2):
        try:
            await periodic_cleanup(interval_seconds=999)
        except asyncio.CancelledError:
            pass

    assert conv_id in conversations


@pytest.mark.asyncio
async def test_rate_limit_pruning():
    """Rate-limit entry with all stale timestamps is pruned."""
    import asyncio
    import time
    from unittest.mock import patch
    from backend.main import periodic_cleanup, _rate_limit_store

    _rate_limit_store.clear()

    # Insert IP with timestamps all outside the 60s window
    now = time.time()
    _rate_limit_store["stale-ip"] = [now - 120, now - 90]

    sleep_count = [0]

    async def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:  # Let initial 30s pass, cancel at interval sleep
            raise asyncio.CancelledError()

    with patch("backend.main.asyncio.sleep", mock_sleep), \
         patch("backend.main.time.time", return_value=now):
        try:
            await periodic_cleanup(interval_seconds=999)
        except asyncio.CancelledError:
            pass

    assert "stale-ip" not in _rate_limit_store


@pytest.mark.asyncio
async def test_active_rate_limit_entry_not_pruned():
    """IP with recent timestamps is NOT pruned."""
    import asyncio
    import time
    from unittest.mock import patch
    from backend.main import periodic_cleanup, _rate_limit_store

    _rate_limit_store.clear()

    now = time.time()
    _rate_limit_store["active-ip"] = [now - 10, now - 5]  # Within 60s window

    sleep_count = [0]

    async def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    with patch("backend.main.asyncio.sleep", mock_sleep), \
         patch("backend.main.time.time", return_value=now):
        try:
            await periodic_cleanup(interval_seconds=999)
        except asyncio.CancelledError:
            pass

    assert "active-ip" in _rate_limit_store
