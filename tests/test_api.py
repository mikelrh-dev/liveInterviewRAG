"""Tests for FastAPI endpoints with mocked services."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from fastapi.testclient import TestClient

# Import rate limit store so we can reset it between tests
from backend.main import _rate_limit_store


@pytest.fixture
def mock_services(isolated_write_targets):
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
            path = output_path or isolated_write_targets.audio / "test.mp3"
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
        assert "transcribir" in response.json()["detail"].lower()
        # The provider's text is server-side only.
        assert "Could not transcribe" not in response.json()["detail"]

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
        assert "respuesta" in response.json()["detail"].lower()
        # The detail used to be the exception prefixed with a fixed phrase, so
        # a provider failure shipped upstream text to the client.
        assert "API unavailable" not in response.json()["detail"]


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
        audio_events = [e for e in events if e.get("event") == "audio_url"]

        assert len(error_events) >= 1, "Expected at least one error event"
        assert len(audio_events) >= 1, "Expected successful audio_url events"
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
        # Both paths emit the canonical audio_url event; they differ by shape.
        # The LLM path is per-sentence, so every event carries a sequential id
        # for the frontend playback cursor.
        assert len(audio_url_events) >= 1
        for event in audio_url_events:
            assert isinstance(event["data"]["id"], int)
            assert event["data"]["url"].startswith("/audio/")
        assert [e["data"]["id"] for e in audio_url_events] == list(
            range(len(audio_url_events))
        ), "per-sentence audio ids must be gapless and ordered"
        assert len(done_events) == 1


class TestCacheRagEnrichment:
    """Cache hits return ONLY the pre-generated answer — RAG chunks are
    tracked for the context panel but never appended to spoken text."""

    def test_cache_hit_returns_cached_answer_without_rag_text(self, client, mock_services):
        """Cached answer is returned verbatim; RAG context is never spoken."""
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
        # Exactly the cached answer, no RAG suffix or source metadata
        assert data["response_text"].startswith("InterviewTTS es mi proyecto de portfolio")
        assert "wiki/interviewtts.md" not in data["response_text"]
        assert "[Source:" not in data["response_text"]
        # LLM was NOT called and raw context string was never requested for speech
        mock_services["llm"].generate.assert_not_called()
        mock_services["rag"].get_context_string.assert_not_called()
        # Chunks still collected (top_k=2) for the context panel
        mock_services["rag"].get_chunks_with_scores.assert_called_with(
            "¿Qué es InterviewTTS?", top_k=2
        )

    def test_cache_hit_without_rag_returns_cache_only(self, client, mock_services):
        """Cached answer is returned as-is when RAG finds nothing relevant."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_chunks_with_scores.return_value = []

        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        data = response.json()
        # Only the cached answer, no RAG suffix
        assert data["response_text"].startswith("InterviewTTS es mi proyecto de portfolio")
        mock_services["llm"].generate.assert_not_called()

    def test_cache_hit_tracks_rag_chunks_in_turn(self, client, mock_services):
        """Cache hit stores chunks_used in the conversation turn for the UI."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
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
    """Streaming cache hits return ONLY the cached answer — RAG chunks are
    tracked for the context panel but never appended to the spoken token."""

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

    def test_stream_cache_hit_returns_cached_answer_without_rag_text(self, client, mock_services):
        """Streaming cache hit speaks only the cached answer; RAG never leaks."""
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

        # Single token event with ONLY the cached answer
        assert len(token_events) == 1
        token_text = token_events[0]["data"]["text"]
        assert token_text.startswith("InterviewTTS es mi proyecto de portfolio")
        assert "wiki/interviewtts.md" not in token_text
        assert "[Source:" not in token_text
        # Single audio file
        assert len(audio_url_events) == 1
        assert len(done_events) == 1
        # LLM was NOT called and raw context string was never requested for speech
        mock_services["llm"].generate_stream_with_context.assert_not_called()
        mock_services["rag"].get_context_string.assert_not_called()

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


class TestPersistenceIntegration:
    """Cap-2 write-through wiring in main.py (spec: Conversation Persistence).

    Uses the shared ``mock_services`` fixture plus a tmp-path-backed
    PersistenceService patched over ``backend.main.persistence``.
    """

    @pytest.fixture
    def persisted_store(self, tmp_path, monkeypatch):
        import backend.main as main_mod
        from backend.services.persistence import PersistenceService

        svc = PersistenceService(tmp_path / "api.db")
        svc.initialize()
        monkeypatch.setattr(main_mod, "persistence", svc)
        main_mod.conversations.clear()
        yield svc

    def test_create_conversation_persists_row(self, client, mock_services, persisted_store):
        """POST /api/conversation writes a matching conversation row immediately."""
        response = client.post("/api/conversation")
        assert response.status_code == 200
        cid = response.json()["conversation_id"]

        loaded = persisted_store.load_conversation(cid)
        assert loaded is not None, "conversation row must exist right after creation"
        assert loaded["id"] == cid

    def test_message_appends_persist_turn_and_message_rows(self, client, mock_services, persisted_store):
        """A completed voice-message turn writes turn AND message rows."""
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"fake audio data", "audio/webm")},
        )
        assert response.status_code == 200

        loaded = persisted_store.load_conversation(conversation_id)
        assert loaded is not None
        assert len(loaded["turns"]) == 1
        assert loaded["turns"][0]["user_text"] == "What technologies did you use?"
        assert (
            loaded["turns"][0]["assistant_text"]
            == "I built InterviewTTS using Python and FastAPI."
        )
        assert len(loaded["messages"]) == 1
        assert loaded["messages"][0]["audio_url"].startswith("/audio/")

    def test_restart_survival_same_id_continues_after_dict_eviction(self, client, mock_services, persisted_store):
        """After memory loss, a persisted cid hydrates and the interview continues."""
        conv_response = client.post("/api/conversation")
        conversation_id = conv_response.json()["conversation_id"]

        first = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio one", "audio/webm")},
        )
        assert first.status_code == 200

        # Simulate a restart: process memory is gone, only the DB remains
        import backend.main as main_mod

        main_mod.conversations.pop(conversation_id)

        second = client.post(
            f"/api/conversation/{conversation_id}/message",
            files={"audio": ("test.webm", b"audio two", "audio/webm")},
        )

        assert second.status_code == 200, "hydration must replace the old bare 404"
        assert (
            second.json()["response_text"]
            == "I built InterviewTTS using Python and FastAPI."
        )

        hydrated = main_mod.conversations[conversation_id]
        assert len(hydrated["turns"]) == 2, "prior turn restored + new turn appended"
        assert hydrated["turns"][0]["user_text"] == "What technologies did you use?"
        assert hydrated["messages"][0]["response_text"] == (
            "I built InterviewTTS using Python and FastAPI."
        )

    def test_db_error_does_not_break_stream_answer(self, client, mock_services, persisted_store, monkeypatch, caplog):
        """SQLite failure during write-through: SSE still delivers the full answer."""
        import json
        import logging
        from pathlib import Path

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["Answer survives database outage."]),
            [],
        )

        async def fake_synth(text, sid, output_dir):
            return (sid, Path(f"audio/{conv_id}/sentence_{sid}.mp3"))

        mock_services["tts"].synthesize_sentence = fake_synth

        def _dead_connect():
            raise RuntimeError("disk dead mid-interview")

        monkeypatch.setattr(persisted_store, "_connect", _dead_connect)
        caplog.set_level(logging.WARNING, logger="backend.services.persistence")

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

        token_texts = "".join(
            e["data"]["text"] for e in events if e.get("event") == "token"
        )
        done_events = [e for e in events if e.get("event") == "done"]

        # Full successful answer reached the client despite the DB being dead
        assert token_texts == "Answer survives database outage."
        assert len(done_events) == 1
        persistence_errors = [
            e for e in events
            if e.get("event") == "error" and "disk" in str(e.get("data", {}))
        ]
        assert persistence_errors == [], "DB failures must never surface to the client"
        # Write-through WAS attempted and swallowed (proves wiring, not absence)
        swallowed = [
            r for r in caplog.records
            if "disk dead mid-interview" in r.getMessage()
        ]
        assert swallowed, "failed write-through must be logged as a warning"


class TestTurnNumberReconciliation:
    """In-memory turn numbers must be the ones the DB actually committed.

    Every call site derives ``n`` from ``len(conversations[cid]["turns"])`` and
    appends the turn to memory BEFORE writing it through. That made ``n`` a
    request rather than a fact: under a genuine collision ``record_turn``
    re-derives it and commits elsewhere, so memory and disk diverged and the
    Context panel for that turn 404s forever. Worse, a failed write left a turn
    in memory that no restart would ever recall.

    ``record_turn`` reports the committed turn; these tests hold every call site
    to using it.
    """

    RACE_TURN = {
        "n": 0,
        "user_text": "Raced question",
        "assistant_text": "Raced answer",
        "chunks_used": [],
    }
    RACE_MESSAGE = {
        "user_text": "Raced question",
        "response_text": "Raced answer",
        "audio_url": "/audio/raced.mp3",
    }

    @pytest.fixture
    def persisted_store(self, tmp_path, monkeypatch):
        import backend.main as main_mod
        from backend.services.persistence import PersistenceService

        svc = PersistenceService(tmp_path / "reconcile.db")
        svc.initialize()
        monkeypatch.setattr(main_mod, "persistence", svc)
        main_mod.conversations.clear()
        yield svc

    def _new_conversation(self, client):
        return client.post("/api/conversation").json()["conversation_id"]

    def _ask(self, client, cid):
        return client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        )

    def _stream(self, client, cid):
        import json

        with client.stream(
            "POST",
            f"/api/conversation/{cid}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            body = "".join(
                f"{line}\n"
                for line in response.iter_lines()
                if line and line.startswith("data: ")
            )
        return [json.loads(line[6:]) for line in body.splitlines() if line.strip()]

    # ── The committed n is the only n memory is allowed to keep ──────────

    def test_memory_adopts_the_n_the_db_committed(
        self, client, mock_services, persisted_store
    ):
        """A turn that collided on n=0 must be remembered as whatever landed.

        Reproduces the live race without concurrency: a turn another request
        already committed is invisible to this process, so memory is empty and
        derives n=0 for a row that is already taken.
        """
        import backend.main as main_mod

        cid = self._new_conversation(client)
        persisted_store.record_turn(cid, dict(self.RACE_TURN), dict(self.RACE_MESSAGE))

        response = self._ask(client, cid)
        assert response.status_code == 200

        disk = persisted_store.load_conversation(cid)
        in_memory = main_mod.conversations[cid]["turns"]

        assert [t["n"] for t in disk["turns"]] == [0, 1], (
            "the collision must push the new turn to the next free n"
        )
        committed_n = next(
            t["n"] for t in disk["turns"] if t["user_text"] == "What technologies did you use?"
        )
        assert in_memory[-1]["n"] == committed_n, (
            f"memory kept n={in_memory[-1]['n']} but the DB committed "
            f"n={committed_n} — the two have diverged"
        )

    def test_collision_does_not_change_the_client_event_sequence(
        self, client, mock_services, persisted_store
    ):
        """The n correction is internal: the client must not be able to tell.

        The candidate already heard the answer, so the response body and event
        sequence are a contract independent of how the DB resolved the number.
        """
        clean_cid = self._new_conversation(client)
        raced_cid = self._new_conversation(client)
        persisted_store.record_turn(
            raced_cid, dict(self.RACE_TURN), dict(self.RACE_MESSAGE)
        )

        raced = self._stream(client, raced_cid)
        clean = self._stream(client, clean_cid)

        assert [e["event"] for e in raced] == [e["event"] for e in clean], (
            "a collision changed the event sequence the client receives"
        )

    # ── A failed write must not leave a phantom turn behind ──────────────

    @pytest.mark.parametrize(
        "path",
        ["message", "stream"],
        ids=["non_streaming", "streaming"],
    )
    def test_failed_write_leaves_no_phantom_turn_in_memory(
        self, client, mock_services, persisted_store, monkeypatch, path
    ):
        """record_turn returning None means nothing reached disk.

        Memory must not keep the turn, the message, or a summary line for it:
        all three are built from the same provisional turn, and a restart would
        forget an exchange the transcript still shows.
        """
        import backend.main as main_mod

        monkeypatch.setattr(persisted_store, "record_turn", lambda *a, **k: None)

        cid = self._new_conversation(client)
        if path == "message":
            assert self._ask(client, cid).status_code == 200
        else:
            self._stream(client, cid)

        state = main_mod.conversations[cid]
        assert state["turns"] == [], "memory kept a turn that was never stored"
        assert state["messages"] == [], "memory kept a message that was never stored"
        assert state["summary"] == "", "the rolling summary counts a turn that does not exist"

    def test_failed_write_still_delivers_the_answer(
        self, client, mock_services, persisted_store, monkeypatch
    ):
        """Losing the turn must not cost the candidate their answer.

        The audio is already synthesized and the client is mid-stream; a write
        failure is a durability problem, not a response problem.
        """
        monkeypatch.setattr(persisted_store, "record_turn", lambda *a, **k: None)

        cid = self._new_conversation(client)
        response = self._ask(client, cid)

        assert response.status_code == 200
        assert (
            response.json()["response_text"]
            == "I built InterviewTTS using Python and FastAPI."
        )

    def test_disabled_store_keeps_the_turn_in_memory(
        self, client, mock_services, persisted_store, monkeypatch
    ):
        """PERSISTENCE_ENABLED=false is not a failure — nothing was ever stored.

        ``record_turn`` reports ``None`` for a disabled store as well as for a
        broken one. Conflating them would silently discard every turn of every
        DB-less deployment, where memory is the only record there is.
        """
        import backend.main as main_mod
        from backend.services.persistence import PersistenceService

        monkeypatch.setattr(
            main_mod, "persistence", PersistenceService(Path("off.db"), enabled=False)
        )

        cid = self._new_conversation(client)
        assert self._ask(client, cid).status_code == 200

        turns = main_mod.conversations[cid]["turns"]
        assert len(turns) == 1, "a disabled store must not eat the turn"
        assert turns[0]["n"] == 0
        assert turns[0]["assistant_text"] == "I built InterviewTTS using Python and FastAPI."


class TestTurnNumberOverTheWire:
    """The server is the authority on turn numbers, and must say so.

    The frontend used to derive the turn by counting transcript elements, so
    from the second interview onward it asked the Context panel for a turn
    that does not exist and got a silent 404. The ``done`` payload now carries
    the number the DB committed, plus whether that turn has context at all, so
    the client never has to infer either.
    """

    @pytest.fixture
    def persisted_store(self, tmp_path, monkeypatch):
        import backend.main as main_mod
        from backend.services.persistence import PersistenceService

        svc = PersistenceService(tmp_path / "wire.db")
        svc.initialize()
        monkeypatch.setattr(main_mod, "persistence", svc)
        main_mod.conversations.clear()
        yield svc

    def _stream(self, client, cid):
        import json

        with client.stream(
            "POST",
            f"/api/conversation/{cid}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            body = "".join(
                f"{line}\n"
                for line in response.iter_lines()
                if line and line.startswith("data: ")
            )
        return [json.loads(line[6:]) for line in body.splitlines() if line.strip()]

    def _done(self, events):
        done = [e for e in events if e["event"] == "done"]
        assert len(done) == 1, f"expected one done, got {[e['event'] for e in events]}"
        return done[0]["data"]

    def test_done_reports_the_committed_turn_number(
        self, client, mock_services, persisted_store
    ):
        client.post("/api/conversation")
        cid = client.post("/api/conversation").json()["conversation_id"]
        mock_services["rag"].get_chunks_with_scores.return_value = []

        assert self._done(self._stream(client, cid))["n"] == 0

    def test_done_reports_the_number_the_db_corrected(
        self, client, mock_services, persisted_store
    ):
        """The number the client must use is the committed one, not the asked one.

        Reporting the provisional number here would reproduce the original bug
        on the wire: a correct-looking turn that the Context panel cannot
        resolve.
        """
        cid = client.post("/api/conversation").json()["conversation_id"]
        persisted_store.record_turn(
            cid,
            {
                "n": 0,
                "user_text": "Raced question",
                "assistant_text": "Raced answer",
                "chunks_used": [],
            },
            {
                "user_text": "Raced question",
                "response_text": "Raced answer",
                "audio_url": "/audio/raced.mp3",
            },
        )
        mock_services["rag"].get_chunks_with_scores.return_value = []

        assert self._done(self._stream(client, cid))["n"] == 1

    def test_done_reports_context_only_when_the_turn_has_some(
        self, client, mock_services, persisted_store
    ):
        """No context means no request, not a request that 404s."""
        cid = client.post("/api/conversation").json()["conversation_id"]
        mock_services["rag"].get_chunks_with_scores.return_value = []

        assert self._done(self._stream(client, cid))["has_context"] is False

        mock_services["rag"].get_chunks_with_scores.return_value = [
            {"text": "chunk", "source": "cv.md", "score": 0.9}
        ]
        other = client.post("/api/conversation").json()["conversation_id"]

        assert self._done(self._stream(client, other))["has_context"] is True

    def test_done_carries_no_turn_when_none_was_stored(
        self, client, mock_services, persisted_store, monkeypatch
    ):
        """A turn that failed to write is a turn the client must not ask about."""
        monkeypatch.setattr(persisted_store, "record_turn", lambda *a, **k: None)
        cid = client.post("/api/conversation").json()["conversation_id"]
        mock_services["rag"].get_chunks_with_scores.return_value = []

        payload = self._done(self._stream(client, cid))
        assert "n" not in payload, (
            "a turn that never reached disk must not be announced to the client"
        )

    def test_done_without_a_turn_carries_no_turn_number(
        self, client, mock_services, persisted_store
    ):
        """The empty-transcription path commits nothing, so it announces nothing."""
        mock_services["stt"].transcribe.return_value = "   "
        cid = client.post("/api/conversation").json()["conversation_id"]

        assert "n" not in self._done(self._stream(client, cid))

    def test_cached_answer_done_also_reports_the_turn(
        self, client, mock_services, persisted_store
    ):
        """The cached-answer path is a committed turn like any other."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"
        mock_services["rag"].get_chunks_with_scores.return_value = []
        cid = client.post("/api/conversation").json()["conversation_id"]

        assert self._done(self._stream(client, cid))["n"] == 0


class TestSemanticCacheIntegration:
    """Cap-3 semantic answer cache slotted between FAQ cache and RAG/LLM.

    Uses ``mock_services`` plus ``patch("backend.main.semantic_cache")`` so
    lookup/store are observable without loading any embedding model.
    """

    @pytest.fixture
    def semantic_cache_mock(self):
        with patch("backend.main.semantic_cache") as mock_cache:
            mock_cache.lookup.return_value = None
            yield mock_cache

    def test_first_turn_cached_second_similar_turn_bypasses_to_llm(
        self, client, mock_services, semantic_cache_mock
    ):
        """Lookup runs on turn 1 only; follow-up turns never consult the cache."""
        semantic_cache_mock.lookup.side_effect = [
            "Semantic cached answer.",
            None,
        ]

        conv = client.post("/api/conversation")
        cid = conv.json()["conversation_id"]

        first = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("t.webm", b"audio one", "audio/webm")},
        )
        assert first.status_code == 200
        assert first.json()["response_text"] == "Semantic cached answer."
        mock_services["llm"].generate.assert_not_called()

        second = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("t.webm", b"audio two", "audio/webm")},
        )
        assert second.status_code == 200
        assert (
            second.json()["response_text"]
            == "I built InterviewTTS using Python and FastAPI."
        )

        # Exactly ONE lookup total: the second (non-first-substantive) turn
        # bypasses the cache entirely — it never even asks
        assert semantic_cache_mock.lookup.call_count == 1
        mock_services["llm"].generate.assert_called_once()

    def test_stream_hit_emits_single_verbatim_token_without_llm(
        self, client, mock_services, semantic_cache_mock
    ):
        """Stream semantic hit mirrors the FAQ-hit contract: 1 token, no LLM."""
        import json

        semantic_cache_mock.lookup.return_value = "Respuesta semántica precisa."
        mock_services["rag"].get_chunks_with_scores.return_value = []

        conv = client.post("/api/conversation")
        conv_id = conv.json()["conversation_id"]

        with client.stream(
            "POST",
            f"/api/conversation/{conv_id}/message/stream",
            files={"audio": ("t.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        token_events = [e for e in events if e.get("event") == "token"]
        audio_url_events = [e for e in events if e.get("event") == "audio_url"]
        done_events = [e for e in events if e.get("event") == "done"]

        assert len(token_events) == 1, "hit streams as a single verbatim token"
        assert token_events[0]["data"]["text"] == "Respuesta semántica precisa."
        assert len(audio_url_events) == 1
        assert len(done_events) == 1
        mock_services["llm"].generate_stream_with_context.assert_not_called()
        # Slotted BEFORE RAG: no context string was ever requested
        mock_services["rag"].get_context_string.assert_not_called()

    def test_nonstream_hit_tracks_chunks_for_context_panel(
        self, client, mock_services, semantic_cache_mock
    ):
        """Non-stream hit returns verbatim answer + chunks_used tracked, no LLM."""
        chunks = [
            {"text": "Relevant cv context", "score": 0.88, "source": "cv.md"}
        ]
        semantic_cache_mock.lookup.return_value = "Verbatim semantic answer."
        mock_services["rag"].get_chunks_with_scores.return_value = chunks

        conv = client.post("/api/conversation")
        cid = conv.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("t.webm", b"audio data", "audio/webm")},
        )

        assert response.status_code == 200
        assert response.json()["response_text"] == "Verbatim semantic answer."
        mock_services["llm"].generate.assert_not_called()
        # Same tracking pattern as FAQ hits (top_k=2), never spoken
        mock_services["rag"].get_chunks_with_scores.assert_called_once_with(
            "What technologies did you use?", top_k=2
        )

        import backend.main as main_mod

        turns = main_mod.conversations[cid]["turns"]
        assert len(turns) == 1
        assert turns[0]["chunks_used"] == chunks

    def test_miss_stores_after_successful_generation(
        self, client, mock_services, semantic_cache_mock
    ):
        """A cache miss on a first turn stores the freshly generated answer."""
        semantic_cache_mock.lookup.return_value = None

        conv = client.post("/api/conversation")
        cid = conv.json()["conversation_id"]

        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("t.webm", b"audio data", "audio/webm")},
        )
        assert response.status_code == 200
        answer = "I built InterviewTTS using Python and FastAPI."
        assert response.json()["response_text"] == answer

        semantic_cache_mock.store.assert_called_once_with(
            "What technologies did you use?", answer
        )

        # The streaming endpoint stores too: fresh conversation, LLM path
        mock_services["llm"].generate_stream_with_context.return_value = (
            iter(["Streamed answer."]),
            [],
        )

        from pathlib import Path

        async def fake_synth(text, sid, output_dir):
            return (sid, Path(f"audio/x/sentence_{sid}.mp3"))

        mock_services["tts"].synthesize_sentence = fake_synth

        conv2 = client.post("/api/conversation")
        cid2 = conv2.json()["conversation_id"]

        with client.stream(
            "POST",
            f"/api/conversation/{cid2}/message/stream",
            files={"audio": ("t.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            for _ in response.iter_lines():
                pass

        semantic_cache_mock.store.assert_any_call(
            "What technologies did you use?", "Streamed answer."
        )
