"""Tests for TTS service with mocked Edge TTS."""

import httpx
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from backend.services.edge_tts_client import EdgeTTSClient
from backend.services.elevenlabs_client import ElevenLabsClient, ElevenLabsError
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


class TestElevenLabsClient:
    """Tests for ElevenLabsClient with mocked httpx transport."""

    @pytest.mark.asyncio
    @patch("backend.services.elevenlabs_client.httpx.AsyncClient")
    async def test_synthesize_success(self, mock_client_cls):
        """Successful response writes audio bytes and returns output path."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"ID3 audio-bytes"
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        client = ElevenLabsClient(api_key="test-key", voice_id="test-voice", timeout=15)
        out_dir = Path("test_audio_el")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "el.mp3"
        result = await client.synthesize("Hola, soy Mikel.", out_path)

        assert result == out_path
        assert out_path.read_bytes() == b"ID3 audio-bytes"
        # The request must target the voice-specific ElevenLabs endpoint
        assert mock_client.post.call_args.args[0] == \
            "https://api.elevenlabs.io/v1/text-to-speech/test-voice"

        out_path.unlink()
        out_dir.rmdir()

    @pytest.mark.asyncio
    @patch("backend.services.elevenlabs_client.httpx.AsyncClient")
    async def test_synthesize_http_error(self, mock_client_cls):
        """Non-2xx status raises ElevenLabsError carrying the status code."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b"unauthorized"
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        client = ElevenLabsClient(api_key="test-key", voice_id="test-voice", timeout=15)
        with pytest.raises(ElevenLabsError, match="401"):
            await client.synthesize("Hola", Path("test_audio_el/out.mp3"))

    @pytest.mark.asyncio
    @patch("backend.services.elevenlabs_client.httpx.AsyncClient")
    async def test_synthesize_timeout(self, mock_client_cls):
        """Transport timeout is wrapped in ElevenLabsError."""
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        client = ElevenLabsClient(api_key="test-key", voice_id="test-voice", timeout=15)
        with pytest.raises(ElevenLabsError):
            await client.synthesize("Hola", Path("test_audio_el/out.mp3"))
