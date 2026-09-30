"""Speech-to-Text service using Faster Whisper."""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcription:
    """What one decode produced: the words, and how long they took to say.

    ``duration_seconds`` is not measured here. It is read off the
    ``TranscriptionInfo`` that ``faster_whisper`` returns from the call the
    transcription already had to make, so carrying it costs nothing and no
    second decode of the same file is introduced.

    It matters because the duration is the one measurement the upload path
    cannot make. ``uploads.stage_upload`` sees bytes -- it has not parsed the
    container and does not know the bitrate the browser encoded at -- so every
    claim it can make about a recording's length is an inference. The only
    component that knows how long a recording really is, for free, is the one
    that just decoded it.
    """

    text: str
    duration_seconds: float


class STTService:
    """Faster Whisper wrapper for speech-to-text transcription."""

    def __init__(
        self,
        model_name: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        max_duration_seconds: float | None = None,
        num_workers: int = 1,
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        #: CTranslate2 inference threads, and the parallelism between
        #: concurrent transcriptions. ``faster_whisper`` defaults this to 1,
        #: which is faster_whisper's own docstring describing a single inference
        #: worker: several workers each get their own copy of the model, so two
        #: overlapping transcriptions really do run at once.
        #:
        #: The default here is 1 -- faster-whisper's -- and the deployed value
        #: comes from ``config.WHISPER_NUM_WORKERS``, because a service does not
        #: read configuration. What the number buys in THIS project, measured on
        #: two concurrent turns against the same two run in series: 28.5 s -> 21.5
        #: s (1.32x) at the shipped default, and 1.60x with four workers. Turns
        #: overlap whenever a second tab, or a retry the rate limiter delayed
        #: past the first turn's decode, is in flight.
        if num_workers < 1:
            # Not a "let the library decide" value: CTranslate2 either rejects
            # zero or accepts it as "no inference worker at all", and either way
            # the model cannot transcribe. A configuration typo must not be able
            # to produce a service that loads successfully and answers nothing.
            raise ValueError(
                f"num_workers must be at least 1, got {num_workers!r}"
            )
        self.num_workers = num_workers
        #: The recording limit this deployment advertises, in seconds, or None
        #: to not check. Injected rather than read from ``backend.config``
        #: because the services do not import configuration: the composition
        #: root owns the wiring, which is what makes the value testable and
        #: swappable.
        #:
        #: It is compared against, and reported, never enforced by rejection. A
        #: recording that arrives over the limit is one the page failed to cut --
        #: a stale cached page, or a client that is not this one -- and refusing
        #: it after the decode has already run would throw away a turn the
        #: candidate can still be answered from, in exchange for bounding work
        #: this process is already doing. So it is recorded, loudly, with both
        #: numbers, and the turn proceeds.
        self.max_duration_seconds = max_duration_seconds
        self._model = None

    def load_model(self):
        """Load the Whisper model at startup."""
        try:
            from faster_whisper import WhisperModel

            logger.info(
                "Loading Whisper model: %s (device=%s, compute=%s, num_workers=%d)",
                self.model_name, self.device, self.compute_type, self.num_workers,
            )
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=self.num_workers,
            )
            logger.info(
                "Whisper model loaded successfully (%d inference worker(s))",
                self.num_workers,
            )
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)
            raise

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def transcribe_measured(self, audio_path: str | Path) -> Transcription:
        """Transcribe, and report the duration the decode already returned.

        Args:
            audio_path: Path to audio file (wav, webm, ogg).

        Returns:
            The transcribed text and the audio duration in seconds.

        Raises:
            RuntimeError: If model not loaded or transcription fails.
            FileNotFoundError: If audio file does not exist.
        """
        if not self._model:
            raise RuntimeError("Whisper model not loaded. Call load_model() first.")

        audio_path = Path(audio_path)
        if not audio_path.exists():
            # The staged path is a server-side filesystem location, and this
            # message is copied into the client's error payload. The path goes
            # in the log instead.
            logger.error("Staged audio file is missing: %s", audio_path)
            raise FileNotFoundError("Audio file not found")

        try:
            segments, info = self._model.transcribe(
                str(audio_path),
                beam_size=1,
                language="es",
                vad_filter=True,
            )

            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            result = " ".join(text_parts)
            duration = float(info.duration)
            logger.info("Transcribed %.1fs audio -> %d chars", duration, len(result))
            self._report_over_limit(duration)
            return Transcription(text=result, duration_seconds=duration)

        except Exception as e:
            # Logged, not carried in the message: see backend/services/tts.py.
            logger.error("Transcription failed: %s", e, exc_info=True)
            raise RuntimeError("Could not transcribe audio") from e

    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe audio file to text.

        A thin wrapper over :meth:`transcribe_measured` for the callers that
        only want the words. It is not a second decode -- the measurement rides
        along with the text, and there is one call to ``faster_whisper`` either
        way.
        """
        return self.transcribe_measured(audio_path).text

    def _report_over_limit(self, duration: float) -> None:
        """Say so when a recording is longer than the limit this server claims.

        A warning rather than a rejection, and the log carries both numbers.
        The measured one is the fact; the configured one is the promise it broke.
        A line that said only "over limit" would leave a reader unable to tell a
        marginally long answer from a client that never cut at all.
        """
        limit = self.max_duration_seconds
        if limit is None or duration <= limit:
            return
        logger.warning(
            "Recording is %.1fs, over the %.0fs limit this server advertises "
            "(MAX_AUDIO_DURATION). The page cuts at that limit, so a longer "
            "recording means a client that did not -- a cached page, or "
            "something that is not this page.",
            duration,
            limit,
        )
