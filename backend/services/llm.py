"""LLM service using OpenRouter API (sync + streaming)."""

import json
import logging
from typing import Generator, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


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
                 temperature: float = 0.7, max_tokens: int = 300,
                 google_api_key: str = "", google_model: str = "gemini-3.1-flash-lite"):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.google_api_key = google_api_key
        self.google_model = google_model

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

    # ── Fallback routing ─────────────────────────────────────
    #
    # generate() / generate_stream() try Google AI first (if a
    # key is set) and fall back to OpenRouter on any exception.

    def generate(self, prompt: str, context: str = "", system_prompt: str = "") -> str:
        """Try Google AI first, fallback to OpenRouter."""
        if self.google_api_key:
            try:
                return self._googleai_generate(prompt, context, system_prompt)
            except Exception as e:
                logger.warning("Google AI generate failed, falling back to OpenRouter: %s", e)
        return self._openrouter_generate(prompt, context, system_prompt)

    def generate_stream(self, prompt: str, context: str = "",
                        system_prompt: str = "") -> Generator[str, None, None]:
        """Try Google AI first, fallback to OpenRouter."""
        if self.google_api_key:
            try:
                yield from self._googleai_generate_stream(prompt, context, system_prompt)
                return
            except Exception as e:
                logger.warning("Google AI stream failed, falling back to OpenRouter: %s", e)
        yield from self._openrouter_generate_stream(prompt, context, system_prompt)

    def generate_stream_with_context(
        self,
        prompt: str,
        context: str = "",
        system_prompt: str = "",
        context_chunks: Optional[List[dict]] = None,
    ) -> tuple:
        """Generate stream + return the context chunks used.

        Args:
            prompt: User's question.
            context: RAG context string.
            system_prompt: System-level instructions.
            context_chunks: List of chunk dicts used for this response.

        Returns:
            (Generator[str, None, None], List[dict]): token iterator and chunks_used list.
        """
        if context_chunks is None:
            context_chunks = []
        return self.generate_stream(prompt, context, system_prompt), context_chunks

    # ── OpenRouter provider ──────────────────────────────────

    def _openrouter_generate(self, prompt: str, context: str = "", system_prompt: str = "") -> str:
        """Generate a full response via OpenRouter (blocking)."""
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

    def _openrouter_generate_stream(self, prompt: str, context: str = "",
                                    system_prompt: str = "") -> Generator[str, None, None]:
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

    # ── Google AI provider ───────────────────────────────────

    def _googleai_generate(self, prompt: str, context: str = "",
                           system_prompt: str = "") -> str:
        """Generate a full response via Google AI API (blocking)."""
        if not self.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY not configured")

        contents = []
        user_text = (
            f"Context from the candidate's profile:\n{context}\n\n"
            f"Recruiter question: {prompt}"
        ) if context else prompt
        contents.append({"role": "user", "parts": [{"text": user_text}]})

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if system_prompt:
            body["system_instruction"] = {"parts": [{"text": system_prompt}]}

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{GOOGLE_API_BASE}/models/{self.google_model}:generateContent",
                    headers={
                        "x-goog-api-key": self.google_api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                )

                if response.status_code != 200:
                    error_body = response.text[:300]
                    if response.status_code == 429:
                        raise RuntimeError("Google AI rate limit exceeded.")
                    raise RuntimeError(
                        f"Google AI generate error: HTTP {response.status_code} - {error_body}"
                    )

                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Google AI returned empty response")
                parts = candidates[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)

        except httpx.TimeoutException:
            raise RuntimeError("Google AI request timed out.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Google AI generate failed: {e}")

    def _googleai_generate_stream(self, prompt: str, context: str = "",
                                  system_prompt: str = "") -> Generator[str, None, None]:
        """Stream tokens via Google AI API.

        Yields text chunks as they arrive from the SSE event stream.
        """
        if not self.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY not configured")

        contents = []
        user_text = (
            f"Context from the candidate's profile:\n{context}\n\n"
            f"Recruiter question: {prompt}"
        ) if context else prompt
        contents.append({"role": "user", "parts": [{"text": user_text}]})

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if system_prompt:
            body["system_instruction"] = {"parts": [{"text": system_prompt}]}

        try:
            with httpx.Client(timeout=60.0) as client:
                url = (
                    f"{GOOGLE_API_BASE}/models/{self.google_model}"
                    ":streamGenerateContent?alt=sse"
                )
                with client.stream(
                    "POST",
                    url,
                    headers={
                        "x-goog-api-key": self.google_api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as response:

                    if response.status_code != 200:
                        error_body = response.text[:300]
                        if response.status_code == 429:
                            raise RuntimeError("Google AI rate limit exceeded.")
                        raise RuntimeError(
                            f"Google AI streaming error: HTTP {response.status_code} - {error_body}"
                        )

                    # Google AI SSE: each `data: ` line is a JSON chunk
                    for line in response.iter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        candidates = data.get("candidates", [])
                        if not candidates:
                            continue
                        finish = candidates[0].get("finishReason")
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                        if finish:
                            break  # STOP, MAX_TOKENS, SAFETY, etc.

        except httpx.TimeoutException:
            raise RuntimeError("Google AI streaming timed out.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Google AI streaming failed: {e}")
