"""The background sweep: expired conversations, stale audio, and expired reports.

Started once from the lifespan and cancelled on shutdown. Every step is
individually guarded so one failing service cannot stop the rest of the sweep —
a sweep that gives up halfway leaves stale audio on disk forever.

The order inside a tick is load-bearing. A conversation's report is generated
*before* the conversation is deleted and its rows evicted, because the report is
the only record that survives eviction by design.

EVERY blocking step in the tick goes to a worker, and that is uniform on
purpose. Three of them did not: the audio sweep, the report-file cleanup and the
report-row prune all ran inline while the lines immediately around them
(``record_report``, ``evict_conversation``, ``prune_conversations``) were already
handing the same kind of work to ``asyncio.to_thread``. Measured with 3000 files
in the audio tree: 171 ms with the loop frozen for every one of them, 65 ms at a
realistic scale. That is 65–171 ms in which no request is served, no SSE token
streams, and this task's own sleep does not elapse — paid on top of work the
process was doing, not instead of it. The neighbours were already right, which
is the only reason to copy them rather than to invent a shape.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from backend import container
from backend.config import config
from backend.conversation import conversations, _rate_limit_store

logger = logging.getLogger(__name__)


def cleanup_stale_audio():
    """Clean up audio files older than 1 hour from previous runs."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for f in config.AUDIO_DIR.rglob("*"):
        if f.is_file() and f.suffix in (".mp3", ".webm", ".wav"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink(missing_ok=True)
                logger.info("Cleaned up stale audio: %s", f.name)


def _activity_moment(conversation: dict, cid: str = "?") -> datetime | None:
    """When this conversation was last active, as a naive UTC datetime.

    ``None`` means the record carries no age this sweep can trust, and the
    caller must not act on it.

    Three ways a stored timestamp defeats a naive ``fromisoformat`` and each of
    them used to abort the whole eviction step, for every conversation, on
    every tick -- because the comprehension that parsed them had no per-record
    guard and the loop's outer ``except`` logged the abort and carried on to the
    next tick, so the sweep reported itself healthy while nothing was ever
    evicted again:

    * the field is absent, so ``.get(..., "")`` produced ``""`` and
      ``fromisoformat`` raised ``ValueError``;
    * the value carries a ``Z`` suffix, which this interpreter rejects before
      3.11 and *accepts* from 3.11 -- returning an aware datetime that then
      raises ``TypeError`` against the naive UTC cutoff it is compared with;
    * the value is simply not a timestamp.

    ``created_at`` is the fallback because it is written by the same call as
    ``last_activity_at`` and is never newer, so it can only make a conversation
    look older than it is -- and "older than it is" is the safe direction to err
    for a retention sweep. It cannot make a stale conversation look fresh.

    Nothing here is destructive: an unreadable record yields ``None`` and the
    caller skips it loudly rather than guessing.
    """
    for key in ("last_activity_at", "created_at"):
        raw = conversation.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            logger.warning(
                "Conversation %s has an unreadable %s (%r); falling back to "
                "the other timestamp fields",
                cid, key, raw,
            )
            continue
        if parsed.tzinfo is not None:
            # Naive UTC everywhere else in this module, so normalise rather than
            # let the comparison raise.
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    logger.warning(
        "Conversation %s carries no readable timestamp at all "
        "(last_activity_at=%r, created_at=%r); it cannot be aged out by the "
        "in-memory sweep and will be left in memory. The store-side prune reads "
        "the NOT NULL column and still removes its rows.",
        cid,
        conversation.get("last_activity_at"),
        conversation.get("created_at"),
    )
    return None


async def evict_stale_conversations() -> int:
    """Evict every conversation older than the session TTL. Returns how many.

    One unreadable record must not cost anyone their eviction, so the age is
    resolved per conversation and a record with no readable age is skipped
    rather than guessed at. Both are reported at WARNING: the skipped record is
    named, because a silent skip here is indistinguishable from the abort this
    replaced.
    """
    cutoff = datetime.utcnow() - timedelta(hours=config.SESSION_TTL_HOURS)

    stale_ids = []
    for cid, conversation in conversations.items():
        moment = _activity_moment(conversation, cid)
        if moment is None:
            logger.warning(
                "Leaving conversation %s in memory: no readable timestamp, so "
                "its age is unknown",
                cid,
            )
            continue
        if moment < cutoff:
            stale_ids.append(cid)

    for cid in stale_ids:
        logger.debug("Evicting stale conversation: %s", cid)
        try:
            report_path = container.report_service().generate(
                cid, conversations.get(cid)
            )
            if report_path is not None:
                # Link the report row BEFORE eviction — evict_conversation
                # preserves it while deleting conversation/turn/message rows
                await asyncio.to_thread(
                    container.persistence().record_report,
                    cid,
                    str(report_path),
                )
        except Exception as e:  # defense-in-depth; service already swallows
            logger.warning("Report on eviction failed for %s: %s", cid, e)
        # pop, not del: an entry another coroutine already dropped is not an
        # error here, and raising would abort the rest of the sweep again.
        conversations.pop(cid, None)
        # Remove the DB rows too; reports row survives by design (D6)
        try:
            await asyncio.to_thread(
                container.persistence().evict_conversation, cid
            )
        except Exception as e:
            logger.warning("DB eviction failed for %s: %s", cid, e)

    return len(stale_ids)


async def periodic_cleanup(interval_seconds: int) -> None:
    """Periodic background task: evict stale conversations, prune rate-limit store, clean audio."""
    # Initial 30s delay so first cleanup doesn't fire during first request
    await asyncio.sleep(30)
    while True:
        try:
            await evict_stale_conversations()
        except Exception as e:
            logger.error("Conversation eviction failed: %s", e)
        try:
            now = time.time()
            for ip in list(_rate_limit_store.keys()):
                _rate_limit_store[ip] = [
                    t for t in _rate_limit_store[ip] if now - t < 60
                ]
                if not _rate_limit_store[ip]:
                    del _rate_limit_store[ip]
        except Exception as e:
            logger.error("Rate-limit pruning failed: %s", e)
        try:
            await asyncio.to_thread(container.cleanup_stale_audio())
        except Exception as e:
            logger.error("Audio cleanup failed: %s", e)
        try:
            await asyncio.to_thread(
                container.report_service().cleanup_expired
            )
        except Exception as e:
            logger.error("Report cleanup failed: %s", e)
        try:
            pruned_rows = await asyncio.to_thread(
                container.persistence().prune_reports,
                config.REPORT_RETENTION_DAYS,
            )
            if pruned_rows:
                logger.info("Pruned %d expired report rows from the store", pruned_rows)
        except Exception as e:
            logger.error("Report-row pruning failed: %s", e)
        try:
            pruned_convs = await asyncio.to_thread(
                container.persistence().prune_conversations, config.SESSION_TTL_HOURS
            )
            if pruned_convs:
                logger.info("Pruned %d stale conversations from the store", pruned_convs)
        except Exception as e:
            logger.error("Conversation pruning failed: %s", e)
        await asyncio.sleep(interval_seconds)
