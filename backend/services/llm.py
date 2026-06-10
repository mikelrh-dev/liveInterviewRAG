"""LLM service using OpenRouter API (sync + streaming)."""

import json
import logging
from typing import Generator, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class SentenceBuffer:
    """Accumulates tokens and yields complete sentences.

    When a sentence boundary (. ! ? \\n) is found, the sentence is sent to TTS
    immediately while the LLM continues generating the next one in parallel.

    Only yields at natural grammatical boundaries — no character-based early
    cutoff, because splitting at artificial points creates audible micro-pauses
    between audio chunks that break the realism of the speech.
    """

    SENTENCE_END = {'.', '!', '?', '\n'}

    def __init__(self):
        self.buffer = ""

    def add_token(self, token: str) -> List[str]:
        """Feed a token; return any complete sentences found."""
        self.buffer += token
        sentences: List[str] = []

        while True:
            pos = -1
            for i, ch in enumerate(self.buffer):
                if ch in self.SENTENCE_END:
                    if ch == '.' and (i + 1 < len(self.buffer) and self.buffer[i + 1] != ' '):
                        continue  # mid-word period like "Dr."
                    pos = i
                    break

            if pos == -1:
                break

            sentence = self.buffer[:pos + 1].strip()
            self.buffer = self.buffer[pos + 1:]
            if sentence:
                sentences.append(sentence)

        return sentences

    def flush(self) -> List[str]:
        """Return any remaining text as a sentence."""
        rest = self.buffer.strip()
        self.buffer = ""
        return [rest] if rest else []


class LLMService:
    """OpenRouter API client for generating candidate responses."""

    def __init__(self, api_key: str, model: str = "openrouter/owl-alpha",
                 temperature: float = 0.7, max_tokens: int = 300):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _build_messages(self, prompt: str, context: str = "", system_prompt: str = "") -> list:
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
        return messages

    def _request_kwargs(self, messages: list, stream: bool = False) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }

    def generate(self, prompt: str, context: str = "", system_prompt: str = "") -> str:
        """Generate a full response (blocking, non-streaming)."""
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured")

        messages = self._build_messages(prompt, context, system_prompt)

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._request_kwargs(messages, stream=False),
                )

                if response.status_code == 429:
                    raise RuntimeError("Rate limit exceeded. Please try again later.")
                if response.status_code in (401, 403):
                    raise RuntimeError("Authentication failed. Check your OPENROUTER_API_KEY.")
                if response.status_code != 200:
                    raise RuntimeError(f"Response generation temporarily unavailable: HTTP {response.status_code}")

                data = response.json()
                return data["choices"][0]["message"]["content"]

        except httpx.TimeoutException:
            raise RuntimeError("Response generation timed out. Try again.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Response generation temporarily unavailable: {e}")

    def generate_stream(self, prompt: str, context: str = "", system_prompt: str = "") -> Generator[str, None, None]:
        """Generate response tokens via streaming. Yields text chunks as they arrive.

        This is a synchronous generator — run via asyncio.to_thread + Queue for async use.
        """
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured")

        messages = self._build_messages(prompt, context, system_prompt)

        try:
            with httpx.Client(timeout=60.0) as client:
                with client.stream(
                    "POST",
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._request_kwargs(messages, stream=True),
                ) as response:

                    if response.status_code != 200:
                        error_body = response.text[:200]
                        if response.status_code == 429:
                            raise RuntimeError("Rate limit exceeded.")
                        raise RuntimeError(f"OpenRouter streaming error: HTTP {response.status_code} - {error_body}")

                    for line in response.iter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta

        except httpx.TimeoutException:
            raise RuntimeError("Response generation timed out.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Streaming failed: {e}")
