"""The background sweep: expired conversations, stale audio, and expired reports.

Started once from the lifespan and cancelled on shutdown. Every step is
individually guarded so one failing service cannot stop the rest of the sweep —
a sweep that gives up halfway leaves stale audio on disk forever.

The order inside a tick is load-bearing. A conversation's report is generated
*before* the conversation is deleted and its rows evicted, because the report is
the only record that survives eviction by design.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

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


async def periodic_cleanup(interval_seconds: int) -> None:
    """Periodic background task: evict stale conversations, prune rate-limit store, clean audio."""
    # Initial 30s delay so first cleanup doesn't fire during first request
    await asyncio.sleep(30)
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(hours=config.SESSION_TTL_HOURS)
            stale_ids = [
                cid
                for cid, c in conversations.items()
                if datetime.fromisoformat(c.get("last_activity_at", "")) < cutoff
            ]
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
                del conversations[cid]
                # Remove the DB rows too; reports row survives by design (D6)
                try:
                    await asyncio.to_thread(
                        container.persistence().evict_conversation, cid
                    )
                except Exception as e:
                    logger.warning("DB eviction failed for %s: %s", cid, e)
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
            container.cleanup_stale_audio()()
        except Exception as e:
            logger.error("Audio cleanup failed: %s", e)
        try:
            container.report_service().cleanup_expired()
        except Exception as e:
            logger.error("Report cleanup failed: %s", e)
        try:
            pruned_rows = container.persistence().prune_reports(
                config.REPORT_RETENTION_DAYS
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
