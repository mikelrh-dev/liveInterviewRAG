"""Tests for LLM service with mocked OpenRouter API."""

import httpx
import pytest
from unittest.mock import patch, MagicMock

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

        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            svc = LLMService(api_key="test-key")
            result = svc.generate("Cuéntame de ti")

            assert "Mikel" in result
            mock_client.post.assert_called_once()

    def test_generate_rate_limit(self):
        """Generate raises on rate limit."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="Rate limit"):
                svc.generate("Hello")

    def test_generate_auth_error(self):
        """Generate raises on auth error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            svc = LLMService(api_key="bad-key")
            with pytest.raises(RuntimeError, match="Authentication"):
                svc.generate("Hello")

    def test_generate_server_error(self):
        """Generate raises on server error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"

        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="temporarily unavailable"):
                svc.generate("Hello")

    def test_generate_timeout(self):
        """Generate raises on timeout."""
        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.TimeoutException("timeout")

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

        with patch("backend.services.llm.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = mock_client_instance
            mock_client.return_value.__exit__.return_value = False

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
