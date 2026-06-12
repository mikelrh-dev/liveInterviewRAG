"""Speech-to-Text service using Faster Whisper."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class STTService:
    """Faster Whisper wrapper for speech-to-text transcription."""

    def __init__(self, model_name: str = "tiny", device: str = "cpu", compute_type: str = "int8"):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def load_model(self):
        """Load the Whisper model at startup."""
        try:
            from faster_whisper import WhisperModel

            logger.info("Loading Whisper model: %s (device=%s, compute=%s)",
                        self.model_name, self.device, self.compute_type)
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("Whisper model loaded successfully")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)
            raise

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe audio file to text.

        Args:
            audio_path: Path to audio file (wav, webm, ogg).

        Returns:
            Transcribed text string.

        Raises:
            RuntimeError: If model not loaded or transcription fails.
            FileNotFoundError: If audio file does not exist.
        """
        if not self._model:
            raise RuntimeError("Whisper model not loaded. Call load_model() first.")

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            segments, info = self._model.transcribe(
                str(audio_path),
                beam_size=1,
                language=None,
                vad_filter=True,
            )

            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            result = " ".join(text_parts)
            logger.info("Transcribed %.1fs audio -> %d chars", info.duration, len(result))
            return result

        except Exception as e:
            logger.error("Transcription failed: %s", e)
            raise RuntimeError(f"Could not transcribe audio: {e}") from e
