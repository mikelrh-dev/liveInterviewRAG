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


class TestResponseCache:
    """Common questions are answered from cache, skipping the LLM call."""

    def test_cached_question_skips_llm(self, client, mock_services):
        """A common question returns the cached answer without calling the LLM."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        # Return empty RAG context so the cached answer is not enriched
        mock_services["rag"].get_context_string.return_value = ""

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        assert "InterviewTTS" in data["response_text"]
        mock_services["llm"].generate.assert_not_called()

    def test_uncached_question_still_calls_llm(self, client, mock_services):
        """A non-common question falls back to the LLM as before."""
        # Default STT mock transcribes "What technologies did you use?" (not cached)
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response_text"] == "I built InterviewTTS using Python and FastAPI."
        mock_services["llm"].generate.assert_called_once()


class TestStreamingResponseCache:
    """Cache hits in /message/stream answer instantly without the LLM."""

    def _stream_events(self, client, conversation_id):
        """POST audio to /message/stream and return the parsed SSE events."""
        import json

        with client.stream(
            "POST",
            f"/api/conversation/{conversation_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    def test_stream_cached_question_answers_from_cache(self, client, mock_services):
        """A common question streams the cached answer as one token, then audio, skipping the LLM."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        # Return empty RAG context so the cached answer is not enriched
        mock_services["rag"].get_context_string.return_value = ""

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        events = self._stream_events(client, conv_id)

        token_event = next(e for e in events if e.get("event") == "token")
        audio_url_events = [e for e in events if e.get("event") == "audio_url"]
        done_events = [e for e in events if e.get("event") == "done"]

        # Cached answer streamed as a single token chunk
        assert "InterviewTTS" in token_event["data"]["text"]
        # Single audio file synthesized for the whole cached answer
        assert len(audio_url_events) == 1
        assert audio_url_events[0]["data"]["url"].startswith("/audio/")
        assert len(done_events) == 1
        # Event order: token → audio_url → done
        assert events.index(audio_url_events[0]) > events.index(token_event)
        assert events.index(done_events[0]) > events.index(audio_url_events[0])
        # LLM streaming never invoked
        mock_services["llm"].generate_stream_with_context.assert_not_called()
        # Conversation memory stores the cached exchange
        from backend.main import conversations
        assert conversations[conv_id]["messages"][-1]["response_text"] == token_event["data"]["text"]

    def test_stream_cached_different_question_returns_matching_answer(self, client, mock_services):
        """A different cached question returns its own answer — real lookup, not a hardcoded reply."""
        mock_services["stt"].transcribe.return_value = "¿Cuáles son tus fortalezas?"
        # Return empty RAG context so the cached answer is not enriched
        mock_services["rag"].get_context_string.return_value = ""

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        events = self._stream_events(client, conv_id)

        token_event = next(e for e in events if e.get("event") == "token")

        assert "disciplina" in token_event["data"]["text"].lower()
        assert "InterviewTTS" not in token_event["data"]["text"]
        mock_services["llm"].generate_stream_with_context.assert_not_called()

    def test_stream_uncached_question_still_uses_llm(self, client, mock_services):
        """A non-common question keeps streaming through the LLM with per-sentence audio chunks."""
        from pathlib import Path

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        # Default STT mock transcribes "What technologies did you use?" (not cached)
        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["I built InterviewTTS using Python and FastAPI."]),
            [],
        )

        async def fake_synth(text, sid, output_dir):
            return (sid, Path(f"audio/{conv_id}/sentence_{sid}.mp3"))

        mock_services["tts"].synthesize_sentence = fake_synth

        events = self._stream_events(client, conv_id)

        token_texts = [e["data"]["text"] for e in events if e.get("event") == "token"]
        audio_url_events = [e for e in events if e.get("event") == "audio_url"]
        done_events = [e for e in events if e.get("event") == "done"]

        # LLM streaming path used — tokens come from the LLM mock, not the cache
        mock_services["llm"].generate_stream_with_context.assert_called_once()
        assert "".join(token_texts) == "I built InterviewTTS using Python and FastAPI."
        # The LLM path emits per-sentence audio_chunk events, never audio_url
        assert len(audio_url_events) == 0
        assert len(done_events) == 1


class TestCacheRagEnrichment:
    """Cache hits are enriched with RAG context when available (non-streaming)."""

    def test_cache_hit_with_rag_enrichment(self, client, mock_services):
        """Cached answer is appended with RAG context when RAG returns results."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_context_string.return_value = (
            "[Source: wiki/interviewtts.md | Tipo: project]\n"
            "InterviewTTS es una app de entrevistas por voz con IA."
        )

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        # Cached answer is present
        assert "InterviewTTS" in data["response_text"]
        # RAG context is appended
        assert "wiki/interviewtts.md" in data["response_text"]
        assert "entrevistas por voz" in data["response_text"]
        # LLM was NOT called
        mock_services["llm"].generate.assert_not_called()
        # RAG was called with top_k=2 for enrichment
        mock_services["rag"].get_context_string.assert_called_with(
            "¿Qué es InterviewTTS?", top_k=2
        )

    def test_cache_hit_without_rag_returns_cache_only(self, client, mock_services):
        """Cached answer is returned as-is when RAG finds nothing relevant."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_context_string.return_value = ""

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        # Only the cached answer, no RAG suffix
        assert data["response_text"] == mock_services["rag"].get_context_string.return_value or \
               "InterviewTTS es mi proyecto" in data["response_text"]
        mock_services["llm"].generate.assert_not_called()

    def test_cache_hit_tracks_rag_chunks_in_turn(self, client, mock_services):
        """Cache hit with RAG enrichment stores chunks_used in the conversation turn."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_context_string.return_value = "RAG context here"
        mock_services["rag"].get_chunks_with_scores.return_value = [
            {"text": "RAG context here", "score": 0.85, "source": "wiki/interviewtts.md"}
        ]

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        from backend.main import conversations
        turns = conversations[conversation_id]["turns"]
        assert len(turns) == 1
        assert len(turns[0]["chunks_used"]) > 0
        assert turns[0]["chunks_used"][0]["source"] == "wiki/interviewtts.md"


class TestMp4UploadAcceptance:
    """Tests for mp4/m4a audio upload support (mobile codec)."""

    def test_send_message_mp4_audio(self, client, mock_services):
        """POST with audio/mp4 content type returns 200."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conv_id}/message",
            files={"audio": ("test.m4a", b"fake-audio-data", "audio/mp4")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "user_text" in data
        assert "response_text" in data

    def test_send_message_stream_mp4_audio(self, client, mock_services):
        """POST with audio/mp4 to stream endpoint returns 200."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        with client.stream(
            "POST",
            f"/api/conversation/{conv_id}/message/stream",
            files={"audio": ("test.m4a", b"fake-audio-data", "audio/mp4")},
        ) as response:
            assert response.status_code == 200

    def test_upload_saves_with_correct_extension(self, client, mock_services):
        """Temp file extension matches content_type: .m4a for audio/mp4."""
        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        original_write = Path.write_bytes
        captured_paths = []

        def capture_write(self, data):
            captured_paths.append(str(self))
            return original_write(self, data)

        with patch.object(Path, "write_bytes", capture_write):
            client.post(
                f"/api/conversation/{conv_id}/message",
                files={"audio": ("test.m4a", b"fake-audio-data", "audio/mp4")},
            )

        temp_files = [p for p in captured_paths if "input_" in p]
        assert len(temp_files) == 1
        assert temp_files[0].endswith(".m4a"), f"Expected .m4a, got: {temp_files[0]}"


class TestStreamingCacheRagEnrichment:
    """Cache hits in /message/stream are enriched with RAG context."""

    def _stream_events(self, client, conversation_id):
        """POST audio to /message/stream and return the parsed SSE events."""
        import json

        with client.stream(
            "POST",
            f"/api/conversation/{conversation_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    def test_stream_cache_hit_with_rag_enrichment(self, client, mock_services):
        """Streaming cache hit appends RAG context to the token and audio."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_context_string.return_value = (
            "[Source: wiki/interviewtts.md | Tipo: project]\n"
            "InterviewTTS es una app de entrevistas por voz con IA."
        )
        mock_services["rag"].get_chunks_with_scores.return_value = [
            {"text": "InterviewTTS app", "score": 0.85, "source": "wiki/interviewtts.md"}
        ]

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        events = self._stream_events(client, conv_id)

        token_events = [e for e in events if e.get("event") == "token"]
        audio_url_events = [e for e in events if e.get("event") == "audio_url"]
        done_events = [e for e in events if e.get("event") == "done"]

        # Single token event with enriched text (cached + RAG)
        assert len(token_events) == 1
        token_text = token_events[0]["data"]["text"]
        assert "InterviewTTS es mi proyecto" in token_text
        assert "wiki/interviewtts.md" in token_text
        # Single audio file
        assert len(audio_url_events) == 1
        assert len(done_events) == 1
        # LLM was NOT called
        mock_services["llm"].generate_stream_with_context.assert_not_called()

    def test_stream_cache_hit_without_rag_returns_cache_only(self, client, mock_services):
        """Streaming cache hit returns only cached answer when RAG finds nothing."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_context_string.return_value = ""

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        events = self._stream_events(client, conv_id)

        token_events = [e for e in events if e.get("event") == "token"]
        assert len(token_events) == 1
        token_text = token_events[0]["data"]["text"]
        # Only cached answer, no RAG suffix
        assert "InterviewTTS es mi proyecto" in token_text
        assert "[Source:" not in token_text
        mock_services["llm"].generate_stream_with_context.assert_not_called()
