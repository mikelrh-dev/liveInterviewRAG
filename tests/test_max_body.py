"""Tests for pre-parse request body size limiting.

The in-route check used to run only AFTER ``await audio.read()``, so the whole
body was already resident in RAM before it could be rejected — a cheap way to
exhaust memory. These tests pin a Content-Length guard that rejects oversized
uploads before the body is parsed, while keeping the in-route check as defence
in depth for chunked uploads that carry no Content-Length.
"""

import pytest
from fastapi import HTTPException
from pathlib import Path

from backend.main import MAX_AUDIO_SIZE, MaxBodySizeMiddleware

# Small enough to keep the unit tests fast; production uses MAX_AUDIO_SIZE.
TEST_LIMIT = 1000


async def _downstream(scope, receive, send):
    """Minimal downstream ASGI app that records that it was reached."""
    scope["app"]["reached"] = True
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})


def make_scope(headers, app_state):
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/conversation/abc/message",
        "raw_path": b"/api/conversation/abc/message",
        "query_string": b"",
        "headers": headers,
        "app": app_state,
        "client": ("127.0.0.1", 50000),
    }


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def call_middleware(headers, limit=TEST_LIMIT):
    """Drive MaxBodySizeMiddleware directly and return (status, app_state)."""
    app_state = {"reached": False}
    middleware = MaxBodySizeMiddleware(_downstream, max_size=limit)
    captured: dict = {}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        captured["body"] = b""

    await middleware(make_scope(headers, app_state), receive, send)
    return captured["status"], app_state


class TestMaxBodySizeMiddleware:
    """Unit tests for the pre-parse Content-Length guard."""

    @pytest.mark.asyncio
    async def test_oversized_content_length_returns_413(self):
        """Content-Length over the limit is rejected with 413."""
        status, app_state = await call_middleware(
            [(b"content-length", str(TEST_LIMIT + 1).encode())]
        )
        assert status == 413

    @pytest.mark.asyncio
    async def test_oversized_content_length_never_reaches_handler(self):
        """The route handler must never be invoked for an oversized body."""
        _, app_state = await call_middleware(
            [(b"content-length", str(TEST_LIMIT + 1).encode())]
        )
        assert app_state["reached"] is False

    @pytest.mark.asyncio
    async def test_undersized_content_length_passes_through(self):
        """A normal request is forwarded untouched."""
        status, app_state = await call_middleware(
            [(b"content-length", str(TEST_LIMIT).encode())]
        )
        assert status == 200
        assert app_state["reached"] is True

    @pytest.mark.asyncio
    async def test_exactly_at_limit_passes_through(self):
        """The limit is inclusive: only strictly larger bodies are rejected."""
        status, app_state = await call_middleware(
            [(b"content-length", str(TEST_LIMIT).encode())]
        )
        assert status == 200
        assert app_state["reached"] is True

    @pytest.mark.asyncio
    async def test_absent_content_length_passes_through(self):
        """Chunked uploads have no Content-Length and must not be broken."""
        status, app_state = await call_middleware([])
        assert status == 200
        assert app_state["reached"] is True

    @pytest.mark.asyncio
    async def test_unparseable_content_length_passes_through(self):
        """A malformed Content-Length is allowed, not crashed on."""
        status, app_state = await call_middleware([(b"content-length", b"banana")])
        assert status == 200
        assert app_state["reached"] is True

    @pytest.mark.asyncio
    async def test_negative_content_length_passes_through(self):
        """A nonsensical negative length is not treated as oversized."""
        status, app_state = await call_middleware([(b"content-length", b"-1")])
        assert status == 200
        assert app_state["reached"] is True


class TestMaxAudioSizeConstant:
    """MAX_AUDIO_SIZE must be a single module-level source of truth."""

    def test_constant_is_module_level_int(self):
        assert isinstance(MAX_AUDIO_SIZE, int)
        assert MAX_AUDIO_SIZE == 5 * 1024 * 1024

    def test_both_routes_reuse_the_module_constant(self):
        """No consumer may redeclare its own local limit.

        Scans the whole package, not just ``main``: after the module split the
        declaration moved to ``backend.uploads`` and the routes live in
        ``backend.routers.turns`` / ``backend.turns``, so scanning one module
        would pass for any content whatsoever.
        """
        import pathlib
        import re

        import backend

        package = pathlib.Path(backend.__file__).parent
        # Horizontal whitespace only: \s would also match the newline before the
        # declaration and make the module-level line look indented.
        pattern = re.compile(r"^[ \t]+MAX_AUDIO_SIZE[ \t]*=", re.MULTILINE)
        offenders = [
            path.relative_to(package).as_posix()
            for path in package.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"these modules declare a local MAX_AUDIO_SIZE, shadowing the "
            f"single source of truth in backend/uploads.py: {offenders}"
        )

    def test_the_single_declaration_is_in_uploads(self):
        """Pin where the constant actually lives, so the scan stays meaningful."""
        import inspect
        import re

        from backend import uploads

        source = inspect.getsource(uploads)
        assert re.search(r"^MAX_AUDIO_SIZE[ \t]*=", source, re.MULTILINE), (
            "the scan above is only meaningful if uploads.py is the sole owner"
        )


class TestMaxBodySizeEndToEnd:
    """End-to-end tests through the real app and the real middleware."""

    @pytest.fixture(autouse=True)
    def clear_rate_limits(self):
        from backend.main import _rate_limit_store

        _rate_limit_store.clear()
        yield
        _rate_limit_store.clear()

    @pytest.fixture
    def mock_services(self, isolated_write_targets):
        from unittest.mock import MagicMock, patch

        with patch("backend.main.stt_service") as stt, patch(
            "backend.main.llm_service"
        ) as llm, patch("backend.main.tts_service") as tts:
            stt.is_loaded = True
            stt.transcribe.return_value = "What technologies did you use?"
            llm.generate.return_value = "I built InterviewTTS with FastAPI."

            async def mock_synthesize(text, output_path=None):
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

    def _new_conversation(self, client):
        response = client.post("/api/conversation")
        return response.json()["conversation_id"]

    def test_oversized_upload_rejected_with_413_and_stt_never_runs(
        self, client, mock_services
    ):
        """The oversize guard fires before STT is ever reached."""
        cid = self._new_conversation(client)
        oversized = b"x" * (MAX_AUDIO_SIZE + 1)

        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("big.webm", oversized, "audio/webm")},
        )

        assert response.status_code == 413
        assert "detail" in response.json()
        # The pipeline must not have been entered at all.
        mock_services["stt"].transcribe.assert_not_called()

    def test_normal_upload_still_passes_through(self, client, mock_services):
        """A small upload is unaffected by the new guard."""
        cid = self._new_conversation(client)
        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("small.webm", b"fake audio data", "audio/webm")},
        )
        assert response.status_code == 200
        mock_services["stt"].transcribe.assert_called_once()

    def test_in_route_check_still_guards_chunked_uploads(self):
        """Defence in depth: no Content-Length still gets rejected in-route.

        A chunked body has no Content-Length, so the pre-parse guard cannot
        see it. The original in-route check must remain as the second line of
        defence, producing 422 rather than the new 413.

        Driven through the route function directly: httpx cannot emit a
        streamed multipart body (python-multipart rejects it with a generic
        400 before the route is reached), so a real chunked request is not
        reproducible through TestClient.
        """
        import backend.main as main

        cid = "in-route-oversize-cid"
        main.conversations[cid] = {
            "turns": [],
            "messages": [],
            "summary": "",
        }

        class OversizedUpload:
            content_type = "audio/webm"

            async def read(self):
                return b"x" * (MAX_AUDIO_SIZE + 1)

        with pytest.raises(HTTPException) as excinfo:
            import asyncio

            asyncio.run(main.send_message(cid, OversizedUpload()))

        assert excinfo.value.status_code == 422
        assert excinfo.value.detail == "Audio too long (max 30 seconds)"
        main.conversations.pop(cid, None)

    def test_in_route_check_still_rejects_empty_audio(self, client, mock_services):
        """The pre-existing empty-body 422 is untouched."""
        cid = self._new_conversation(client)
        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("empty.webm", b"", "audio/webm")},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Empty audio file"
        mock_services["stt"].transcribe.assert_not_called()

    def test_stream_route_in_route_check_still_guards_oversize(self):
        """The streaming route keeps its own in-route 422 as well."""
        import asyncio

        import backend.main as main

        cid = "stream-oversize-cid"
        main.conversations[cid] = {"turns": [], "messages": [], "summary": ""}

        class OversizedUpload:
            content_type = "audio/webm"

            async def read(self):
                return b"x" * (MAX_AUDIO_SIZE + 1)

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(main.send_message_stream(cid, OversizedUpload()))

        assert excinfo.value.status_code == 422
        assert excinfo.value.detail == "Audio too long (max 30 seconds)"
        main.conversations.pop(cid, None)
