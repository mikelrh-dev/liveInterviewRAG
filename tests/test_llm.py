"""Tests for LLM service with mocked OpenRouter API."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from unittest.mock import patch, MagicMock

import backend.services.llm as llm_module
from backend.services.llm import LLMService


class TestLLMService:
    """Tests for OpenRouter LLM wrapper."""

    def test_init(self):
        """LLM service initializes with config."""
        svc = LLMService(api_key="test-key", model="openrouter/owl-alpha")
        assert svc.api_key == "test-key"
        assert svc.model == "openrouter/owl-alpha"
        assert svc.temperature == 0.7
        assert svc.max_tokens == 300

    def test_generate_no_api_key(self):
        """Generate raises if no API key configured."""
        svc = LLMService(api_key="")
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            svc.generate("Hello")

    def test_generate_success(self):
        """Generate returns text from successful API response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Soy Mikel, un desarrollador junior."}}]
        }

        # Patch the factory, NOT httpx.Client: bypassing the singleton cache
        # prevents one test's mock from freezing into the shared client and
        # leaking into later tests (design D2).
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("backend.services.llm._get_client", return_value=mock_client):
            svc = LLMService(api_key="test-key")
            result = svc.generate("Cuéntame de ti")

            assert "Mikel" in result
            mock_client.post.assert_called_once()

    def test_generate_rate_limit(self):
        """Generate raises on rate limit."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("backend.services.llm._get_client", return_value=mock_client):
            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="Rate limit"):
                svc.generate("Hello")

    def test_generate_auth_error(self):
        """Generate raises on auth error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("backend.services.llm._get_client", return_value=mock_client):
            svc = LLMService(api_key="bad-key")
            with pytest.raises(RuntimeError, match="Authentication"):
                svc.generate("Hello")

    def test_generate_server_error(self):
        """Generate raises on server error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("backend.services.llm._get_client", return_value=mock_client):
            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="temporarily unavailable"):
                svc.generate("Hello")

    def test_generate_timeout(self):
        """Generate raises on timeout."""
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        with patch("backend.services.llm._get_client", return_value=mock_client):
            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="timed out"):
                svc.generate("Hello")


class TestLLMStreamChunksUsed:
    """Tests for generate_stream returning chunks_used metadata."""

    def test_generate_stream_returns_chunks_used(self):
        """generate_stream_with_context returns (tokens_iter, chunks_used_list)."""
        mock_stream_response = MagicMock()
        mock_stream_response.status_code = 200
        mock_stream_response.__enter__ = MagicMock(return_value=mock_stream_response)
        mock_stream_response.__exit__ = MagicMock(return_value=False)
        mock_stream_response.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": " world"}}]}',
            "data: [DONE]",
        ]

        mock_client_instance = MagicMock()
        mock_client_instance.stream.return_value = mock_stream_response

        with patch("backend.services.llm._get_client", return_value=mock_client_instance):
            svc = LLMService(api_key="test-key")
            context_chunks = [
                {"text": "Built web apps", "score": 0.85, "source": "cv.md"}
            ]
            tokens_iter, returned_chunks = svc.generate_stream_with_context(
                prompt="Hi",
                context="Built web apps with Python.",
                system_prompt="",
                context_chunks=context_chunks,
            )
            tokens = list(tokens_iter)
            assert len(tokens) > 0
            assert returned_chunks == context_chunks


class TestSharedHTTPClient:
    """Cap-1: one shared thread-safe httpx.Client across all provider calls.

    Spec: LLM HTTP Pooling — Shared Client Reuse / Shutdown Closing.
    """

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Guarantee a clean shared-client state around every test."""
        llm_module.close_http_clients()
        yield
        llm_module.close_http_clients()

    def test_get_client_returns_same_instance(self):
        """Consecutive factory calls return the exact same client object."""
        first = llm_module._get_client()
        second = llm_module._get_client()
        assert first is second

    def test_get_client_thread_safe_concurrent_identity(self):
        """16 concurrent factory calls from 8 threads all get one instance."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            clients = list(pool.map(lambda _: llm_module._get_client(), range(16)))
        assert len(clients) == 16
        assert all(c is clients[0] for c in clients)

    def test_generate_uses_shared_client_timeout_60(self):
        """Client constructed once with timeout=60.0; both generate calls reuse it."""
        mock_client_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        mock_client_instance.post.return_value = mock_response

        with patch("backend.services.llm.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value = mock_client_instance

            svc = LLMService(api_key="test-key")
            assert svc.generate("First") == "ok"
            assert svc.generate("Second") == "ok"

            # Constructed exactly once, with the pre-change timeout
            mock_client_cls.assert_called_once_with(timeout=60.0)
            # Both calls went through the same shared client
            assert mock_client_instance.post.call_count == 2

    def test_close_http_clients_closes_once_and_safe_uninitialized(self):
        """close() is a no-op when never created and closes exactly once otherwise."""
        # Safe when the client was never created
        llm_module.close_http_clients()

        mock_client = MagicMock()
        with patch("backend.services.llm.httpx.Client", return_value=mock_client):
            created = llm_module._get_client()
            assert created is mock_client

        llm_module.close_http_clients()
        llm_module.close_http_clients()  # idempotent
        mock_client.close.assert_called_once()
