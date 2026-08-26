"""Configuration management for InterviewTTS backend."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


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

        # Semantic answer cache (Cap-3): instant paraphrase answers
        self.SEMANTIC_CACHE_ENABLED: bool = _env_bool("SEMANTIC_CACHE_ENABLED", "true")
        self.SEMANTIC_CACHE_TTL_DAYS: int = _env_int("SEMANTIC_CACHE_TTL_DAYS", "14")
        self.SEMANTIC_CACHE_MAX_ROWS: int = _env_int("SEMANTIC_CACHE_MAX_ROWS", "500")
        self.SEMANTIC_CACHE_THRESHOLD: float = _env_float(
            "SEMANTIC_CACHE_THRESHOLD", "0.93"
        )

        # Server
        # Intentional 0.0.0.0 binding: app sits behind nginx on the same VPS host.
        # pi-lens-ignore: B104
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = _env_int("PORT", "8000")

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

        # Periodic audio cleanup interval in minutes
        self.AUDIO_CLEANUP_INTERVAL_MIN: int = _env_int(
            "AUDIO_CLEANUP_INTERVAL_MIN", "30"
        )

        # Audio limits
        self.MAX_AUDIO_DURATION: int = _env_int("MAX_AUDIO_DURATION", "30")


config = Config()
