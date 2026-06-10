"""LLM service using OpenRouter API with Owl Alpha model."""

import logging

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


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
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    },
                )

                if response.status_code == 429:
                    logger.warning("Rate limited by OpenRouter API")
                    raise RuntimeError("Rate limit exceeded. Please try again later.")

                if response.status_code == 401 or response.status_code == 403:
                    logger.error("OpenRouter auth error: %s", response.text)
                    raise RuntimeError("Authentication failed. Check your OPENROUTER_API_KEY.")

                if response.status_code != 200:
                    logger.error("OpenRouter API error: %d - %s", response.status_code, response.text)
                    raise RuntimeError(f"Response generation temporarily unavailable: HTTP {response.status_code}")

                data = response.json()
                return data["choices"][0]["message"]["content"]

        except httpx.TimeoutException:
            logger.error("OpenRouter API timeout")
            raise RuntimeError("Response generation timed out. Try again.")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("OpenRouter API error: %s", e)
            raise RuntimeError(f"Response generation temporarily unavailable: {e}")
