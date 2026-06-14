"""Tests for FastAPI endpoints with mocked services."""

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
        async def mock_synthesize(text, output_path=None):
            path = output_path or Path("audio/test.mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path
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

        async def fake_synth(text, sid, output_dir):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("TTS failed")
            from pathlib import Path
            return (sid, Path(f"audio/{conv_id}/sentence_{sid}.mp3"))

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

        async def failing_synth(text, sid, output_dir):
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
