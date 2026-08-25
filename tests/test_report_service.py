"""Tests for ReportService and report-related configuration.

Covers openspec change `conversation-reports`:
- Config: REPORTS_DIR / REPORT_RETENTION_DAYS env overrides + defaults
- ReportService unit behavior (design §8 test table)
- main.py hook wiring (farewell branch + TTL eviction ordering)
"""

import logging
import os
import time
from contextlib import suppress
from pathlib import Path

import pytest


def _state(n_turns=2):
    """Conversation state shaped exactly like create_conversation in main.py."""
    msgs = [
        {
            "user_text": f"pregunta {i}",
            "response_text": f"respuesta {i}",
            "audio_url": "",
        }
        for i in range(1, n_turns + 1)
    ]
    return {
        "messages": msgs,
        "created_at": "2026-08-25T10:00:00",
        "last_activity_at": "2026-08-25T10:05:30",
    }


@pytest.fixture
def svc(tmp_path):
    from backend.services.report import ReportService

    return ReportService(output_dir=tmp_path / "reports", retention_days=30)


# ─── Phase 1 — Config settings ───────────────────────────


class TestConfigReportSettings:
    """REPORTS_DIR / REPORT_RETENTION_DAYS follow the RAG_CACHE_DIR pattern."""

    def test_reports_dir_default_is_base_dir_reports(self):
        from backend.config import Config

        cfg = Config()
        assert cfg.REPORTS_DIR == cfg.BASE_DIR / "reports"
        assert isinstance(cfg.REPORTS_DIR, Path)

    def test_reports_dir_env_override(self, monkeypatch, tmp_path):
        from backend.config import Config

        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "elsewhere"))
        cfg = Config()
        assert Path(tmp_path / "elsewhere") == cfg.REPORTS_DIR

    def test_report_retention_days_default_30(self):
        from backend.config import Config

        assert Config().REPORT_RETENTION_DAYS == 30

    def test_report_retention_days_env_override(self, monkeypatch):
        from backend.config import Config

        monkeypatch.setenv("REPORT_RETENTION_DAYS", "7")
        assert Config().REPORT_RETENTION_DAYS == 7


# ─── Phase 2 — ReportService unit behavior (design §8) ───────────


class TestReportServiceGenerate:
    def test_generate_writes_markdown_file(self, svc, tmp_path):
        path = svc.generate("abc", _state())
        assert isinstance(path, Path)
        assert path.exists()
        assert path.parent == tmp_path / "reports" / "abc"
        assert path.suffix == ".md"

    def test_report_header_fields(self, svc):
        path = svc.generate("abc", _state(n_turns=3))
        content = path.read_text(encoding="utf-8")
        assert "**Fecha:** 2026-08-25" in content
        assert "**Duración:** 00:05:30" in content
        assert "**Turnos:** 3" in content

    def test_full_transcript_rendering(self, svc):
        state = {
            "messages": [
                {
                    "user_text": "¿Qué tecnologías dominas?",
                    "response_text": "Domino Python, FastAPI y más.",
                    "audio_url": "",
                },
                {
                    "user_text": "Háblame de tu experiencia",
                    "response_text": "Trabajé cinco años en desarrollo web.",
                    "audio_url": "",
                },
            ],
            "created_at": "2026-08-25T10:00:00",
            "last_activity_at": "2026-08-25T10:01:00",
        }
        path = svc.generate("abc", state)
        content = path.read_text(
            encoding="utf-8"
        )  # explicit UTF-8: accents must survive
        assert "**Reclutador:** ¿Qué tecnologías dominas?" in content
        assert "**Gemelo:** Domino Python, FastAPI y más." in content
        assert "**Reclutador:** Háblame de tu experiencia" in content
        assert "**Gemelo:** Trabajé cinco años en desarrollo web." in content

    def test_idempotent_regeneration_noop(self, svc):
        first = svc.generate("abc", _state())
        mtime_before = first.stat().st_mtime_ns
        content_before = first.read_text(encoding="utf-8")
        second = svc.generate("abc", _state())
        assert second is None
        assert len(list(first.parent.glob("*.md"))) == 1
        assert first.read_text(encoding="utf-8") == content_before
        assert first.stat().st_mtime_ns == mtime_before

    def test_empty_conversation_skipped(self, svc, tmp_path):
        state = _state()
        state["messages"] = []
        assert svc.generate("abc", state) is None
        assert not (tmp_path / "reports" / "abc").exists()

    def test_none_state_skipped(self, svc, tmp_path):
        assert svc.generate("x", None) is None
        assert not (tmp_path / "reports" / "x").exists()

    def test_write_failure_degrades(self, tmp_path, caplog):
        from backend.services.report import ReportService

        # A file where the cid directory should be forces mkdir to fail
        (tmp_path / "reports").mkdir(parents=True)
        (tmp_path / "reports" / "abc").write_text("blocker")
        svc = ReportService(output_dir=tmp_path / "reports", retention_days=30)
        with caplog.at_level(logging.WARNING, logger="backend.services.report"):
            result = svc.generate("abc", _state())
        assert result is None  # no exception escaped
        assert any("Report generation failed" in r.message for r in caplog.records)


class TestReportServiceCleanup:
    def _seed(self, out_dir, name):
        cid_dir = out_dir / "abc"
        cid_dir.mkdir(parents=True, exist_ok=True)
        f = cid_dir / name
        f.write_text("report", encoding="utf-8")
        return f

    def test_cleanup_expired_deletes_old_only(self, svc, tmp_path):
        out_dir = tmp_path / "reports"
        old = self._seed(out_dir, "old.md")
        fresh = self._seed(out_dir, "fresh.md")
        now = time.time()
        os.utime(old, (now - 40 * 86400, now - 40 * 86400))
        os.utime(fresh, (now, now))
        deleted = svc.cleanup_expired()
        assert deleted == 1
        assert not old.exists()
        assert fresh.exists()

    def test_cleanup_custom_days_override(self, svc, tmp_path):
        out_dir = tmp_path / "reports"
        f = self._seed(out_dir, "fresh.md")
        assert svc.cleanup_expired(days=0) == 1
        assert not f.exists()
        # Default uses constructor retention_days (30): a fresh file survives
        g = self._seed(out_dir, "fresh2.md")
        assert svc.cleanup_expired() == 0
        assert g.exists()

    def test_cleanup_never_raises(self, tmp_path):
        from backend.services.report import ReportService

        svc = ReportService(output_dir=tmp_path / "does-not-exist", retention_days=30)
        assert svc.cleanup_expired() == 0


# ─── Phase 3 — main.py hook wiring (design §5, light integration) ────────────


@pytest.fixture
def main_mod():
    import backend.main as m

    m.conversations.clear()
    yield m
    m.conversations.clear()


class TestFarewellHook:
    def test_detect_farewell_truthy(self, main_mod):
        assert main_mod.detect_farewell("gracias, eso es todo") is True

    def test_farewell_branch_tail_writes_report_including_farewell(
        self, main_mod, monkeypatch, tmp_path
    ):
        cid = "farewelltestcid"
        main_mod.conversations[cid] = {
            "messages": [
                {
                    "user_text": "¿Qué tecnologías usan?",
                    "response_text": "Usamos Python y FastAPI.",
                    "audio_url": "",
                }
            ],
            "created_at": "2026-08-25T10:00:00",
            "last_activity_at": "2026-08-25T10:03:00",
        }
        # Redirect the wired service instance to a temp dir (never real reports/)
        monkeypatch.setattr(main_mod.report_service, "output_dir", tmp_path)

        assert main_mod.detect_farewell("gracias, eso es todo")
        farewell = (
            "¡Gracias a ti! Ha sido un placer. Si tenés más preguntas en el "
            "futuro, acá estoy. ¡Éxito en tu búsqueda!"
        )
        # Simulate the wired branch tail: append farewell exchange, then generate
        main_mod.conversations[cid]["messages"].append(
            {
                "user_text": "gracias, eso es todo",
                "response_text": farewell,
                "audio_url": "",
            }
        )
        result = main_mod.report_service.generate(cid, main_mod.conversations.get(cid))

        assert isinstance(result, Path)
        content = result.read_text(encoding="utf-8")
        # Post-append placement proof: the farewell turn itself is in the report
        assert "gracias, eso es todo" in content
        assert farewell in content


class TestEvictionOrderingHook:
    def test_generate_called_before_delete(self, main_mod, monkeypatch):
        """periodic_cleanup must generate the report while state still exists."""
        import asyncio

        cid = "stalecid"
        main_mod.conversations[cid] = {
            "messages": [
                {"user_text": "hola", "response_text": "hola!", "audio_url": ""}
            ],
            "created_at": "2026-08-25T10:00:00",
            "last_activity_at": "2020-01-01T00:00:00",  # far in the past → stale
        }

        captured = {}

        def fake_generate(conv_id, state):
            captured["state"] = state  # None would mean deleted before generate
            return None

        monkeypatch.setattr(main_mod.report_service, "generate", fake_generate)
        monkeypatch.setattr(main_mod, "cleanup_stale_audio", lambda: None)

        # Break the infinite sweep after the first tick: 2nd sleep raises
        real_sleep = asyncio.sleep
        sleeps = {"n": 0}

        async def fake_sleep(_seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise GeneratorExit
            await real_sleep(0)

        monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)

        with suppress(GeneratorExit, RuntimeError):
            asyncio.run(main_mod.periodic_cleanup(interval_seconds=0))

        assert "state" in captured, "generate() was never called by the sweep"
        assert captured["state"] is not None, (
            "generate ran AFTER del conversations[cid]"
        )
