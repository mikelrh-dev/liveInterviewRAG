"""Tests for TTS service with mocked Edge TTS."""

import asyncio
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
        result_path, provider = await svc.synthesize("Hello, I am Mikel.")

        assert result_path.exists()
        assert result_path.suffix == ".mp3"
        assert provider == "microsoft"
        mock_communicate.save.assert_called_once()

        # Cleanup
        result_path.unlink()
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
        result_path, provider = await svc.synthesize("Hello", output_path=custom_path)

        assert result_path == custom_path
        assert result_path.exists()
        assert provider == "microsoft"

        # Cleanup
        result_path.unlink()
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


class TestTTSServiceOrchestrator:
    """Tests for provider selection, per-conversation fallback, and pinning."""

    def _make_service(self, **kwargs):
        defaults = dict(primary_provider="elevenlabs", elevenlabs_api_key="test-key",
                        elevenlabs_voice_id="test-voice", output_dir="test_audio_orch")
        defaults.update(kwargs)
        return TTSService(**defaults)

    def test_default_voice_is_alvaro(self):
        """Design default voice for the orchestrator is es-ES-AlvaroNeural."""
        svc = TTSService(output_dir="test_audio_orch")
        assert svc.voice == "es-ES-AlvaroNeural"
        if svc.output_dir.exists():
            svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_missing_creds_skips_elevenlabs(self):
        """primary=elevenlabs without API key forces Microsoft; EL client is never created."""
        svc = TTSService(primary_provider="elevenlabs", elevenlabs_api_key="",
                         elevenlabs_voice_id="test-voice", output_dir="test_audio_orch")
        assert svc._elevenlabs is None

        async def fake_save(text, output_path):
            Path(output_path).touch()

        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(side_effect=fake_save)
        svc._edge = mock_edge

        out_path = svc.output_dir / "missing_creds.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path,
                                              conversation_id="conv-a")
        assert provider == "microsoft"
        assert path == out_path
        assert path.exists()

        out_path.unlink()
        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_primary_microsoft_never_tries_elevenlabs(self):
        """provider=microsoft means ElevenLabs is never called even with creds present."""
        svc = TTSService(primary_provider="microsoft", elevenlabs_api_key="test-key",
                         elevenlabs_voice_id="test-voice", output_dir="test_audio_orch")
        assert svc._elevenlabs is None

        async def fake_save(text, output_path):
            Path(output_path).touch()

        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(side_effect=fake_save)
        svc._edge = mock_edge

        out_path = svc.output_dir / "ms_only.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path,
                                              conversation_id="conv-a")
        assert provider == "microsoft"
        assert path.exists()

        out_path.unlink()
        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_elevenlabs_success_pins_conversation(self):
        """Successful EL call returns provider elevenlabs and pins the conversation."""
        svc = self._make_service()
        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        svc._edge = mock_edge

        out_path = svc.output_dir / "el_ok.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path,
                                              conversation_id="conv-a")
        assert provider == "elevenlabs"
        assert path == out_path
        mock_el.synthesize.assert_awaited_once_with("Hola", out_path)
        # Pinned: a second call for the same conversation uses EL again
        await svc.synthesize("Otra vez", output_path=out_path, conversation_id="conv-a")
        assert mock_el.synthesize.await_count == 2
        assert mock_edge.synthesize.await_count == 0

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_elevenlabs_failure_falls_back_and_pins_microsoft(self):
        """EL failure triggers MS for the same call and pins the conversation to MS."""
        svc = self._make_service()
        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(side_effect=ElevenLabsError("boom"))
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._edge = mock_edge

        out_path = svc.output_dir / "el_fail.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path,
                                              conversation_id="conv-a")
        assert provider == "microsoft"
        assert path == out_path
        mock_edge.synthesize.assert_awaited_once()
        # Pinned: the next call for the same conversation never retries EL
        await svc.synthesize("Otra vez", output_path=out_path, conversation_id="conv-a")
        assert mock_el.synthesize.await_count == 1
        assert mock_edge.synthesize.await_count == 2

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_conversation_isolation(self):
        """Failure in conversation A does not affect conversation B's provider."""
        svc = self._make_service()
        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(
            side_effect=[ElevenLabsError("boom"), Path("ignored-b")]
        )
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._edge = mock_edge

        out_a = svc.output_dir / "iso_a.mp3"
        out_b = svc.output_dir / "iso_b.mp3"

        path_a, provider_a = await svc.synthesize("A", output_path=out_a,
                                                  conversation_id="conv-a")
        assert provider_a == "microsoft"

        path_b, provider_b = await svc.synthesize("B", output_path=out_b,
                                                  conversation_id="conv-b")
        assert provider_b == "elevenlabs"
        assert path_b == out_b
        assert mock_el.synthesize.await_count == 2  # conv B tried EL again

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_slow_elevenlabs_times_out_and_falls_back(self):
        """EL exceeding the timeout is treated as provider failure -> MS fallback."""
        svc = self._make_service(elevenlabs_timeout=0.05)

        async def slow(text, output_path):
            await asyncio.sleep(0.5)
            return output_path

        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(side_effect=slow)
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._edge = mock_edge

        out_path = svc.output_dir / "timeout.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path,
                                              conversation_id="conv-a")
        assert provider == "microsoft"
        assert path == out_path
        mock_edge.synthesize.assert_awaited_once()
        # Pinned after timeout: next call does not retry EL
        await svc.synthesize("Otra vez", output_path=out_path, conversation_id="conv-a")
        assert mock_el.synthesize.await_count == 1

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_both_fail_raises_runtime_error(self):
        """When EL and MS both fail, RuntimeError propagates."""
        svc = self._make_service()
        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(side_effect=ElevenLabsError("boom"))
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(side_effect=RuntimeError("Could not synthesize"))
        svc._edge = mock_edge

        with pytest.raises(RuntimeError, match="Could not synthesize"):
            await svc.synthesize("Hola", conversation_id="conv-a")

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_no_conversation_id_skips_pinning(self):
        """Direct callers without conversation_id get per-call provider, no pinning."""
        svc = self._make_service()
        mock_el = AsyncMock()
        mock_el.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._elevenlabs = mock_el
        mock_edge = AsyncMock()
        svc._edge = mock_edge

        out_path = svc.output_dir / "nopin.mp3"
        path, provider = await svc.synthesize("Hola", output_path=out_path)
        assert provider == "elevenlabs"
        assert svc._conversation_providers == {}

        svc.output_dir.rmdir()

    @pytest.mark.asyncio
    async def test_synthesize_sentence_returns_provider_triple(self):
        """synthesize_sentence returns (sentence_id, path, provider)."""
        svc = TTSService(output_dir="test_audio_orch")
        mock_edge = AsyncMock()
        mock_edge.synthesize = AsyncMock(return_value=Path("ignored"))
        svc._edge = mock_edge

        sid, path, provider = await svc.synthesize_sentence(
            "Hola", 3, output_dir=svc.output_dir, conversation_id="conv-a"
        )
        assert sid == 3
        assert path.suffix == ".mp3"
        assert provider == "microsoft"
        assert svc._conversation_providers["conv-a"] == "microsoft"

        svc.output_dir.rmdir()

    def test_forget_conversation_idempotent(self):
        """forget_conversation removes state and tolerates unknown ids."""
        svc = TTSService(output_dir="test_audio_orch")
        svc._conversation_providers["conv-a"] = "microsoft"
        svc._conversation_providers["conv-b"] = "elevenlabs"

        svc.forget_conversation("conv-a")
        assert svc._conversation_providers == {"conv-b": "elevenlabs"}
        # Idempotent: forgetting the same or unknown ids is a no-op
        svc.forget_conversation("conv-a")
        svc.forget_conversation("never-existed")
        assert svc._conversation_providers == {"conv-b": "elevenlabs"}

        svc.output_dir.rmdir()
