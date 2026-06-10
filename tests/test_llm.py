"""Tests for LLM service with mocked OpenRouter API."""

import pytest
from unittest.mock import MagicMock, patch

from backend.services.llm import LLMService


class TestLLMService:
    """Tests for OpenRouter LLM wrapper."""

    def test_init(self):
        """LLM service initializes with config."""
        svc = LLMService(api_key="test-key", model="openrouter/owl-alpha")
        assert svc.api_key == "test-key"
        assert svc.model == "openrouter/owl-alpha"
        assert svc.temperature == 0.7
        assert svc.max_tokens == 500

    def test_generate_no_api_key(self):
        """Generate raises if no API key configured."""
        svc = LLMService(api_key="")
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            svc.generate("Hello")

    def test_generate_success(self):
        """Generate returns text from successful API response."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(
                content="I am a junior developer with experience in Python."
            ))
        ]

        with patch("backend.services.llm.OpenRouter") as mock_openrouter:
            mock_client = MagicMock()
            mock_client.chat.send.return_value = mock_response
            mock_openrouter.return_value.__enter__.return_value = mock_client

            svc = LLMService(api_key="test-key")
            result = svc.generate("Tell me about yourself")

            assert "junior developer" in result
            mock_client.chat.send.assert_called_once()

    def test_generate_rate_limit(self):
        """Generate raises on rate limit."""
        with patch("backend.services.llm.OpenRouter") as mock_openrouter:
            mock_client = MagicMock()
            mock_client.chat.send.side_effect = Exception("rate limit exceeded")
            mock_openrouter.return_value.__enter__.return_value = mock_client

            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="Rate limit"):
                svc.generate("Hello")

    def test_generate_server_error(self):
        """Generate raises on server error."""
        with patch("backend.services.llm.OpenRouter") as mock_openrouter:
            mock_client = MagicMock()
            mock_client.chat.send.side_effect = Exception("API error 500")
            mock_openrouter.return_value.__enter__.return_value = mock_client

            svc = LLMService(api_key="test-key")
            with pytest.raises(RuntimeError, match="Response generation"):
                svc.generate("Hello")
