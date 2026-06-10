"""Tests for LLM service with mocked Owl API."""

import pytest
import httpx

from backend.services.llm import LLMService


class TestLLMService:
    """Tests for Owl API LLM wrapper."""

    def test_init(self):
        """LLM service initializes with config."""
        svc = LLMService(api_key="test-key", api_url="https://api.test.com", model="test-model")
        assert svc.api_key == "test-key"
        assert svc.api_url == "https://api.test.com"
        assert svc.model == "test-model"

    @pytest.mark.asyncio
    async def test_generate_no_api_key(self):
        """Generate raises if no API key configured."""
        svc = LLMService(api_key="", api_url="https://api.test.com")
        with pytest.raises(RuntimeError, match="not configured"):
            await svc.generate("Hello")

    @pytest.mark.asyncio
    async def test_generate_success(self):
        """Generate returns text from successful API response."""
        mock_response = httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {"message": {"content": "I am a junior developer with experience in Python."}}
                ]
            },
            request=httpx.Request("POST", "https://api.test.com"),
        )

        svc = LLMService(api_key="test-key", api_url="https://api.test.com")

        # Mock the client
        mock_client = httpx.AsyncClient()
        svc._client = mock_client

        with pytest.MonkeyPatch.context() as m:
            async def mock_post(*args, **kwargs):
                return mock_response
            m.setattr(mock_client, "post", mock_post)

            result = await svc.generate("Tell me about yourself")
            assert "junior developer" in result

    @pytest.mark.asyncio
    async def test_generate_rate_limit(self):
        """Generate raises on 429 rate limit."""
        mock_response = httpx.Response(
            status_code=429,
            text="Rate limited",
            request=httpx.Request("POST", "https://api.test.com"),
        )

        svc = LLMService(api_key="test-key", api_url="https://api.test.com")
        mock_client = httpx.AsyncClient()
        svc._client = mock_client

        with pytest.MonkeyPatch.context() as m:
            async def mock_post(*args, **kwargs):
                return mock_response
            m.setattr(mock_client, "post", mock_post)

            with pytest.raises(RuntimeError, match="Rate limit"):
                await svc.generate("Hello")

    @pytest.mark.asyncio
    async def test_generate_server_error(self):
        """Generate raises on non-200 status."""
        mock_response = httpx.Response(
            status_code=500,
            text="Internal error",
            request=httpx.Request("POST", "https://api.test.com"),
        )

        svc = LLMService(api_key="test-key", api_url="https://api.test.com")
        mock_client = httpx.AsyncClient()
        svc._client = mock_client

        with pytest.MonkeyPatch.context() as m:
            async def mock_post(*args, **kwargs):
                return mock_response
            m.setattr(mock_client, "post", mock_post)

            with pytest.raises(RuntimeError, match="HTTP 500"):
                await svc.generate("Hello")

    @pytest.mark.asyncio
    async def test_close(self):
        """Close cleans up HTTP client."""
        svc = LLMService(api_key="test-key", api_url="https://api.test.com")
        await svc.close()  # Should not raise
