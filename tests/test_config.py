"""Tests for configuration module."""

import os
from pathlib import Path
from unittest.mock import patch

from backend import config
from backend.config import Config


def test_config_defaults(monkeypatch):
    """Config loads with sensible defaults."""
    # Ensure WHISPER_MODEL is absent so we test the actual default,
    # not whatever the operator has in their .env (which load_dotenv
    # from backend.main may have injected into os.environ).
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    # Same reason, same trap. A local .env carrying MAX_AUDIO_DURATION=30 --
    # which is exactly what the value was before it became a real limit -- was
    # reported here as a failing default rather than as an operator override,
    # because the test could not tell the two apart.
    monkeypatch.delenv("MAX_AUDIO_DURATION", raising=False)
    with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
        cfg = Config()
    assert cfg.WHISPER_MODEL == "small"
    assert cfg.WHISPER_DEVICE == "cpu"
    assert cfg.WHISPER_COMPUTE_TYPE == "int8"
    assert cfg.TTS_VOICE == "es-ES-AlvaroNeural"
    assert cfg.RAG_TOP_K == 3
    assert cfg.CHUNK_SIZE == 400
    assert cfg.CHUNK_OVERLAP == 50
    assert cfg.RATE_LIMIT_PER_MINUTE == 10
    assert cfg.MAX_AUDIO_DURATION == 60
    assert cfg.GOOGLE_API_KEY == ""
    assert cfg.GOOGLE_MODEL == "gemini-3.1-flash-lite"


def test_config_env_override():
    """Config respects environment variable overrides."""
    with patch.dict(os.environ, {"WHISPER_MODEL": "small", "RAG_TOP_K": "5"}):
        cfg = Config()
        assert cfg.WHISPER_MODEL == "small"
        assert cfg.RAG_TOP_K == 5


def test_session_ttl_default():
    """SESSION_TTL_HOURS defaults to 2 when not set."""
    with patch.dict(os.environ, {}, clear=False):
        cfg = Config()
    assert cfg.SESSION_TTL_HOURS == 2


def test_audio_cleanup_interval_default():
    """AUDIO_CLEANUP_INTERVAL_MIN defaults to 30 when not set."""
    with patch.dict(os.environ, {}, clear=False):
        cfg = Config()
    assert cfg.AUDIO_CLEANUP_INTERVAL_MIN == 30


def test_session_ttl_floor_enforced(caplog):
    """SESSION_TTL_HOURS below floor 0.1 defaults to 2 with warning."""
    import logging
    caplog.set_level(logging.WARNING)
    with patch.dict(os.environ, {"SESSION_TTL_HOURS": "0.05"}, clear=False):
        cfg = Config()
    assert cfg.SESSION_TTL_HOURS == 2
    assert "SESSION_TTL_HOURS" in caplog.text
    assert "below floor" in caplog.text


def test_session_ttl_respects_normal_value():
    """SESSION_TTL_HOURS above floor is used as-is."""
    with patch.dict(os.environ, {"SESSION_TTL_HOURS": "1.5"}, clear=False):
        cfg = Config()
    assert cfg.SESSION_TTL_HOURS == 1.5


def test_config_paths():
    """The derived paths are anchored at the repository root, whatever it is called.

    This used to assert ``cfg.BASE_DIR.name == "InterviewTTS"``, which asserts
    the name of the directory the repository happens to be checked out into. It
    passed on the author's machine and failed everywhere else:
    ``actions/checkout`` names the workspace after the REPOSITORY, and the remote
    is ``mikelrh-dev/liveInterviewRAG``, so ``cfg.BASE_DIR.name`` is
    ``liveInterviewRAG`` in CI. The workflow documented that as "the one known
    failure on this platform" and shipped it.

    A directory name is not a property of this code. What is a property of this
    code is that BASE_DIR is the repository root -- the parent of the ``backend``
    package ``config.py`` lives in -- and that the three derived directories are
    fixed names directly inside it. That is what is asserted now, and it is
    asserted by construction: BASE_DIR is compared against
    ``Path(config.__file__).resolve().parent.parent``, which is true in a
    directory called InterviewTTS, in one called liveInterviewRAG, and in one
    called anything else.
    """
    cfg = Config()
    package_dir = Path(config.__file__).resolve().parent

    # BASE_DIR is the repository root: the parent of the backend package.
    assert cfg.BASE_DIR == package_dir.parent
    assert cfg.BASE_DIR.is_dir()
    # And it is recognisably the repository root, not merely some parent.
    assert (cfg.BASE_DIR / "backend" / "config.py").is_file()
    assert (cfg.BASE_DIR / "pyproject.toml").is_file()

    # The three derived names are properties of config.py, not of the checkout.
    assert cfg.CANDIDATE_DIR.name == "candidate"
    assert cfg.AUDIO_DIR.name == "audio"
    assert cfg.FRONTEND_DIR.name == "frontend"

    # Each one is a direct child of the root, resolved to an absolute path.
    for derived in (cfg.CANDIDATE_DIR, cfg.AUDIO_DIR, cfg.FRONTEND_DIR):
        assert derived == cfg.BASE_DIR / derived.name
        assert derived.parent == cfg.BASE_DIR
        assert derived.is_absolute()
