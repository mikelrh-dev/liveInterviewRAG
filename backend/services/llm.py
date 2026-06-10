"""LLM service using Owl API for candidate response generation."""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class LLMService:
    """Owl API client for generating candidate responses."""

    def __init__(self, api_key: str, api_url: str, model: str = "gpt-3.5-turbo"):
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def generate(self, prompt: str, context: str = "", system_prompt: str = "") -> str:
        """Generate a response using the Owl API.

        Args:
            prompt: The user's question/input.
            context: RAG-retrieved context to include.
            system_prompt: System prompt defining the candidate persona.

        Returns:
            Generated response text.

        Raises:
            RuntimeError: If API call fails or returns invalid response.
        """
        if not self.api_key:
            raise RuntimeError("OWL_API_KEY not configured")

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if context:
            user_content = f"Context from candidate's profile:\n{context}\n\nUser question: {prompt}"
        else:
            user_content = prompt

        messages.append({"role": "user", "content": user_content})

        try:
            client = await self._get_client()
            response = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 500,
                },
            )

            if response.status_code == 429:
                logger.warning("Rate limited by Owl API")
                raise RuntimeError("Rate limit exceeded. Please try again later.")

            if response.status_code != 200:
                logger.error("Owl API error: %d - %s", response.status_code, response.text)
                raise RuntimeError(f"LLM request failed: HTTP {response.status_code}")

            data = response.json()
            return data["choices"][0]["message"]["content"]

        except httpx.TimeoutException:
            logger.error("Owl API timeout")
            raise RuntimeError("Response generation timed out")
        except httpx.HTTPError as e:
            logger.error("Owl API HTTP error: %s", e)
            raise RuntimeError(f"Response generation temporarily unavailable: {e}")

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
