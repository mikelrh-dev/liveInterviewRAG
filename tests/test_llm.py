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
