"""Report generation service for InterviewTTS.

Persists finished conversations as Markdown transcripts under REPORTS_DIR,
one file per conversation id. Purely post-hoc: no LLM calls, no HTTP surface,
and every filesystem failure degrades to a logged warning instead of raising
(so it can never break the SSE stream or the periodic cleanup loop).
"""

import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ReportService:
    """Persists finished conversations as Markdown transcripts under REPORTS_DIR."""

    def __init__(self, output_dir: str | Path, retention_days: int = 30) -> None:
        self.output_dir = Path(output_dir)
        self.retention_days = int(retention_days)

    def generate(
        self, conversation_id: str, conversation_state: dict | None
    ) -> Path | None:
        """Render one conversation to {output_dir}/{cid}/{timestamp}.md.

        Returns the written Path, or None when skipped (already generated /
        empty conversation / write failure). NEVER raises.
        """
        try:
            # Skip order per design §3: None state → empty messages → already generated
            if not conversation_state or not conversation_state.get("messages"):
                return None

            cid_dir = Path(self.output_dir) / conversation_id
            # Idempotency: any existing .md for this cid means first writer won
            if any(cid_dir.glob("*.md")):
                return None

            cid_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H%M%S")
            path = cid_dir / f"{timestamp}.md"

            path.write_text(
                self._render(conversation_id, conversation_state), encoding="utf-8"
            )
            logger.info("Report written: %s", path)
            return path
        except Exception as e:
            logger.warning("Report generation failed for %s: %s", conversation_id, e)
            return None

    def cleanup_expired(self, days: int | None = None) -> int:
        """Delete *.md report files older than `days` (default: self.retention_days).

        Prunes by mtime across the whole reports tree, mirroring
        cleanup_stale_audio. Returns count deleted. NEVER raises.
        """
        window_days = self.retention_days if days is None else days
        deleted = 0
        try:
            cutoff = time.time() - window_days * 86400
            for f in Path(self.output_dir).rglob("*.md"):
                if f.is_file() and f.stat().st_mtime <= cutoff:
                    f.unlink()
                    deleted += 1
                    logger.info("Deleted expired report: %s", f.name)
        except Exception as e:
            logger.warning("Report cleanup failed: %s", e)
        return deleted

    @staticmethod
    def _render(conversation_id: str, state: dict) -> str:
        """Render the exact Markdown format from design §3."""
        created_at = str(state.get("created_at", ""))
        duration = "desconocida"
        try:
            end = datetime.fromisoformat(state["last_activity_at"])
            start = datetime.fromisoformat(created_at)
            total = int((end - start).total_seconds())
            h, m, s = total // 3600, (total % 3600) // 60, total % 60
            duration = f"{h:02d}:{m:02d}:{s:02d}"
        except (KeyError, TypeError, ValueError):
            pass

        lines = [
            f"# Entrevista simulada — {conversation_id}",
            "",
            f"- **Fecha:** {created_at[:10]}",
            f"- **Duración:** {duration}",
            f"- **Turnos:** {len(state['messages'])}",
        ]
        for msg in state["messages"]:
            lines += [
                "",
                "---",
                "",
                f"**Reclutador:** {msg.get('user_text', '')}",
                "",
                f"**Gemelo:** {msg.get('response_text', '')}",
                "",
                "---",
            ]
        return "\n".join(lines) + "\n"
