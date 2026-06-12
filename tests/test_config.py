"""Tests for configuration module."""

import os
from unittest.mock import patch

from backend.config import Config


def test_config_defaults():
    """Config loads with sensible defaults."""
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


def test_config_paths():
    """Config paths are resolved correctly."""
    cfg = Config()
    assert cfg.BASE_DIR.name == "InterviewTTS"
    assert cfg.CANDIDATE_DIR.name == "candidate"
    assert cfg.AUDIO_DIR.name == "audio"
    assert cfg.FRONTEND_DIR.name == "frontend"
