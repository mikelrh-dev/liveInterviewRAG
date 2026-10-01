"""In-process session state: the conversation record and the rate-limit buckets.

Two stores live here because they share a lifecycle. Both are process-local,
both are bounded only by time, and both are swept by the same background task
(``backend.maintenance.periodic_cleanup``): ``conversations`` by
``SESSION_TTL_HOURS``, ``_rate_limit_store`` by the rate-limit window.

The conversation record is the candidate's memory. It holds the rolling summary,
the last few turns in full, and the RAG chunks per turn that the Context panel
reads back. Nothing here is authoritative: every turn is also written through to
SQLite, and an unknown id hydrates from there.
"""

import asyncio
import logging
from datetime import datetime

from fastapi import HTTPException

from backend import container

logger = logging.getLogger(__name__)

# In-memory conversation store
conversations: dict[str, dict] = {}

# Rate limiting store: {ip: [timestamps]}
_rate_limit_store: dict[str, list] = {}

MAX_SUMMARY_CHARS = 1500  # ~300 tokens for the rolling summary
MAX_TURN_TEXT_CHARS = 200  # Truncate each turn's text in the prompt

#: The two answers to "what are these passages, to the answer that is on
#: screen?". Exported because the page has to draw the same distinction and
#: a third spelling on either side would silently become "assume grounded".
GROUNDED = "grounded"
RELATED = "related"


async def get_conversation_or_hydrate(conversation_id: str) -> dict:
    """Return the conversation from memory, hydrating it from the DB on miss.

    Load-on-demand hydration (design D5): a persisted-but-unknown cid is
    rebuilt into ``conversations`` so the interview continues seamlessly
    after a restart. Unknown-and-unpersisted ids still raise 404, exactly
    as the pre-change bare guards did.
    """
    conv = conversations.get(conversation_id)
    if conv is not None:
        return conv

    persisted = await asyncio.to_thread(
        container.persistence().load_conversation, conversation_id
    )
    if persisted is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversations[conversation_id] = persisted
    logger.info(
        "Hydrated conversation %s from persistent store (%d turns)",
        conversation_id,
        len(persisted.get("turns", [])),
    )
    return persisted


def update_conversation_summary(conversation_id: str, new_turn: dict) -> None:
    """Append a compressed entry for the new turn to the rolling summary.

    Older entries are dropped if the summary exceeds MAX_SUMMARY_CHARS.
    """
    if conversation_id not in conversations:
        return

    summary = conversations[conversation_id].get("summary", "")
    user_brief = (new_turn.get("user_text") or "")[:80]
    assist_brief = (new_turn.get("assistant_text") or "")[:120]
    new_line = f"- P: {user_brief} → R: {assist_brief}\n"

    combined = summary + new_line
    if len(combined) > MAX_SUMMARY_CHARS:
        # Drop oldest lines until it fits, keep at least the most recent
        lines = combined.split("\n")
        while len("\n".join(lines)) > MAX_SUMMARY_CHARS and len(lines) > 1:
            lines.pop(0)
        combined = (
            "[Resumen — turnos más antiguos omitidos por longitud]\n" + "\n".join(lines)
        )

    conversations[conversation_id]["summary"] = combined


def build_conversation_context(conversation_id: str, recent_count: int = 3) -> str:
    """Build the conversation history to inject into the system prompt.

    Combines:
    - Rolling summary of older turns (compressed)
    - Recent turns in full text (truncated to MAX_TURN_TEXT_CHARS)

    Returns empty string if no turns exist.
    """
    if conversation_id not in conversations:
        return ""

    turns = conversations[conversation_id].get("turns", [])
    if not turns:
        return ""

    summary = conversations[conversation_id].get("summary", "")
    recent = turns[-recent_count:] if len(turns) >= recent_count else turns
    older_count = len(turns) - len(recent)

    parts = []
    if summary and older_count > 0:
        parts.append(f"[Resumen de la conversación — {older_count} turnos anteriores]")
        parts.append(summary)
    if recent:
        parts.append(f"\n[Últimos {len(recent)} turnos — texto completo]")
        for turn in recent:
            user_t = (turn.get("user_text") or "")[:MAX_TURN_TEXT_CHARS]
            assist_t = (turn.get("assistant_text") or "")[:MAX_TURN_TEXT_CHARS]
            parts.append(f"- P: {user_t}")
            parts.append(f"  R: {assist_t}")

    return "\n".join(parts)


# ─── Writing a completed turn ───────────────────────────


def build_turn(
    conversation_id: str,
    user_text: str,
    response_text: str,
    chunks_used: list,
    audio_url: str,
    *,
    incomplete: bool = False,
) -> tuple[dict, dict]:
    """Build the ``(turn, message)`` pair a completed exchange is stored as.

    ``n`` is a request, not a fact: it is derived from memory, so two in-flight
    turns on the same conversation ask for the same number. ``persist_turn``
    resolves that against the store and reports the number actually committed.

    ``audio_url`` is a parameter because the two pipelines genuinely differ — a
    single synthesised file names itself, a streamed answer points at the
    conversation's directory of per-sentence chunks, and a farewell whose
    synthesis failed points nowhere at all.

    ``incomplete`` is the mark on an answer the candidate did not get whole. It
    goes on the MESSAGE and not on the turn, because the transcript entry is what
    the report renders and what comes back from the store: a flag on the turn
    would have to be stitched back onto the message list to reach either reader.
    The turn is the exchange; the message is the answer, and it is the answer
    that falls short. It has two causes -- the model stopped generating, or the
    model generated all of it and only part of the audio was delivered -- so
    every reader of the mark renders both, and neither one alone is true of both
    turns.
    """
    turn_number = len(conversations[conversation_id].get("turns", []))
    turn = {
        "n": turn_number,
        "user_text": user_text,
        "assistant_text": response_text,
        "chunks_used": chunks_used,
    }
    message = {
        "user_text": user_text,
        "response_text": response_text,
        "audio_url": audio_url,
        "incomplete": bool(incomplete),
    }
    return turn, message


def touch_activity(conversation_id: str) -> None:
    """Stamp the conversation as active; this is what TTL eviction reads."""
    conversations[conversation_id]["last_activity_at"] = datetime.utcnow().isoformat()


def store_is_configured() -> bool:
    """Whether the installed store is meant to receive writes at all.

    Asked of the service through its public ``is_enabled()`` accessor rather
    than read out of ``config.PERSISTENCE_ENABLED``: ``persistence`` is a
    swappable module global, and the config flag only describes the instance
    built at import time. They agree in production and disagree the moment the
    store is replaced, which is exactly when guessing wrong would silently
    drop turns.

    A store that does not answer the question -- one predating the accessor --
    is treated as **live**, and that is the deliberate reading of "unfamiliar
    store is configured", not a side effect of a default argument. The two
    directions of error are not symmetric: answering "disabled" for a store
    that is really live leaves memory claiming a turn that never reached disk,
    a divergence that stays invisible until a restart forgets the exchange.
    Answering "live" only costs a turn in the one corner case no real
    implementation occupies -- a store that is simultaneously unfamiliar and
    genuinely switched off.

    Previously this fell back to ``config.PERSISTENCE_ENABLED``, which
    contradicted the docstring above it: with persistence off in the
    environment the code took the "switched off" branch the prose said it
    would not.
    """
    store = container.persistence()
    is_enabled = getattr(store, "is_enabled", None)
    if not callable(is_enabled):
        return True
    return bool(is_enabled())


def turn_done_payload(
    committed_turn: dict | None, *, incomplete: bool = False, grounded: bool = True
) -> dict:
    """``done`` payload for a turn that reached disk.

    The turn number is the server's to report. The pipeline derives ``n`` from
    memory, so the number the client must ask the Context panel about is
    whatever was committed, not whatever was requested — reporting the
    provisional one would put the original bug on the wire.

    ``has_context`` exists so the client can tell "this turn has context"
    from "I do not know yet": the first means show something, the second would
    mean firing a request that 404s.

    An empty payload means no turn was stored, so the client must not ask
    about one.

    ``incomplete`` is passed in rather than read off the committed turn,
    because it is a property of the ANSWER and not of the exchange: the caller
    is the only one that knows the model stopped generating, or that synthesis
    dropped part of an answer it finished. It is absent on a finished turn rather
    than sent as ``false``, so a client can tell "this answer was not delivered
    whole" from "this build says nothing about it" instead of reading an explicit
    false as a report that the answer is whole.

    ``grounded`` is the provenance of the chunks, and it is the field that used
    to be missing. ``has_context`` answers "are there passages"; it never
    answered "did the RAG write this answer", so a cache hit -- which retrieves
    passages purely to draw the panel and answers from a fixed string in
    ``response_cache.py``, with no LLM and no context string anywhere near it --
    reported ``has_context: true`` and the page called those passages "the
    passages the answer was actually built from". For roughly 18 of the most
    common interview questions that was false, and false in the direction a
    recruiter acts on.

    Setting ``has_context`` to false instead was considered and rejected: the
    page uses it to decide whether to REFRESH the panel, so a false there makes
    no request and leaves the previous turn's passages standing inside a panel
    that now belongs to this one -- which the frontend already documents as the
    worst failure it is capable of. The passages really are related to the
    question, and they are worth showing; what is false is the word "source".

    The field is ABSENT, not ``grounded``, on a turn with no chunks and on an
    empty payload. It is a claim about passages, so with no passages there is
    nothing to claim; and an absent field on an older server reads as grounded
    by the page, which is correct for every RAG-written answer it ever sent.
    """
    if committed_turn is None:
        return {}
    has_context = bool(committed_turn.get("chunks_used") or [])
    payload = {
        "n": int(committed_turn["n"]),
        "has_context": has_context,
    }
    if has_context:
        payload["context_grounding"] = GROUNDED if grounded else RELATED
    if incomplete:
        payload["incomplete"] = True
    return payload


async def persist_turn(
    conversation_id: str, new_turn: dict, new_message: dict
) -> dict | None:
    """Write a turn through to the DB, then make memory match what landed.

    ``n`` is a request, not a fact. The pipeline derives it from
    ``len(conversations[cid]["turns"])``, so two in-flight turns on the same
    conversation ask for the same one. ``record_turn`` resolves that (same
    content = idempotent retry, different content = commit at the next free
    ``n``) and reports the turn it actually committed. Appending the provisional
    turn and discarding that return value left memory claiming a number the DB
    never used, so the Context panel for that turn 404s forever.

    A ``None`` return means nothing reached disk, so memory is left untouched —
    a turn that is not stored must not look stored. The one exception is a
    deliberately disabled store, where nothing was ever meant to hit disk and
    memory is the only record there is; conflating that with a failure would
    silently discard every turn of a DB-less deployment.

    Returns the committed turn, or ``None`` when it was not stored. The
    response the client is already receiving is built from local variables, so
    dropping the turn costs nothing on the wire.
    """
    committed = await asyncio.to_thread(
        container.persistence().record_turn, conversation_id, new_turn, new_message
    )

    if committed is None:
        if store_is_configured():
            logger.warning(
                "Turn write failed for %s — left out of memory so memory and "
                "disk agree",
                conversation_id,
            )
            return None
        # Store deliberately off: memory is the record of record.
        committed = new_turn

    conv = conversations[conversation_id]
    conv["turns"].append(committed)
    update_conversation_summary(conversation_id, committed)
    conv["messages"].append(new_message)
    return committed
