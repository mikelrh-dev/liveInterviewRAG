"""Edge TTS client — wraps Microsoft Edge text-to-speech synthesis."""

import logging
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)


class EdgeTTSClient:
    """Synthesize speech through Microsoft Edge TTS."""

    def __init__(self, voice: str):
        self.voice = voice

    async def synthesize(self, text: str, output_path: Path) -> Path:
        """Convert text to an audio file at the given output path.

        Args:
            text: Text to synthesize.
            output_path: Destination path for the generated audio file.

        Returns:
            The output path.

        Raises:
            RuntimeError: If synthesis fails.
        """
        try:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(str(output_path))
            logger.info("Synthesized %d chars -> %s", len(text), output_path.name)
            return output_path

        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            raise RuntimeError(f"Could not synthesize speech: {e}") from e
