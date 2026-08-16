"""Tests for FastAPI endpoints with mocked services."""

import shutil
from datetime import datetime, timedelta

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from fastapi.testclient import TestClient

# Import rate limit store so we can reset it between tests
from backend.main import _rate_limit_store


@pytest.fixture
def mock_services():
    """Mock all external services for API tests."""
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        # STT mock
        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = "What technologies did you use?"

        # RAG mock
        mock_rag.get_context_string.return_value = "Built InterviewTTS with Python and FastAPI."
        mock_rag.chunks = [MagicMock()]  # Non-empty

        # LLM mock
        mock_llm.generate.return_value = "I built InterviewTTS using Python and FastAPI."

        # TTS mock
        async def mock_synthesize(text, output_path=None, conversation_id=None):
            path = output_path or Path("audio/test.mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path, "microsoft"
        mock_tts.synthesize = mock_synthesize

        # Profile mock
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {"cv.md": "content"}

        yield {
            "stt": mock_stt,
            "llm": mock_llm,
            "tts": mock_tts,
            "rag": mock_rag,
            "profile": mock_profile,
        }


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit store before each test."""
    _rate_limit_store.clear()


@pytest.fixture
def client(mock_services):
    """Create a test client with mocked services."""
    from backend.main import app
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /api/health"""

    def test_health_returns_ok(self, client):
        """Health endpoint returns status ok."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "whisper_loaded" in data
        assert "rag_chunks" in data


class TestConversationEndpoint:
    """Tests for POST /api/conversation"""

    def test_create_conversation(self, client):
        """Creating a conversation returns ID and welcome message."""
        response = client.post("/api/conversation")
        assert response.status_code == 200
        data = response.json()
        assert "conversation_id" in data
        assert "welcome_message" in data
        assert len(data["conversation_id"]) > 0


class TestMessageEndpoint:
    """Tests for POST /api/conversation/{id}/message"""

    def test_send_message_full_pipeline(self, client, mock_services):
        """Full pipeline processes audio and returns response."""
        # First create a conversation
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        # Create a fake audio file
        audio_content = b"fake audio data"
        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", audio_content, "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        assert "user_text" in data
        assert "response_text" in data
        assert "audio_url" in data
        assert data["user_text"] == "What technologies did you use?"
        assert data["response_text"] == "I built InterviewTTS using Python and FastAPI."
        assert data["audio_url"].startswith("/audio/")

    def test_send_message_invalid_conversation(self, client):
        """Message to non-existent conversation returns 404."""
        audio_content = b"fake audio data"
        response = client.post(
            "/api/conversation/nonexistent/message",
            files={"audio": ("test.webm", audio_content, "audio/webm")},
        )
        assert response.status_code == 404

    def test_send_message_invalid_audio_type(self, client, mock_services):
        """Non-audio content type returns 422."""
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.txt", b"not audio", "text/plain")},
        )
        assert response.status_code == 422

    def test_send_message_empty_audio(self, client, mock_services):
        """Empty audio file returns 422."""
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("empty.webm", b"", "audio/webm")},
        )
        assert response.status_code == 422

    def test_send_message_stt_failure(self, client, mock_services):
        """STT failure returns 422."""
        mock_services["stt"].transcribe.side_effect = RuntimeError("Could not transcribe")

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )
        assert response.status_code == 422
        assert "transcribe" in response.json()["detail"].lower()

    def test_send_message_llm_failure(self, client, mock_services):
        """LLM failure returns 503."""
        mock_services["llm"].generate.side_effect = RuntimeError("API unavailable")

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()


class TestContextEndpoint:
    """Tests for GET /api/conversation/{id}/context?turn=N"""

    def test_context_returns_chunks_for_turn(self, client, mock_services):
        """Context endpoint returns chunks_used for a specific turn."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        client.post(
            f"/api/conversation/{conv_id}/message",
            files={"audio": ("test.webm", b"fake audio", "audio/webm")},
        )

        from backend.main import conversations
        if conv_id in conversations:
            conversations[conv_id]["turns"] = [
                {
                    "n": 0,
                    "user_text": "What technologies did you use?",
                    "assistant_text": "I built InterviewTTS using Python and FastAPI.",
                    "chunks_used": [
                        {"text": "Built with Python", "score": 0.85, "source": "cv.md"}
                    ],
                }
            ]

        response = client.get(f"/api/conversation/{conv_id}/context?turn=0")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["source"] == "cv.md"

    def test_context_404_missing_conversation(self, client):
        """Context endpoint returns 404 for non-existent conversation."""
        response = client.get("/api/conversation/nonexistent/context?turn=0")
        assert response.status_code == 404

    def test_context_404_missing_turn(self, client, mock_services):
        """Context endpoint returns 404 for valid conversation but missing turn."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        response = client.get(f"/api/conversation/{conv_id}/context?turn=0")
        assert response.status_code == 404

    def test_context_empty_chunks(self, client, mock_services):
        """Context endpoint returns empty list when turn has no chunks."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        from backend.main import conversations
        conversations[conv_id]["turns"] = [
            {"n": 0, "user_text": "Hi", "assistant_text": "Hello", "chunks_used": []}
        ]

        response = client.get(f"/api/conversation/{conv_id}/context?turn=0")
        assert response.status_code == 200
        assert response.json() == []


class TestStreamingTTSErrors:
    """Tests for TTS error resilience in /message/stream."""

    def test_tts_synthesis_error_emits_sse_and_continues(self, client, mock_services):
        """synthesize_sentence failure emits error SSE and stream continues to done."""
        import json

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        # Mock LLM to produce sentences
        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["Hello world.", "How are you?"]),
            [],
        )

        # Mock TTS synthesize_sentence to fail on first call, succeed on second
        call_count = [0]

        async def fake_synth(text, sid, output_dir, conversation_id=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("TTS failed")
            from pathlib import Path
            return (sid, Path(f"audio/{conv_id}/sentence_{sid}.mp3"), "microsoft")

        mock_services["tts"].synthesize_sentence = fake_synth

        with client.stream(
            "POST",
            f"/api/conversation/{conv_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        error_events = [e for e in events if e.get("event") == "error"]
        done_events = [e for e in events if e.get("event") == "done"]
        audio_chunk_events = [e for e in events if e.get("event") == "audio_chunk"]

        assert len(error_events) >= 1, "Expected at least one error event"
        assert len(audio_chunk_events) >= 1, "Expected successful audio_chunk events"
        assert len(done_events) >= 1, "Expected done event (stream should continue)"
        assert "detail" in error_events[0].get("data", {})

    def test_tts_result_exception_emits_error_and_continues(self, client, mock_services):
        """done.result() exception emits error SSE and event loop continues."""
        import json

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["Hello world.", "How are you?"]),
            [],
        )

        async def failing_synth(text, sid, output_dir, conversation_id=None):
            raise RuntimeError("simulated TTS failure in task")

        mock_services["tts"].synthesize_sentence = failing_synth

        with client.stream(
            "POST",
            f"/api/conversation/{conv_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        error_events = [e for e in events if e.get("event") == "error"]
        done_events = [e for e in events if e.get("event") == "done"]

        assert len(error_events) >= 1, "Expected at least one error event"
        assert len(done_events) >= 1, "Expected done event despite errors"


class TestTTSProviderFlag:
    """Tests for provider flags in responses (spec: Provider Flag in Responses)."""

    def test_message_json_includes_tts_provider(self, client, mock_services):
        """Non-streaming POST /message returns tts_provider in JSON body."""
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tts_provider"] == "microsoft"

    def test_stream_audio_chunk_includes_provider(self, client, mock_services):
        """Streaming audio_chunk events carry the provider that produced the audio."""
        import json

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["Hello world.", "How are you?"]),
            [],
        )

        async def fake_synth(text, sid, output_dir, conversation_id=None):
            return (sid, Path(f"audio/{conv_id}/sentence_{sid}.mp3"), "microsoft")

        mock_services["tts"].synthesize_sentence = fake_synth

        with client.stream(
            "POST",
            f"/api/conversation/{conv_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        audio_chunk_events = [e for e in events if e.get("event") == "audio_chunk"]
        assert len(audio_chunk_events) >= 1, "Expected at least one audio_chunk event"
        for event in audio_chunk_events:
            assert event["data"]["provider"] == "microsoft"


class TestTTSFallbackIntegration:
    """Integration: transparent EL->MS fallback and both-fail 503 at the API layer.

    Uses a real TTSService with mocked clients (patch clients, not the service),
    so the orchestrator's fallback/pinning logic runs end-to-end through main.py.
    """

    @staticmethod
    def _make_service(**kwargs):
        from backend.services.tts import TTSService

        defaults = dict(
            primary_provider="elevenlabs",
            elevenlabs_api_key="test-key",
            elevenlabs_voice_id="test-voice",
            output_dir="test_audio_api",
        )
        defaults.update(kwargs)
        return TTSService(**defaults)

    @staticmethod
    def _cleanup(svc):
        """Remove the output dir created by the service constructor."""
        if svc.output_dir.exists():
            shutil.rmtree(svc.output_dir, ignore_errors=True)

    @staticmethod
    def _fail_elevenlabs(svc, error=None):
        """Make the service's ElevenLabs client raise on every synthesize call."""
        from backend.services.elevenlabs_client import ElevenLabsError

        svc._elevenlabs = AsyncMock()
        svc._elevenlabs.synthesize = AsyncMock(
            side_effect=error or ElevenLabsError("EL down")
        )

    @staticmethod
    def _succeed_edge(svc):
        """Make the service's Microsoft edge client create the audio file."""
        async def fake_edge(text, output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).touch()
            return output_path

        svc._edge = AsyncMock()
        svc._edge.synthesize = AsyncMock(side_effect=fake_edge)

    @staticmethod
    def _fail_edge(svc, error=None):
        """Make the service's Microsoft edge client raise on synthesize."""
        svc._edge = AsyncMock()
        svc._edge.synthesize = AsyncMock(
            side_effect=error or RuntimeError("Could not synthesize")
        )

    def test_elevenlabs_failure_falls_back_to_microsoft(self, client, mock_services):
        """EL failure is transparent: 200 with provider=microsoft."""
        svc = self._make_service()
        self._fail_elevenlabs(svc)
        self._succeed_edge(svc)

        with patch("backend.main.tts_service", svc):
            conv_response = client.post("/api/conversation")
            conversation_id = conv_response.json()["conversation_id"]

            response = client.post(
                f"/api/conversation/{conversation_id}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["tts_provider"] == "microsoft"
        assert data["audio_url"].startswith("/audio/")

        self._cleanup(svc)

    def test_elevenlabs_failure_pins_conversation(self, client, mock_services):
        """After EL fails, a second message in the same conversation skips EL."""
        svc = self._make_service()
        self._fail_elevenlabs(svc)
        self._succeed_edge(svc)

        with patch("backend.main.tts_service", svc):
            conv_response = client.post("/api/conversation")
            conversation_id = conv_response.json()["conversation_id"]

            first = client.post(
                f"/api/conversation/{conversation_id}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )
            second = client.post(
                f"/api/conversation/{conversation_id}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["tts_provider"] == "microsoft"
        # Pinned: EL must not be retried on the second turn of the same conversation
        assert svc._elevenlabs.synthesize.await_count == 1

        self._cleanup(svc)

    def test_conversation_isolation(self, client, mock_services):
        """A failed conversation does not pin others: a fresh conversation retries EL."""
        from backend.services.elevenlabs_client import ElevenLabsError

        svc = self._make_service()
        # First call fails (conv A), second call succeeds (conv B).
        # The orchestrator ignores EL's return value, so a plain Path suffices.
        svc._elevenlabs = AsyncMock()
        svc._elevenlabs.synthesize = AsyncMock(
            side_effect=[ElevenLabsError("EL down"), Path("ignored-b")]
        )
        self._succeed_edge(svc)

        with patch("backend.main.tts_service", svc):
            conv_a = client.post("/api/conversation").json()["conversation_id"]
            conv_b = client.post("/api/conversation").json()["conversation_id"]

            resp_a = client.post(
                f"/api/conversation/{conv_a}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )
            resp_b = client.post(
                f"/api/conversation/{conv_b}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )

        assert resp_a.status_code == 200
        assert resp_a.json()["tts_provider"] == "microsoft"
        assert resp_b.status_code == 200
        assert resp_b.json()["tts_provider"] == "elevenlabs"
        assert svc._elevenlabs.synthesize.await_count == 2  # conv B retried EL

        self._cleanup(svc)

    def test_both_providers_fail_returns_503(self, client, mock_services):
        """EL and MS both failing returns HTTP 503 with 'TTS synthesis failed'."""
        svc = self._make_service()
        self._fail_elevenlabs(svc)
        self._fail_edge(svc)

        with patch("backend.main.tts_service", svc):
            conv_response = client.post("/api/conversation")
            conversation_id = conv_response.json()["conversation_id"]

            response = client.post(
                f"/api/conversation/{conversation_id}/message",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )

        assert response.status_code == 503
        assert "TTS synthesis failed" in response.json()["detail"]

        self._cleanup(svc)


class TestConversationEviction:
    """Tests for TTS pinning eviction in periodic_cleanup."""

    def test_eviction_forgets_conversation_pinning(self, client, mock_services):
        """Evicting a stale conversation also forgets its TTS provider pinning."""
        from backend.main import conversations, evict_stale_conversations

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        # Simulate a stale conversation: last activity 5h ago, cutoff 1h ago
        conversations[conversation_id]["last_activity_at"] = (
            datetime.utcnow() - timedelta(hours=5)
        ).isoformat()
        cutoff = datetime.utcnow() - timedelta(hours=1)

        evicted = evict_stale_conversations(cutoff)

        assert conversation_id in evicted
        assert conversation_id not in conversations
        mock_services["tts"].forget_conversation.assert_called_once_with(conversation_id)

    def test_eviction_keeps_fresh_conversations(self, client, mock_services):
        """Conversations active after the cutoff are not evicted nor forgotten."""
        from backend.main import conversations, evict_stale_conversations

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        # Fresh: last activity now, cutoff 1h ago
        conversations[conversation_id]["last_activity_at"] = datetime.utcnow().isoformat()
        cutoff = datetime.utcnow() - timedelta(hours=1)

        evicted = evict_stale_conversations(cutoff)

        assert conversation_id not in evicted
        assert conversation_id in conversations
        mock_services["tts"].forget_conversation.assert_not_called()
