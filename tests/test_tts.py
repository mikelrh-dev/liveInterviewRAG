"""Tests for TTS service with mocked Edge TTS."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from backend.services.edge_tts_client import EdgeTTSClient
from backend.services.tts import TTSService


class TestTTSService:
    """Tests for Edge TTS wrapper."""

    def test_init(self):
        """TTS service initializes with voice and output dir."""
        svc = TTSService(voice="en-US-GuyNeural", output_dir="test_audio")
        assert svc.voice == "en-US-GuyNeural"
        assert svc.output_dir.name == "test_audio"
        # Cleanup
        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_synthesize_empty_text(self):
        """Synthesize raises on empty text."""
        svc = TTSService(output_dir="test_audio_out")
        with pytest.raises(ValueError, match="empty text"):
            await svc.synthesize("")
        with pytest.raises(ValueError, match="empty text"):
            await svc.synthesize("   ")
        # Cleanup
        if svc.output_dir.exists():
            svc.output_dir.rmdir()

    @pytest.mark.asyncio
    @patch("backend.services.edge_tts_client.edge_tts.Communicate")
    async def test_synthesize_success(self, mock_communicate_cls):
        """Synthesize creates audio file and returns path."""
        # Make the mock actually create the file
        async def fake_save(path):
            Path(path).touch()
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock(side_effect=fake_save)
        mock_communicate_cls.return_value = mock_communicate

        svc = TTSService(output_dir="test_audio_out")
        result = await svc.synthesize("Hello, I am Mikel.")

        assert result.exists()
        assert result.suffix == ".mp3"
        mock_communicate.save.assert_called_once()

        # Cleanup
        result.unlink()
        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    @patch("backend.services.edge_tts_client.edge_tts.Communicate")
    async def test_synthesize_custom_path(self, mock_communicate_cls):
        """Synthesize respects custom output path."""
        async def fake_save(path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).touch()
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock(side_effect=fake_save)
        mock_communicate_cls.return_value = mock_communicate

        svc = TTSService(output_dir="test_audio_out")
        custom_path = svc.output_dir / "custom_response.mp3"
        result = await svc.synthesize("Hello", output_path=custom_path)

        assert result == custom_path
        assert result.exists()

        # Cleanup
        result.unlink()
        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    @patch("backend.services.edge_tts_client.edge_tts.Communicate")
    async def test_synthesize_failure(self, mock_communicate_cls):
        """Synthesize raises on Edge TTS failure."""
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock(side_effect=Exception("Network error"))
        mock_communicate_cls.return_value = mock_communicate

        svc = TTSService(output_dir="test_audio_out")
        with pytest.raises(RuntimeError, match="Could not synthesize"):
            await svc.synthesize("Hello")

        # Cleanup
        if svc.output_dir.exists():
            svc.output_dir.rmdir()

    def test_get_audio_url(self):
        """Audio URL is formatted correctly."""
        svc = TTSService(output_dir="test_audio_out")
        url = svc.get_audio_url(Path("abc123.mp3"))
        assert url == "/audio/abc123.mp3"
        # Cleanup
        svc.output_dir.rmdir()


class TestEdgeTTSClient:
    """Tests for the extracted EdgeTTSClient."""

    def test_init(self):
        """Client stores the configured voice."""
        client = EdgeTTSClient(voice="es-ES-AlvaroNeural")
        assert client.voice == "es-ES-AlvaroNeural"

    @pytest.mark.asyncio
    @patch("backend.services.edge_tts_client.edge_tts.Communicate")
    async def test_synthesize_writes_file(self, mock_communicate_cls):
        """Synthesize writes audio through edge_tts and returns the path."""
        async def fake_save(path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).touch()
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock(side_effect=fake_save)
        mock_communicate_cls.return_value = mock_communicate

        client = EdgeTTSClient(voice="es-ES-AlvaroNeural")
        out = Path("test_audio_el/out.mp3")
        result = await client.synthesize("Hola", out)

        assert result == out
        assert out.exists()
        mock_communicate.save.assert_called_once_with(str(out))

        out.unlink()
        out.parent.rmdir()

    @pytest.mark.asyncio
    @patch("backend.services.edge_tts_client.edge_tts.Communicate")
    async def test_synthesize_failure_raises_runtime_error(self, mock_communicate_cls):
        """Synthesis failure is wrapped in RuntimeError."""
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock(side_effect=Exception("Network error"))
        mock_communicate_cls.return_value = mock_communicate

        client = EdgeTTSClient(voice="es-ES-AlvaroNeural")
        with pytest.raises(RuntimeError, match="Could not synthesize"):
            await client.synthesize("Hola", Path("test_audio_el/out.mp3"))
