"""Tests that blocking STT work never runs on the event-loop thread.

Whisper transcription takes seconds. Calling it synchronously from an ``async
def`` route stalls the entire event loop for that long: no other request is
served, no SSE token streams, and the background pruning task cannot run. The
streaming route already offloaded it; this file pins that parity so the
non-streaming route cannot regress.

Note on the assertion: the thread to compare against is the *event-loop*
thread, not ``threading.main_thread()``. ``TestClient`` serves the app on an
anyio blocking portal thread, so even a fully synchronous handler runs off
MainThread and a "not the main thread" assertion would pass even with the bug
present. The loop thread is captured from ``tts_service.synthesize``, which
the route genuinely awaits, so it runs on the loop itself.
"""

import threading
from pathlib import Path

import pytest


class TestSttDoesNotBlockEventLoop:
    """The non-streaming message endpoint must not transcribe inline."""

    @pytest.fixture(autouse=True)
    def clear_rate_limits(self):
        from backend.main import _rate_limit_store

        _rate_limit_store.clear()
        yield
        _rate_limit_store.clear()

    @pytest.fixture
    def threads(self):
        """Record which thread STT and the (awaited) TTS call each ran on."""
        return {"stt": None, "tts": None}

    @pytest.fixture
    def mock_services(self, threads, isolated_write_targets):
        from unittest.mock import patch

        with patch("backend.main.stt_service") as stt, patch(
            "backend.main.llm_service"
        ) as llm, patch("backend.main.tts_service") as tts:
            stt.is_loaded = True

            def transcribe(path):
                threads["stt"] = threading.current_thread()
                return "What technologies did you use?"

            stt.transcribe.side_effect = transcribe
            llm.generate.return_value = "I built InterviewTTS with FastAPI."

            async def mock_synthesize(text, output_path=None):
                # Awaited directly by the route, so this runs on the loop.
                threads["tts"] = threading.current_thread()
                path = output_path or isolated_write_targets.audio / "test.mp3"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                return path

            tts.synthesize = mock_synthesize
            yield {"stt": stt, "llm": llm, "tts": tts}

    @pytest.fixture
    def client(self, mock_services):
        from fastapi.testclient import TestClient

        from backend.main import app

        return TestClient(app, client=("127.0.0.1", 50000))

    def _send_message(self, client):
        cid = client.post("/api/conversation").json()["conversation_id"]
        return client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("a.webm", b"fake audio data", "audio/webm")},
        )

    def test_transcribe_does_not_run_on_the_event_loop_thread(
        self, client, threads
    ):
        """STT must not execute on the event-loop thread.

        This is the assertion that would have caught the bug: a synchronous
        ``stt_service.transcribe(...)`` inside ``async def`` runs on the loop
        thread, making it the *same* thread as the awaited TTS call. After the
        fix ``asyncio.to_thread`` hands it to a worker instead.
        """
        response = self._send_message(client)

        assert response.status_code == 200
        assert threads["stt"] is not None, "transcribe was never called"
        assert threads["tts"] is not None, "tts was never called"
        assert threads["stt"] is not threads["tts"], (
            "transcribe ran on the event-loop thread and would block it"
        )

    def test_transcribe_runs_on_an_executor_worker(self, client, threads):
        """It should land on a thread-pool worker, not merely a different one."""
        self._send_message(client)

        worker = threads["stt"]
        # asyncio.to_thread uses the loop's default ThreadPoolExecutor, whose
        # workers are named asyncio_<n> and are not daemon threads.
        assert worker.name.startswith("asyncio_")
        assert worker is not threads["tts"]
