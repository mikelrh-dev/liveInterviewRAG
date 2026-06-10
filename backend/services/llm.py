"""LLM service using OpenRouter API with Owl Alpha model."""

import logging
from typing import Optional

from openrouter import OpenRouter

logger = logging.getLogger(__name__)


class LLMService:
    """OpenRouter API client for generating candidate responses."""

    def __init__(self, api_key: str, model: str = "openrouter/owl-alpha",
                 temperature: float = 0.7, max_tokens: int = 500):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, prompt: str, context: str = "", system_prompt: str = "") -> str:
        """Generate a response using the OpenRouter API.

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
            raise RuntimeError("OPENROUTER_API_KEY not configured")

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if context:
            user_content = (
                f"Context from the candidate's profile:\n{context}\n\n"
                f"Recruiter question: {prompt}"
            )
        else:
            user_content = prompt

        messages.append({"role": "user", "content": user_content})

        try:
            with OpenRouter(api_key=self.api_key) as client:
                response = client.chat.send(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            return response.choices[0].message.content

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "429" in error_msg:
                logger.warning("Rate limited by OpenRouter API")
                raise RuntimeError("Rate limit exceeded. Please try again later.")
            logger.error("OpenRouter API error: %s", e)
            raise RuntimeError(f"Response generation temporarily unavailable: {e}")
