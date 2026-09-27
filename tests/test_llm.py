"""Tests for LLM service with mocked OpenRouter API."""

import json
import logging
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


# ── Google → OpenRouter fallback ────────────────────────────
#
# Before this class the primary provider, the fallback, and the reliability
# story around them had ZERO coverage: every test drove OpenRouter only, so the
# Google path and the provider switch shipped untested. The mid-stream
# concatenation defect is what that gap hid.


def _exception_chain(exc):
    """Every exception reachable from `exc` via cause or context.

    _googleai_generate_stream rewraps the transport error in a RuntimeError
    with an implicit `raise`, so the ReadError sits in __context__, not
    __cause__. Walking both links is the only way to assert the original
    failure is still diagnosable after the re-raise.
    """
    seen, current = [], exc
    while current is not None and id(current) not in {id(e) for e in seen}:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _drain(stream):
    """Consume a stream fully, returning (tokens, failure-or-None).

    The failure is returned rather than raised because the failure IS the
    observation: these tests care about the exact text the client would have
    received alongside whether the stream reported the death.
    """
    tokens = []
    failure = None
    try:
        for token in stream:
            tokens.append(token)
    except Exception as exc:  # noqa: BLE001 - deliberately observing the raise
        failure = exc
    return tokens, failure


def _google_sse(text):
    """One Google AI SSE line carrying a single text part."""
    return "data: " + json.dumps(
        {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    )


def _openrouter_sse(text):
    """One OpenRouter SSE line carrying a single content delta."""
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


class TestGoogleFallbackStreaming:
    """Fallback routing between the two providers, streaming and blocking.

    The rule under test, stated once:

        A provider that fails BEFORE yielding anything may be swapped out.
        A provider that fails AFTER yielding anything may not, because the
        partial answer is already in the client's hands and a second provider
        would append a second complete answer to it.

    Google and OpenRouter are both driven through their REAL implementations
    with only httpx mocked, so the tests exercise the actual SSE parsing rather
    than a hand-written stand-in for it.
    """

    GOOGLE_TOKENS = ["GOOGLE-PARTIAL-1 ", "GOOGLE-PARTIAL-2"]
    OPENROUTER_TOKENS = ["OpenRouter-", "completa."]
    OPENROUTER_TEXT = "OpenRouter-completa."
    GOOGLE_TEXT = "GOOGLE-PARTIAL-1 GOOGLE-PARTIAL-2"

    # ── fixtures / builders ─────────────────────────────────

    def _client(self, google_response, openrouter_response):
        """An httpx.Client stand-in that routes by host.

        Routing on the URL rather than on call order means both real provider
        implementations run, and asserting on `openrouter_response.iter_lines`
        proves whether the fallback stream was ever started.
        """
        def _stream(method, url, **kwargs):
            return google_response if "generativelanguage" in url else openrouter_response

        client = MagicMock()
        client.stream.side_effect = _stream
        return client

    def _google_ok_then_dead(self):
        """A 200 Google stream that dies mid-iteration, after two real tokens."""
        def _lines():
            for token in self.GOOGLE_TOKENS:
                yield _google_sse(token)
            raise httpx.ReadError("connection reset by peer")

        response = MagicMock()
        response.status_code = 200
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.iter_lines = _lines
        return response

    def _google_rate_limited(self):
        """A 429 on the very first byte: no token was ever produced."""
        response = MagicMock()
        response.status_code = 429
        response.read.return_value = b'{"error": {"status": "RESOURCE_EXHAUSTED"}}'
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        # A 429 must be rejected before any line is parsed; touching iter_lines
        # here would mean the pre-token path stopped short-circuiting.
        response.iter_lines = MagicMock(
            side_effect=AssertionError("429 must not reach iter_lines()")
        )
        return response

    def _openrouter_ok(self):
        response = MagicMock()
        response.status_code = 200
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.iter_lines.return_value = (
            [_openrouter_sse(t) for t in self.OPENROUTER_TOKENS] + ["data: [DONE]"]
        )
        return response

    def _service(self, google_response):
        return LLMService(api_key="test-key", google_api_key="test-google-key")

    def _patched(self, google_response, openrouter_response):
        """(patcher, service) with both real providers over a routed client."""
        client = self._client(google_response, openrouter_response)
        svc = self._service(google_response)
        return patch("backend.services.llm._get_client", return_value=client), svc

    # ── 1. the regression, stated as behaviour ──────────────

    def test_post_token_failure_does_not_concatenate_a_second_answer(self):
        """Two Google tokens then a transport death yields those two tokens only.

        Before the fix the consumer received four tokens: the partial Google
        answer immediately followed by the whole OpenRouter answer.
        """
        google = self._google_ok_then_dead()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)

        with patcher:
            tokens, failure = _drain(svc.generate_stream("Cuéntame de ti"))

        # The exact text the client would have received. Asserted as a string so
        # a regression prints the garbled concatenation in the diff.
        assert "".join(tokens) == self.GOOGLE_TEXT
        assert tokens == self.GOOGLE_TOKENS

        # A second answer must never be appended...
        assert failure is not None, "mid-stream death must be reported, not hidden"
        assert isinstance(failure, RuntimeError)
        # ...and the fallback must never have been started.
        openrouter.iter_lines.assert_not_called()

    def test_post_token_failure_emits_no_text_from_the_second_provider(self):
        """No interleaving: the client sees the primary's text and nothing else."""
        google = self._google_ok_then_dead()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)

        with patcher:
            tokens, failure = _drain(svc.generate_stream("Cuéntame de ti"))

        text = "".join(tokens)
        assert "GOOGLE-PARTIAL" in text
        assert "OpenRouter" not in text
        assert self.OPENROUTER_TEXT not in text
        assert failure is not None

    def test_post_token_failure_reports_how_far_the_stream_got(self):
        """The failure names the token count, so the operator can see the loss."""
        google = self._google_ok_then_dead()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)

        with patcher:
            _, failure = _drain(svc.generate_stream("Cuéntame de ti"))

        assert failure is not None
        assert "2" in str(failure)
        # The original transport error survives the re-raise, so the abort is
        # diagnosable rather than a bare "stream failed".
        assert any(
            isinstance(e, httpx.ReadError) for e in _exception_chain(failure)
        ), f"transport error lost from the chain: {failure!r}"

    # ── 2. the fallback still works when nothing was yielded ─

    def test_pre_token_rate_limit_falls_back_to_the_full_openrouter_answer(self):
        """A 429 before the first token is a clean provider switch.

        This is the free case: nothing has reached the client, so OpenRouter
        answers in full, byte for byte.
        """
        google = self._google_rate_limited()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)

        with patcher:
            tokens, failure = _drain(svc.generate_stream("Cuéntame de ti"))

        assert failure is None
        assert tokens == self.OPENROUTER_TOKENS
        assert "".join(tokens) == self.OPENROUTER_TEXT
        openrouter.iter_lines.assert_called_once()

    def test_pre_token_failure_with_no_google_key_still_uses_openrouter(self):
        """With no Google key configured, OpenRouter answers directly."""
        openrouter = self._openrouter_ok()
        client = self._client(MagicMock(), openrouter)
        svc = LLMService(api_key="test-key", google_api_key="")

        with patch("backend.services.llm._get_client", return_value=client):
            tokens, failure = _drain(svc.generate_stream("Cuéntame de ti"))

        assert failure is None
        assert "".join(tokens) == self.OPENROUTER_TEXT

    # ── 3. the two log paths are distinguishable ────────────

    def test_pre_token_switch_and_post_token_abort_log_differently(self, caplog):
        """A provider switch is a warning; a lost answer is an error.

        Conflating the two would hide the only case where a candidate actually
        received a degraded answer, behind the noise of routine 429s.
        """
        caplog.set_level(logging.DEBUG, logger="backend.services.llm")
        records = {}

        for name, google in (
            ("pre_token", self._google_rate_limited()),
            ("post_token", self._google_ok_then_dead()),
        ):
            with caplog.at_level(logging.DEBUG, logger="backend.services.llm"):
                patcher, svc = self._patched(google, self._openrouter_ok())
                with patcher:
                    _drain(svc.generate_stream("Cuéntame de ti"))
            relevant = [
                r for r in caplog.records
                if r.name == "backend.services.llm" and "Google" in r.getMessage()
            ]
            assert relevant, f"{name} logged nothing"
            records[name] = relevant

        pre = records["pre_token"][-1]
        post = records["post_token"][-1]

        assert pre.levelno == logging.WARNING
        assert post.levelno == logging.ERROR
        # Different words, not just different levels.
        assert pre.getMessage() != post.getMessage()
        assert "falling back" in pre.getMessage()
        assert "falling back" not in post.getMessage()
        assert "2" in post.getMessage()

    # ── 4. the non-streaming path is correct and must stay so ─

    def test_blocking_generate_still_falls_back_unconditionally(self):
        """Guard: a blocking call that raises has produced nothing, so fallback
        is always safe. A future edit must not "fix" this path to match the
        streaming one — the asymmetry is the point.
        """
        svc = LLMService(api_key="test-key", google_api_key="test-google-key")

        google = MagicMock()
        google.status_code = 200
        google.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ignored"}]}}]
        }
        openrouter = MagicMock()
        openrouter.status_code = 200
        openrouter.json.return_value = {
            "choices": [{"message": {"content": self.OPENROUTER_TEXT}}]
        }
        client = MagicMock()
        # _googleai_generate is replaced, so the only HTTP call left is the
        # fallback's; side_effect must not carry a payload for a call that
        # never happens or it is consumed by the wrong provider.
        client.post.side_effect = [openrouter]

        with patch("backend.services.llm._get_client", return_value=client):
            with patch.object(
                svc, "_googleai_generate", side_effect=RuntimeError("boom")
            ) as google_call:
                result = svc.generate("Cuéntame de ti")

        assert result == self.OPENROUTER_TEXT
        google_call.assert_called_once()
        # Fallback happened after a raise, with no token accounting at all.
        assert client.post.call_count == 1

    def test_blocking_generate_keeps_the_google_answer_when_it_works(self):
        """Guard: the blocking path must not consult OpenRouter on success."""
        svc = LLMService(api_key="test-key", google_api_key="test-google-key")
        openrouter = MagicMock()
        openrouter.status_code = 200
        openrouter.json.return_value = {
            "choices": [{"message": {"content": self.OPENROUTER_TEXT}}]
        }
        client = MagicMock()
        client.post.return_value = openrouter

        with patch("backend.services.llm._get_client", return_value=client):
            with patch.object(svc, "_googleai_generate", return_value="GOOGLE-OK"):
                assert svc.generate("Cuéntame de ti") == "GOOGLE-OK"

        # Only the blocked call happened; the fallback was never reached.
        assert client.post.call_count == 0

    # ── 5. generate_stream_with_context shares the defect ────

    def test_with_context_post_token_failure_does_not_concatenate(self):
        """The context variant delegates to generate_stream and must behave
        identically: a mid-stream death aborts rather than appending."""
        google = self._google_ok_then_dead()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)
        chunks = [{"text": "Built web apps", "score": 0.85, "source": "cv.md"}]

        with patcher:
            stream, returned = svc.generate_stream_with_context(
                prompt="Hi",
                context="Built web apps with Python.",
                system_prompt="",
                context_chunks=chunks,
            )
            tokens, failure = _drain(stream)

        assert "".join(tokens) == self.GOOGLE_TEXT
        assert failure is not None and isinstance(failure, RuntimeError)
        openrouter.iter_lines.assert_not_called()
        # Metadata is returned eagerly and must survive the abort untouched.
        assert returned == chunks

    def test_with_context_pre_token_failure_still_falls_back(self):
        """The context variant keeps the safe switch when nothing was yielded."""
        google = self._google_rate_limited()
        openrouter = self._openrouter_ok()
        patcher, svc = self._patched(google, openrouter)

        with patcher:
            stream, _ = svc.generate_stream_with_context(prompt="Hi")
            tokens, failure = _drain(stream)

        assert failure is None
        assert "".join(tokens) == self.OPENROUTER_TEXT


class TestGoogleProviderStreaming:
    """The primary provider's own SSE parsing, which the fallback depends on."""

    def test_google_stream_parses_parts_and_stops_on_finish_reason(self):
        """Baseline for the fixture the fallback tests rely on: without this,
        a broken parser would make them pass for the wrong reason."""
        response = MagicMock()
        response.status_code = 200
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.iter_lines.return_value = [
            _google_sse("Hola "),
            _google_sse("mundo"),
            "data: " + json.dumps(
                {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}
            ),
            _google_sse("IGNORED-AFTER-FINISH"),
        ]
        client = MagicMock()
        client.stream.return_value = response

        with patch("backend.services.llm._get_client", return_value=client):
            svc = LLMService(api_key="test-key", google_api_key="test-google-key")
            tokens = list(svc._googleai_generate_stream("Hi"))

        assert tokens == ["Hola ", "mundo"]

    def test_google_stream_without_key_raises_before_any_request(self):
        """No key means no request: the guard is the pre-token failure path."""
        client = MagicMock()
        with patch("backend.services.llm._get_client", return_value=client):
            svc = LLMService(api_key="test-key", google_api_key="")
            with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
                list(svc._googleai_generate_stream("Hi"))
        client.stream.assert_not_called()
