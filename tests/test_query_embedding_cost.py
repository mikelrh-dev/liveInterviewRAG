"""How many times does one turn embed the same query?

MEASURED, not assumed. The streaming pipeline called both
``get_chunks_with_scores`` and ``get_context_string`` back to back for one
turn; each reaches ``retrieve()``, which calls ``self._embedder.encode(...)``.
A first-substantive turn also ran the semantic cache's ``lookup`` and
``store``, each of which embeds the raw question. That is up to four embeds
of one question per turn.

These tests count real ``encode`` calls through a proxy wrapped around the
real ``all-MiniLM-L6-v2``, over a real RAG pipeline on the fixture corpus.
``build_pipeline`` leaves ``cache_dir=None``, so nothing writes to
``backend/.rag_cache/``. The proxy is installed on ``_embedder`` AFTER
ingestion, so the corpus encoding is not counted.

Two kinds of embed are distinguished, and the question below is chosen so
they can be told apart:

* the RETRIEVAL embed, which is ``expand_query(question)`` -- ``retrieve()``
  expands before embedding;
* the SEMANTIC CACHE embed, which is the RAW question (design D9: expansion
  is retrieval-oriented and would skew question-to-question similarity).

``expand_query`` fires on "proyecto", so the two are different strings here.
Without that they would be byte-identical and the counts would be
unattributable. The FAQ literal cache is also cleared so the question reaches
the RAG step instead of being answered before it.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.fixture_corpus import build_pipeline

#: Misses the literal FAQ cache and contains "proyecto", so ``expand_query``
#: rewrites it. Both properties are asserted in ``test_fixture_is_discernible``
#: rather than trusted, because either one silently collapsing makes every
#: count below unattributable.
QUESTION = "hablame del proyecto del detector de fraude"


class CountingEmbedder:
    """Forwards to the real model, recording every ``encode`` input."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.texts: list[str] = []

    def encode(self, texts, **kwargs):
        self.calls += 1
        if isinstance(texts, str):
            self.texts.append(texts)
        else:
            self.texts.extend(texts)
        return self._inner.encode(texts, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def reset(self):
        self.calls = 0
        self.texts = []

    def retrieval_embeds_for(self, query: str = QUESTION) -> list[str]:
        """Embeds ``retrieve()`` made for ``query``."""
        from backend.services.rag import expand_query

        return [t for t in self.texts if t == expand_query(query)]

    @property
    def retrieval_embeds(self) -> list[str]:
        return self.retrieval_embeds_for(QUESTION)

    @property
    def cache_embeds(self) -> list[str]:
        """Embeds made by the semantic cache (the raw question)."""
        return [t for t in self.texts if t == QUESTION]


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Every test here opens a conversation, and the rate limiter is module state.

    ``test_api.py`` has this same autouse fixture, but fixtures defined in a
    test module do not apply to other modules -- without it the fifth
    conversation in a run is refused and the test fails on
    ``KeyError: 'conversation_id'``, which reads as a product defect.
    """
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


@pytest.fixture
def counting_rag(fixture_corpus_targets):
    """A real pipeline whose embedder counts.

    The proxy replaces the private ``_embedder`` because that is the attribute
    ``retrieve()`` actually calls. The public ``embedder`` property returns the
    same object, so the semantic cache -- whose provider reads
    ``main.rag_pipeline.embedder`` at call time -- embeds through this counter
    too, which is the point.
    """
    rag = build_pipeline()
    if rag._use_tfidf:
        pytest.skip("TF-IDF fallback active: there is no embedder to count.")
    rag._embedder = CountingEmbedder(rag._embedder)
    return rag


@pytest.fixture
def live_services(counting_rag, monkeypatch):
    """Real RAG and a real semantic cache; STT / LLM / TTS replaced by doubles.

    The semantic cache is deliberately NOT doubled: it contributes embeds, and
    a double would erase the very cost being measured.
    """
    import backend.main as main_mod
    from backend.services import response_cache

    # The FAQ literal cache answers before RAG and would make this a different
    # turn. Emptied rather than patched per-key so no question can slip past.
    monkeypatch.setattr(response_cache, "_CACHED_QUESTIONS", [])
    monkeypatch.setattr(main_mod, "get_cached_response", lambda q: None, raising=False)

    async def _synthesize(text, output_path=None, **kwargs):
        from pathlib import Path

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return p

    async def _synthesize_sentence(text, sentence_id=0, output_dir=None, **kwargs):
        """The streaming path unpacks this as ``(sentence_id, path)``."""
        from pathlib import Path

        p = Path(output_dir) / f"sentence-{sentence_id}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return (sentence_id, p)

    with patch.object(main_mod, "rag_pipeline", counting_rag), \
         patch.object(main_mod, "stt_service") as stt, \
         patch.object(main_mod, "llm_service") as llm, \
         patch.object(main_mod, "tts_service") as tts:
        stt.is_loaded = True
        stt.transcribe.return_value = QUESTION
        llm.generate.return_value = "Una respuesta generada."
        llm.generate_stream_with_context.return_value = (
            iter(["Una respuesta generada."]),
            [],
        )
        tts.synthesize.side_effect = _synthesize
        tts.synthesize_sentence.side_effect = _synthesize_sentence

        yield SimpleNamespace(
            rag=counting_rag,
            counter=counting_rag._embedder,
            stt=stt,
            llm=llm,
        )


@pytest.fixture
def client(live_services):
    from backend.main import app

    return TestClient(app)


def _stream_events(client, conversation_id):
    with client.stream(
        "POST",
        f"/api/conversation/{conversation_id}/message/stream",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    ) as response:
        assert response.status_code == 200
        events = []
        for line in response.iter_lines():
            if line and line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _new_conversation(client):
    response = client.post("/api/conversation")
    assert response.status_code == 200, (
        f"could not open a conversation: {response.status_code} {response.text}"
    )
    return response.json()["conversation_id"]


def _post_blocking(client, conversation_id):
    response = client.post(
        f"/api/conversation/{conversation_id}/message",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    )
    assert response.status_code == 200
    return response


def test_fixture_is_discernible():
    """Guard: the two embed kinds must be different strings, or nothing below counts.

    Cheap to assert, and it is the assumption the whole file rests on. If a
    future ``expand_query`` stops firing on "proyecto", the cache embeds and
    the retrieval embeds become indistinguishable and these tests start
    passing for the wrong reason.
    """
    from backend.services.rag import expand_query
    from backend.services.response_cache import get_cached_response

    assert expand_query(QUESTION) != QUESTION, (
        "expand_query is now identity for this question, so retrieval embeds "
        "and cache embeds are indistinguishable -- pick another QUESTION"
    )
    assert get_cached_response(QUESTION) is None, (
        "QUESTION now hits the FAQ literal cache, so the turn never reaches "
        "RAG -- pick another QUESTION"
    )


class TestRetrieveWithContextIsEquivalentToTwoCalls:
    """The claim the whole deduplication rests on, checked directly.

    Not "the counts dropped" -- that would also be true if the two shapes had
    started disagreeing. This asserts that one retrieval produces byte-for-
    byte what the two separate calls produced, across questions that hit
    different ``detect_doc_type`` outcomes and different ``top_k`` values,
    including the empty-result case.
    """

    QUERIES = [
        QUESTION,
        "cuentame sobre ti en treinta segundos",
        "que testing hiciste en el proyecto de fraude",
        "hola",
    ]

    @pytest.mark.parametrize("query", QUERIES)
    @pytest.mark.parametrize("top_k", [1, 2, 3, 8])
    def test_matches_the_two_separate_calls(self, query, top_k, counting_rag):
        counter = counting_rag._embedder
        counter.reset()

        expected_chunks = counting_rag.get_chunks_with_scores(query, top_k=top_k)
        expected_context = counting_rag.get_context_string(query, top_k=top_k)
        assert len(counter.retrieval_embeds_for(query)) == 2, (
            "the two reference calls should each embed -- if they did not, "
            "this test is comparing something other than two retrievals"
        )

        counter.reset()
        context, chunks = counting_rag.retrieve_with_context(query, top_k=top_k)

        assert context == expected_context
        assert chunks == expected_chunks
        assert len(counter.retrieval_embeds_for(query)) == 1, (
            "the fused call must embed once"
        )

    def test_agrees_with_get_context_string_and_never_calls_it(self, counting_rag):
        """The fused call is not a wrapper around the two public methods.

        Worth pinning: if ``retrieve_with_context`` were ever reimplemented as
        ``return (self.get_context_string(q), self.get_chunks_with_scores(q))``
        the equivalence above would still hold and the duplicate embed would be
        back, silently.
        """
        called = []
        original_context = type(counting_rag).get_context_string
        original_chunks = type(counting_rag).get_chunks_with_scores

        def spy_context(self, *a, **kw):
            called.append("context")
            return original_context(self, *a, **kw)

        def spy_chunks(self, *a, **kw):
            called.append("chunks")
            return original_chunks(self, *a, **kw)

        with patch.object(type(counting_rag), "get_context_string", spy_context), \
             patch.object(type(counting_rag), "get_chunks_with_scores", spy_chunks):
            counting_rag.retrieve_with_context(QUESTION, top_k=3)

        assert called == [], (
            f"retrieve_with_context re-entered the public methods ({called}), "
            "so it retrieves twice and the deduplication is gone"
        )


class TestRetrievalEmbedsOnce:
    """The guarantee the deduplication is responsible for.

    One retrieval per turn, so one retrieval embed. This holds regardless of
    what else embeds the question.
    """

    def test_streaming_first_substantive_turn_retrieves_once(self, live_services, client):
        counter = live_services.counter
        counter.reset()
        conv_id = _new_conversation(client)
        events = _stream_events(client, conv_id)

        assert any(e.get("event") == "done" for e in events), "the turn never finished"
        assert len(counter.retrieval_embeds) == 1, (
            f"expected 1 retrieval embed, got {len(counter.retrieval_embeds)}: "
            f"{counter.texts}"
        )

    def test_streaming_later_turn_retrieves_once(self, live_services, client):
        counter = live_services.counter
        conv_id = _new_conversation(client)
        _stream_events(client, conv_id)

        counter.reset()
        _stream_events(client, conv_id)
        assert len(counter.retrieval_embeds) == 1, (
            f"expected 1 retrieval embed on the second turn, got "
            f"{len(counter.retrieval_embeds)}: {counter.texts}"
        )

    def test_blocking_turn_retrieves_once(self, live_services, client):
        counter = live_services.counter
        counter.reset()
        conv_id = _new_conversation(client)
        _post_blocking(client, conv_id)

        assert len(counter.retrieval_embeds) == 1, (
            f"expected 1 retrieval embed, got {len(counter.retrieval_embeds)}: "
            f"{counter.texts}"
        )

    def test_blocking_later_turn_retrieves_once(self, live_services, client):
        counter = live_services.counter
        conv_id = _new_conversation(client)
        _post_blocking(client, conv_id)

        counter.reset()
        _post_blocking(client, conv_id)
        assert len(counter.retrieval_embeds) == 1, (
            f"expected 1 retrieval embed on the second blocking turn, got "
            f"{len(counter.retrieval_embeds)}: {counter.texts}"
        )
        assert counter.calls == 1, (
            f"a later blocking turn should cost exactly one embed, got "
            f"{counter.calls}: {counter.texts}"
        )


class TestOneEmbedPerTurnInTotal:
    """The end state: a turn costs exactly one embed, whatever the turn is.

    FAILING as of this commit, and the failure is the remaining cost, not a
    broken test. With the semantic cache still in the service graph a
    first-substantive turn embeds three times: once for retrieval, once for
    ``lookup`` and once for ``store``. Two of those three are the semantic
    cache, which is measured to never return a hit; retiring it is what makes
    this green. Later turns already satisfy it -- the cache is gated on the
    first substantive turn.
    """

    def test_streaming_first_substantive_turn_embeds_once(self, live_services, client):
        counter = live_services.counter
        counter.reset()
        conv_id = _new_conversation(client)
        _stream_events(client, conv_id)

        assert counter.calls == 1, (
            f"expected 1 embed for one streaming turn, got {counter.calls}: "
            f"{counter.texts}"
        )

    def test_blocking_first_substantive_turn_embeds_once(self, live_services, client):
        counter = live_services.counter
        counter.reset()
        conv_id = _new_conversation(client)
        _post_blocking(client, conv_id)

        assert counter.calls == 1, (
            f"expected 1 embed for one blocking turn, got {counter.calls}: "
            f"{counter.texts}"
        )
