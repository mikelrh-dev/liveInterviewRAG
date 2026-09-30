"""The Context panel must not claim a cache hit was built from the passages.

THE DEFECT
----------
``backend/turns/streaming.py``'s cache path calls
``get_chunks_with_scores(user_text, top_k=2)`` and stores the result on the
turn, purely so the Context panel has something to draw. The answer itself came
from ``response_cache.py``: a fixed string, chosen by a match on the question,
with no RAG anywhere near it. No LLM ran. No context string was built.

And the payload said otherwise. ``turn_done_payload`` sets
``has_context: bool(chunks_used)``, so a cache hit reported ``has_context:
true`` -- the one field whose entire job is "this turn has context". The page
then described those chips as "the passages the answer was actually built
from -- the credibility argument of the whole panel". For roughly 18 of the
most common interview questions that is false, and false in the specific
direction a recruiter would act on.

THE CHOICE, AND WHY IT IS NOT ``has_context: false``
---------------------------------------------------
Setting ``has_context`` to false on a cache hit was the obvious fix and it is
worse. ``has_context`` is not decorative: ``createTurnState.commit`` uses it to
decide whether to refresh the panel, and a false there means NO REQUEST is made
-- which leaves turn N-1's passages standing inside a panel that now belongs to
turn N. The frontend already documents that as "the worst failure this panel is
capable of" (``fetchContext``). So it would trade one lie for a staler one.

The passages really are related to the question -- they were retrieved for it,
they are what ``GET .../context`` returns for the turn, and they are worth
showing. What is false is the word "source". So the payload grows a field that
says which of the two it is, and the page says which of the two it is showing.

WHAT IS ASSERTED HERE
---------------------
* A cache hit does not claim grounding.
* A normal LLM turn still does -- the fix must not weaken the real case.
* An older server that sends no such field is read as grounded, because every
  RAG-grounded turn on such a server IS grounded.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import stub_rag_context_shapes
from backend import conversation
from backend.conversation import (
    GROUNDED,
    RELATED,
    conversations,
    turn_done_payload,
)
from backend.turns import streaming

CACHE_QUESTION = "¿Qué es InterviewTTS?"


def _register(cid: str) -> str:
    conversations[cid] = {
        "id": cid, "messages": [], "turns": [], "summary": "",
        "created_at": "", "last_activity_at": "",
    }
    return cid


def _container(question, tokens, chunks):
    stt = MagicMock()
    stt.transcribe.return_value = question

    def stream_with_context(*args, **kwargs):
        def generate():
            for token in tokens:
                yield token
        return (generate(), [])

    llm = MagicMock()
    # A MagicMock, not a plain function, so "the LLM was never called" is an
    # assertion rather than an AttributeError.
    llm.generate_stream_with_context = MagicMock(side_effect=stream_with_context)

    rag = MagicMock()
    rag.get_chunks_with_scores.return_value = chunks
    # retrieve_with_context delegates to get_chunks_with_scores, so the LLM
    # path sees the same chunks the cache path does. A test that passed because
    # the LLM turn retrieved nothing would prove nothing about grounding.
    stub_rag_context_shapes(rag)

    tts = MagicMock()

    async def sentence(text, sentence_id=0, output_dir=None, **kwargs):
        return sentence_id, Path(f"sentence_{sentence_id}.mp3")

    async def whole(text, output_path=None, **kwargs):
        return output_path

    tts.synthesize_sentence = sentence
    tts.synthesize = whole

    store = MagicMock()
    store.is_enabled.return_value = True
    # Echo the turn back, as the real store does: `persist_turn` treats the
    # return value as authoritative and `turn_done_payload` reads `n` and
    # `chunks_used` off it. A bare MagicMock answers both with mocks, so every
    # payload assertion below would be reading a lie.
    store.record_turn = MagicMock(side_effect=lambda cid, turn, message: turn)

    container = MagicMock()
    container.stt_service.return_value = stt
    container.llm_service.return_value = llm
    container.rag_pipeline.return_value = rag
    container.persistence.return_value = store
    container.tts_service.return_value = tts
    return container


def _drain(question, tokens, chunks, tmp_path, cid) -> dict:
    container = _container(question, tokens, chunks)
    staged = tmp_path / "in.wav"
    staged.write_bytes(b"RIFF")

    async def go():
        with patch.object(streaming, "container", container), patch.object(
            conversation, "container", container
        ):
            return [
                event async for event in streaming.build_stream(cid, staged)
            ]

    return {"container": container, "events": asyncio.run(go())}


def _done_payload(events) -> dict:
    dones = [e for e in events if '"event": "done"' in e]
    assert len(dones) == 1, f"expected one terminal event, got {dones}"
    return json.loads(dones[0].split("data: ", 1)[1].strip())["data"]


# ─── The payload ────────────────────────────────────────────────────────────


class TestACacheHitDoesNotClaimGrounding:
    def test_it_does_not_say_grounded(self, tmp_path):
        cid = _register("ground-cache-hit")
        try:
            result = _drain(
                CACHE_QUESTION, [], [{"text": "x", "source": "wiki/a.md", "score": 0.4}],
                tmp_path, cid,
            )
        finally:
            conversations.pop(cid, None)

        payload = _done_payload(result["events"])
        assert payload.get("has_context") is True, (
            "the panel has passages to show, so has_context should stay true: "
            f"{payload}"
        )
        # Non-vacuous: an absent field would satisfy `!= "grounded"`, and an
        # absent field is a page that cannot say which of the two it is showing.
        assert "context_grounding" in payload, payload
        assert payload["context_grounding"] != "grounded", (
            "a cache hit reports the passages it retrieved as the answer's "
            f"source, which no RAG produced: {payload}"
        )

    def test_it_says_related_instead(self, tmp_path):
        cid = _register("ground-related")
        try:
            result = _drain(
                CACHE_QUESTION, [], [{"text": "x", "source": "wiki/a.md", "score": 0.4}],
                tmp_path, cid,
            )
        finally:
            conversations.pop(cid, None)

        assert _done_payload(result["events"]).get("context_grounding") == "related"

    def test_the_llm_really_was_never_called(self, tmp_path):
        """The premise of every other assertion in this class.

        Without this, a test proving the payload is honest about a cache hit is
        also passing because the cache missed and it was an ordinary turn.
        """
        cid = _register("ground-premise")
        try:
            result = _drain(
                CACHE_QUESTION, [], [{"text": "x", "source": "wiki/a.md", "score": 0.4}],
                tmp_path, cid,
            )
        finally:
            conversations.pop(cid, None)

        llm = result["container"].llm_service.return_value
        llm.generate_stream_with_context.assert_not_called()

    def test_no_grounding_claim_without_passages(self, tmp_path):
        """The field is absent, not "related", when there is nothing to show."""
        cid = _register("ground-no-chunks")
        try:
            result = _drain(CACHE_QUESTION, [], [], tmp_path, cid)
        finally:
            conversations.pop(cid, None)

        payload = _done_payload(result["events"])
        assert payload["has_context"] is False
        assert "context_grounding" not in payload, (
            f"grounding is a claim about passages, and there are none: {payload}"
        )


class TestAnLLMTurnStillClaimsGrounding:
    def test_it_is_grounded(self, tmp_path):
        cid = _register("ground-llm")
        try:
            result = _drain(
                "hablame de un proyecto raro", ["Uno. ", "Dos. "],
                [{"text": "x", "source": "wiki/b.md", "score": 0.6}],
                tmp_path, cid,
            )
        finally:
            conversations.pop(cid, None)

        payload = _done_payload(result["events"])
        assert payload["has_context"] is True, payload
        assert payload.get("context_grounding") == "grounded", (
            f"a turn the RAG really did ground is not claiming it: {payload}"
        )


class TestThePayloadBuilder:
    def test_grounded_is_the_default(self):
        """Every existing caller keeps claiming what it has always claimed."""
        turn = {"n": 0, "chunks_used": [{"text": "x"}]}
        assert turn_done_payload(turn)["context_grounding"] == "grounded"

    def test_related_is_opt_in(self):
        turn = {"n": 0, "chunks_used": [{"text": "x"}]}
        assert (
            turn_done_payload(turn, grounded=False)["context_grounding"] == "related"
        )

    def test_nothing_stored_means_no_claim_at_all(self):
        """The empty payload is "no turn here"; a provenance field would lie."""
        assert turn_done_payload(None, grounded=False) == {}

    def test_the_turn_with_no_chunks_claims_nothing(self):
        turn = {"n": 0, "chunks_used": []}
        assert "context_grounding" not in turn_done_payload(turn)
        assert "context_grounding" not in turn_done_payload(turn, grounded=False)

    def test_it_survives_the_farewell(self, tmp_path):
        """`interview_end` carries the same payload builder.

        A farewell stores no chunks, so it must claim nothing -- but it must
        still terminate normally rather than lose the field and crash.
        """
        cid = _register("ground-farewell")
        try:
            result = _drain("adiós, muchas gracias", [], [], tmp_path, cid)
        finally:
            conversations.pop(cid, None)

        ends = [e for e in result["events"] if '"event": "interview_end"' in e]
        assert len(ends) == 1, f"the farewell did not terminate the stream: {result['events']}"
        payload = json.loads(ends[0].split("data: ", 1)[1].strip())["data"]
        assert "context_grounding" not in payload, payload


@pytest.mark.parametrize("value", [True, False])
def test_the_two_known_grounding_values(value):
    """One vocabulary, quoted on both sides of the wire.

    The page reads this field to decide what to SAY, so a third value means the
    two sides disagree about the words. The builder produces exactly one of the
    two exported names and nothing else -- which is what a Node-side test in
    ``tests/frontend/cache_hit_grounding.test.mjs`` can pin against.
    """
    turn = {"n": 0, "chunks_used": [{"text": "x"}]}
    produced = turn_done_payload(turn, grounded=value)["context_grounding"]
    assert produced in {GROUNDED, RELATED}, (
        f"the builder produced {produced!r}, which is not one of the two names "
        f"the page knows: {GROUNDED!r}, {RELATED!r}"
    )
    assert (produced == GROUNDED) is bool(value)


def test_the_vocabulary_is_exported_for_the_other_side_to_quote():
    """A shared name, so the page's list and the server's cannot drift apart."""
    assert GROUNDED == "grounded"
    assert RELATED == "related"
