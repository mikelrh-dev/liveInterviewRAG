"""Text-to-Speech orchestration with per-conversation provider fallback.

The service owns two TTS client implementations behind one ``TTSClient``
protocol: Microsoft Edge TTS (default) and ElevenLabs (opt-in). Provider
selection is decided per conversation on its first synthesis; a failed primary
provider pins that conversation to Microsoft for its lifetime.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Protocol

from backend.services.edge_tts_client import EdgeTTSClient
from backend.services.elevenlabs_client import ElevenLabsClient, ElevenLabsError

logger = logging.getLogger(__name__)


class TTSClient(Protocol):
    """Structural type for a TTS backend client."""

    async def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize text to an audio file at ``output_path``."""
        ...


class TTSService:
    """TTS orchestrator with provider selection, pinning, and fallback.

    Provider selection:
      - ``primary_provider="microsoft"`` (default): Microsoft only; ElevenLabs
        is never called even when credentials are present.
      - ``primary_provider="elevenlabs"`` with both credentials set: ElevenLabs
        is attempted first for each conversation.
      - ``primary_provider="elevenlabs"`` with missing credentials: forced to
        Microsoft; ElevenLabs client is not created.

    Per-conversation pinning:
      - The first synthesis of a conversation picks the primary provider.
      - On success the conversation is pinned to that provider.
      - On failure (or timeout) the conversation is pinned to Microsoft and
        the fallback is used for the same call; later calls never retry the
        failed primary for that conversation.
      - ``conversation_id=None`` (direct callers) performs a per-call fallback
        without recording any pinning state.
    """

    def __init__(self, voice: str = "es-ES-AlvaroNeural", output_dir: str | Path = "audio",
                 primary_provider: str = "microsoft", elevenlabs_api_key: str = "",
                 elevenlabs_voice_id: str = "", elevenlabs_timeout: float = 15.0):
        self.voice = voice
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.primary_provider = primary_provider
        self.elevenlabs_timeout = elevenlabs_timeout
        self._conversation_providers: dict[str, str] = {}

        # ElevenLabs is enabled only when configured as primary AND fully
        # credentialed. A misconfigured primary degrades to Microsoft.
        self._elevenlabs: ElevenLabsClient | None = None
        if primary_provider == "elevenlabs":
            if elevenlabs_api_key and elevenlabs_voice_id:
                self._elevenlabs = ElevenLabsClient(
                    api_key=elevenlabs_api_key,
                    voice_id=elevenlabs_voice_id,
                    timeout=elevenlabs_timeout,
                )
            else:
                logger.warning(
                    "TTS_PRIMARY_PROVIDER=elevenlabs but ELEVENLABS_API_KEY/VOICE_ID missing; "
                    "falling back to Microsoft"
                )
        self._edge: TTSClient = EdgeTTSClient(voice)

    async def synthesize(self, text: str, output_path: str | Path | None = None,
                         conversation_id: str | None = None) -> tuple[Path, str]:
        """Convert text to an audio file and report the provider that produced it.

        Args:
            text: Text to synthesize.
            output_path: Optional custom output path. If None, generates a UUID-based name.
            conversation_id: Optional conversation id used for provider pinning.

        Returns:
            Tuple of (path to the generated audio file, provider name).

        Raises:
            ValueError: If text is empty.
            RuntimeError: If synthesis fails on every available provider.
        """
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text")

        if output_path is None:
            filename = f"{uuid.uuid4().hex}.mp3"
            output_path = self.output_dir / filename
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        provider = self._select_provider(conversation_id)

        if provider == "elevenlabs":
            try:
                # Per-request timeout: a timeout counts as provider failure.
                await asyncio.wait_for(
                    self._elevenlabs.synthesize(text, output_path),
                    timeout=self.elevenlabs_timeout,
                )
                self._pin(conversation_id, "elevenlabs")
                return output_path, "elevenlabs"
            except (ElevenLabsError, asyncio.TimeoutError) as e:
                logger.warning(
                    "ElevenLabs synthesis failed (conversation=%s): %s — "
                    "falling back to Microsoft", conversation_id, e
                )
                self._pin(conversation_id, "microsoft")
                provider = "microsoft"

        # Microsoft path: primary microsoft, pinned fallback, or EL failure above.
        await self._edge.synthesize(text, output_path)
        self._pin(conversation_id, provider)
        return output_path, provider

    async def synthesize_sentence(self, text: str, sentence_id: int,
                                  output_dir: Path | None = None,
                                  conversation_id: str | None = None) -> tuple[int, Path, str]:
        """Synthesize a single sentence for parallel streaming.

        Returns:
            Tuple of (sentence_id, audio path, provider name).
        """
        out_dir = output_dir or self.output_dir
        filename = f"sentence_{sentence_id}_{uuid.uuid4().hex}.mp3"
        output_path = out_dir / filename
        _, provider = await self.synthesize(
            text, output_path=output_path, conversation_id=conversation_id
        )
        return sentence_id, output_path, provider

    def forget_conversation(self, conversation_id: str) -> None:
        """Evict a conversation's provider pinning state (idempotent)."""
        self._conversation_providers.pop(conversation_id, None)

    def _select_provider(self, conversation_id: str | None) -> str:
        """Resolve the provider for a call.

        A pinned conversation always uses its pinned provider; unpinned
        conversations use the configured primary.
        """
        if conversation_id is not None:
            pinned = self._conversation_providers.get(conversation_id)
            if pinned is not None:
                return pinned
        return self.primary_provider if self._elevenlabs is not None else "microsoft"

    def _pin(self, conversation_id: str | None, provider: str) -> None:
        """Record a conversation's provider (no-op when conversation_id is None)."""
        if conversation_id is not None:
            self._conversation_providers[conversation_id] = provider

    def get_audio_url(self, audio_path: Path) -> str:
        """Convert an audio file path to a URL path for serving.

        Args:
            audio_path: Absolute path to the audio file.

        Returns:
            URL path like /audio/{filename}.
        """
        return f"/audio/{audio_path.name}"