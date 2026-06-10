"""Text-to-Speech service using Edge TTS."""

import asyncio
import logging
import uuid
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)


class TTSService:
    """Edge TTS wrapper for text-to-speech synthesis."""

    def __init__(self, voice: str = "en-US-GuyNeural", output_dir: str | Path = "audio"):
        self.voice = voice
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def synthesize(self, text: str, output_path: str | Path | None = None) -> Path:
        """Convert text to speech audio file.

        Args:
            text: Text to synthesize.
            output_path: Optional custom output path. If None, generates a UUID-based name.

        Returns:
            Path to the generated audio file.

        Raises:
            RuntimeError: If synthesis fails.
        """
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text")

        if output_path is None:
            filename = f"{uuid.uuid4().hex}.mp3"
            output_path = self.output_dir / filename
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(str(output_path))
            logger.info("Synthesized %d chars -> %s", len(text), output_path.name)
            return output_path

        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            raise RuntimeError(f"Could not synthesize speech: {e}") from e

    async def synthesize_sentence(self, text: str, sentence_id: int,
                                    output_dir: Path | None = None) -> tuple[int, Path]:
        """Synthesize a single sentence for parallel streaming. Returns (id, path)."""
        out_dir = output_dir or self.output_dir
        filename = f"sentence_{sentence_id}_{uuid.uuid4().hex}.mp3"
        output_path = out_dir / filename
        await self.synthesize(text, output_path=output_path)
        return sentence_id, output_path

    def get_audio_url(self, audio_path: Path) -> str:
        """Convert an audio file path to a URL path for serving.

        Args:
            audio_path: Absolute path to the audio file.

        Returns:
            URL path like /audio/{filename}.
        """
        return f"/audio/{audio_path.name}"
