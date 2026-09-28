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

        # TTS settings
        self.TTS_VOICE: str = os.getenv("TTS_VOICE", "es-ES-AlvaroNeural")

        # LLM settings
        self.LLM_MODEL: str = os.getenv("LLM_MODEL", "openrouter/owl-alpha")
        self.LLM_TEMPERATURE: float = _env_float("LLM_TEMPERATURE", "0.7")
        self.LLM_MAX_TOKENS: int = _env_int("LLM_MAX_TOKENS", "200")

        # RAG settings
        self.EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
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
        self.MAX_AUDIO_DURATION: int = _env_int("MAX_AUDIO_DURATION", "30")


config = Config()
