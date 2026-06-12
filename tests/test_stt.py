"""Tests for STT service with mocked external APIs."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services.stt import STTService


class TestSTTService:
    """Tests for Faster Whisper STT wrapper."""

    def test_init_defaults(self):
        """STT service initializes with default parameters."""
        svc = STTService()
        assert svc.model_name == "tiny"
        assert svc.device == "cpu"
        assert svc.compute_type == "int8"
        assert not svc.is_loaded

    @patch("faster_whisper.WhisperModel")
    def test_load_model_success(self, mock_whisper_cls):
        """Model loads successfully."""
        svc = STTService(model_name="tiny")
        svc.load_model()

        mock_whisper_cls.assert_called_once_with(
            "tiny", device="cpu", compute_type="int8"
        )
        assert svc.is_loaded

    @patch("faster_whisper.WhisperModel")
    def test_transcribe_success(self, mock_whisper_cls):
        """Transcription returns text from audio file."""
        # Setup mock
        mock_model = MagicMock()
        mock_segment = MagicMock()
        mock_segment.text = " Hello, this is a test. "
        mock_info = MagicMock()
        mock_info.duration = 2.5
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        mock_whisper_cls.return_value = mock_model

        svc = STTService()
        svc.load_model()

        # Create a temp audio file
        audio_file = Path("test_audio.wav")
        audio_file.touch()
        try:
            result = svc.transcribe(audio_file)
            assert result == "Hello, this is a test."
            mock_model.transcribe.assert_called_once()
        finally:
            audio_file.unlink()

    def test_transcribe_without_model_raises(self):
        """Transcription raises if model not loaded."""
        svc = STTService()
        with pytest.raises(RuntimeError, match="not loaded"):
            svc.transcribe(Path("nonexistent.wav"))

    def test_transcribe_missing_file_raises(self):
        """Transcription raises for missing audio file."""
        svc = STTService()
        svc._model = MagicMock()  # Simulate loaded model
        with pytest.raises(FileNotFoundError):
            svc.transcribe(Path("nonexistent.wav"))

    @patch("faster_whisper.WhisperModel")
    def test_transcribe_api_error(self, mock_whisper_cls):
        """Transcription raises RuntimeError on API failure."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = Exception("CUDA out of memory")
        mock_whisper_cls.return_value = mock_model

        svc = STTService()
        svc.load_model()

        audio_file = Path("test_audio.wav")
        audio_file.touch()
        try:
            with pytest.raises(RuntimeError, match="Could not transcribe"):
                svc.transcribe(audio_file)
        finally:
            audio_file.unlink()
