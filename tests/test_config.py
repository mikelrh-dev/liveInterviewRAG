"""Tests for configuration module."""

import os
from unittest.mock import patch

from backend.config import Config


def test_config_defaults():
    """Config loads with sensible defaults."""
    with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
        cfg = Config()
    assert cfg.WHISPER_MODEL == "tiny"
    assert cfg.WHISPER_DEVICE == "cpu"
    assert cfg.WHISPER_COMPUTE_TYPE == "int8"
    assert cfg.TTS_VOICE == "es-ES-AlvaroNeural"
    assert cfg.RAG_TOP_K == 3
    assert cfg.CHUNK_SIZE == 400
    assert cfg.CHUNK_OVERLAP == 50
    assert cfg.RATE_LIMIT_PER_MINUTE == 10
    assert cfg.MAX_AUDIO_DURATION == 30
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
    """Config paths are resolved correctly."""
    cfg = Config()
    assert cfg.BASE_DIR.name == "InterviewTTS"
    assert cfg.CANDIDATE_DIR.name == "candidate"
    assert cfg.AUDIO_DIR.name == "audio"
    assert cfg.FRONTEND_DIR.name == "frontend"
