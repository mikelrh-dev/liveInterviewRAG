"""Configuration management for InterviewTTS backend."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Coarsest sweep interval that is still an interval, in minutes. Exported
#: because the floor is a policy statement and the test that holds it should
#: quote the same constant the code applies, not restate the number.
MIN_AUDIO_CLEANUP_INTERVAL_MIN = 1


def _env_int(name: str, default: str) -> int:
    """Read an integer env var; fail fast naming the offending variable."""
    try:
        return int(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"Invalid integer for {name}: {os.getenv(name)!r}") from None


def _env_float(name: str, default: str) -> float:
    """Read a float env var; fail fast naming the offending variable."""
    try:
        return float(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"Invalid float for {name}: {os.getenv(name)!r}") from None


def _env_bool(name: str, default: str) -> bool:
    """Read a boolean env var ("1"/"true"/"yes"/"on" are truthy)."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _env_origin_list(name: str, default: str) -> list[str]:
    """Read a comma-separated env var as a clean list of origins.

    An empty value falls back to ``default`` rather than yielding ``[]``. Those
    are different intentions: an operator who has not filled the variable in yet
    is not an operator who wants every origin rejected, and silently
    distinguishing them by looking at whether the line is blank is not something
    a reader of a shell file can do.
    """
    raw = os.getenv(name, default)
    origins = [item.strip() for item in raw.split(",")]
    return [origin for origin in origins if origin] or [default]


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        # API Keys
        self.OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
        self.GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
        self.GOOGLE_MODEL: str = os.getenv("GOOGLE_MODEL", "gemini-3.1-flash-lite")

        # Whisper settings
        self.WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "small")
        self.WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
        self.WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        # CTranslate2 inference threads. It was never set, so faster-whisper's
        # default of 1 applied: ONE inference worker, which is what its own
        # docstring says serialises concurrent transcriptions. Turns overlap in
        # this application whenever a second tab is open, or a retry the rate
        # limiter delayed past the first turn's decode is in flight, and the
        # measured cost was 28.5 s for two concurrent turns against 21.5 s for
        # the same two run in series (1.32x) -- 1.60x with four workers.
        #
        # Four is the core count of the deployed box (ARM64, 4 cores,
        # deployment/interviewtts.service) and the service is single-process, so
        # this is "every core, no more": CTranslate2 spawns THREADS inside the
        # one process, and a count above the core count buys oversubscription
        # rather than throughput. The rule an operator applies to a different
        # box is min(cores, this default). A development machine with more cores
        # does not need them -- transcription there is one developer's own turn.
        #
        # The floor is 1, checked in STTService rather than here: a value this
        # low is a service that loads a model which cannot transcribe, and
        # refusing it at the service means every construction path is covered by
        # one check.
        self.WHISPER_NUM_WORKERS: int = _env_int("WHISPER_NUM_WORKERS", "4")

        # TTS settings
        self.TTS_VOICE: str = os.getenv("TTS_VOICE", "es-ES-AlvaroNeural")

        # LLM settings
        self.LLM_MODEL: str = os.getenv("LLM_MODEL", "openrouter/owl-alpha")
        self.LLM_TEMPERATURE: float = _env_float("LLM_TEMPERATURE", "0.7")
        self.LLM_MAX_TOKENS: int = _env_int("LLM_MAX_TOKENS", "200")

        # RAG settings
        #
        # The default embedder is MULTILINGUAL because the corpus and the
        # questions are: `wiki/` is Spanish and the question reaches the RAG
        # verbatim from Whisper, in Spanish, unpunctuated. The default it
        # replaced, all-MiniLM-L6-v2, is an ENGLISH model, so every Spanish
        # paraphrase paid for the language gap.
        #
        # CURRENT — measured 2026-09-29 and re-verified 2026-09-30, through the
        # production path (real loader, real 400/50 chunker, real
        # `expand_query`, strict primary-gold-page match at top_k=3, one chunk
        # per page), on the labelled questions in `tests/real_wiki.py`.
        #
        # TWO POPULATIONS, because `wiki/` has two states. Four FAQ pages
        # (`nivel-ingles`, `disponibilidad`, `hobbies-intereses`,
        # `por-que-contratarte`) exist on disk and are NOT in the index, so a
        # clean clone scores 41 of the 49 labelled questions, not 49. Publishing
        # only the 49-question figures made this comment describe a corpus a
        # clone does not have:
        #     full (37 pages, 49 questions) recall@1 0.7347 · recall@3 0.8163 · MRR@5 0.7803
        #     reduced (33 pages, 41 questions) recall@1 0.7317 · recall@3 0.8293 · MRR@5 0.7935
        # On the full population 40 of 49 gold pages are served, 2 of 49
        # absent from the ranking entirely.
        # Four things differ from the 2026-08-28 English baseline and none of
        # them is the embedder alone: this model, the page-identity prefix on
        # the embedded text, the one-chunk-per-page cut, and the bodyless-
        # heading filter. `tests/real_wiki.py::CommentFigures` owns both rows
        # and is what enforces them; this comment is checked against them and
        # against a fresh measurement by
        # `tests/test_recall_claims.py::TestTheRAGCommentsQuoteTheCurrentMeasurement`.
        #
        # HISTORY — 2026-09-29, the embedder swap (4de600d) with the model as the
        # ONLY lever touched, before the prefix, the per-page cut and the
        # bodyless-heading filter existed:
        #     recall@1 0.5510 -> 0.6122 · recall@3 0.6531 -> 0.7959
        #     MRR@5 0.6109 -> 0.7088
        # Ten questions fixed, three broken. That pair is kept because it is the
        # record of ONE decision, and it is not a before/after against the
        # figures above: 0.7959 and 0.8163 are different measurements of
        # different configurations, and reading the second as the embedder's
        # share of the movement overstates what this model did.
        self.EMBEDDING_MODEL: str = os.getenv(
            "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.RAG_TOP_K: int = _env_int("RAG_TOP_K", "3")
        self.CHUNK_SIZE: int = _env_int("CHUNK_SIZE", "400")
        self.CHUNK_OVERLAP: int = _env_int("CHUNK_OVERLAP", "50")

        # Paths
        self.BASE_DIR: Path = Path(__file__).resolve().parent.parent
        self.CANDIDATE_DIR: Path = self.BASE_DIR / "candidate"
        self.WIKI_DIR: Path = self.BASE_DIR / "wiki"
        self.AUDIO_DIR: Path = self.BASE_DIR / "audio"
        self.FRONTEND_DIR: Path = self.BASE_DIR / "frontend"

        # Reports — persisted Markdown interview transcripts
        self.REPORTS_DIR: Path = Path(
            os.getenv("REPORTS_DIR", str(self.BASE_DIR / "reports"))
        )

        # Reports — retention window in days for cleanup_expired()
        self.REPORT_RETENTION_DAYS: int = _env_int("REPORT_RETENTION_DAYS", "30")

        # RAG cache
        self.RAG_CACHE_DIR: Path = Path(
            os.getenv("RAG_CACHE_DIR", str(self.BASE_DIR / "backend" / ".rag_cache"))
        )

        # Persistence (Cap-2): SQLite write-through store; *.db is gitignored
        self.PERSISTENCE_ENABLED: bool = _env_bool("PERSISTENCE_ENABLED", "true")
        self.DB_PATH: Path = Path(
            os.getenv("DB_PATH", str(self.BASE_DIR / "data" / "interviewtts.db"))
        )

        # The semantic answer cache (Cap-3) used to live here, as four
        # SEMANTIC_CACHE_* keys. It was removed because the shipped embedder
        # cannot tell a Spanish paraphrase from a different Spanish question --
        # see tests/test_rag.py::TestSemanticAnswerCacheWasNotViable. The keys
        # are not honoured any more; the FAQ literal cache below is the only
        # cache in front of the LLM.

        # Server
        # The bind address is NOT read from here, and neither is the port. Both
        # are process-level concerns owned by the unit file, which runs uvicorn
        # with `--host 127.0.0.1 --port 8000` so the API is only reachable
        # through the local reverse proxy. The two places that decide it are:
        #
        #   deployment/interviewtts.service:24  the deployed service
        #   RUNBOOK.md:18                       local development
        #
        # Keeping a second copy of that decision in config is how the two drifted
        # apart: HOST used to default to 0.0.0.0 here with a comment asserting
        # that was intentional, while the hardened unit file bound to loopback --
        # and .env.example declared HOST=0.0.0.0 on top, so an operator reading
        # the example believed they had configured an exposure that the process
        # never honoured. PORT was the same shape one field over: read into
        # config.PORT and then never read again by anything, including /api/config.
        #
        # To change either, change the command line, not this file.
        # pi-lens-ignore: B104

        # Rate limiting
        self.RATE_LIMIT_PER_MINUTE: int = _env_int("RATE_LIMIT_PER_MINUTE", "10")

        # Session TTL — hours before idle conversation eviction (floor 0.1)
        raw_ttl = _env_float("SESSION_TTL_HOURS", "2")
        if raw_ttl < 0.1:
            logger.warning(
                "SESSION_TTL_HOURS=%s is below floor of 0.1; defaulting to 2", raw_ttl
            )
            raw_ttl = 2.0
        self.SESSION_TTL_HOURS: float = raw_ttl

        # Periodic audio cleanup interval in minutes (floor 1)
        #
        # The floor is not a nicety, and it is the same rule SESSION_TTL_HOURS
        # got: this value is multiplied by 60 in main.py and handed to
        # periodic_cleanup, whose tick ends with `await asyncio.sleep(interval)`.
        # asyncio.sleep returns immediately for any value at or below zero, so an
        # interval of 0 does not mean "sweep constantly" -- it means a loop that
        # never yields, re-running the whole sweep body (an rglob over the audio
        # tree, a report prune, four SQLite statements) at full speed for as long
        # as the process lives. One minute is the coarsest interval that is still
        # an interval.
        raw_interval = _env_int("AUDIO_CLEANUP_INTERVAL_MIN", "30")
        if raw_interval < MIN_AUDIO_CLEANUP_INTERVAL_MIN:
            logger.warning(
                "AUDIO_CLEANUP_INTERVAL_MIN=%s is below floor of %d; defaulting to 30",
                raw_interval,
                MIN_AUDIO_CLEANUP_INTERVAL_MIN,
            )
            raw_interval = 30
        self.AUDIO_CLEANUP_INTERVAL_MIN: int = raw_interval

        # CORS origins, as the list the middleware is given.
        #
        # Parsed here rather than in main.py, which read the variable with
        # os.getenv and handed `.split(",")` straight to CORSMiddleware. Two
        # consequences: the padding around a comma-separated value survived, and
        # an origin like " https://site " never matches the Origin header a
        # browser actually sends -- so the request fails CORS and the operator
        # is looking at a policy problem while the cause is a stray space.
        # Reading it here also makes the value constructible in a test without
        # mutating the process environment.
        self.CORS_ORIGINS: list[str] = _env_origin_list(
            "CORS_ORIGINS", "http://localhost:8000"
        )

        # Audio limits
        #
        # MAX_AUDIO_DURATION is the ONE limit on a recording, in seconds, and
        # it used to be read by nobody. Its only appearance in the code was this
        # assignment; the only ceiling that ever fired was MAX_AUDIO_SIZE, which
        # is a byte count. So the page invented its own number instead, and the
        # two were free to disagree.
        #
        # It is now published by GET /api/config and cut at by the page, which
        # has to do it itself: the duration is not knowable without decoding the
        # container, and a request that arrives at 5 MiB is already too late to
        # answer with advice.
        #
        # 60, not 30. Thirty was never checked against anything, and the byte
        # ceiling it sat above permits 5.4 minutes at the recorder's own
        # audioBitsPerSecond -- so it was arbitrarily strict rather than
        # deliberately strict. It is also now a safety net rather than the
        # mechanism: the VAD finds the end of an ordinary turn, and this only
        # catches the turn it cannot (a room too noisy for any silence to
        # register). Cutting an answer at 30 s would throw away most of what a
        # candidate said in order to prevent a failure that the floor already
        # handles.
        self.MAX_AUDIO_DURATION: int = _env_int("MAX_AUDIO_DURATION", "60")


config = Config()
