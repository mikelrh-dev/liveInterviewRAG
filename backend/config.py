"""Configuration management for InterviewTTS backend."""

import os
from pathlib import Path


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        # API Keys
        self.OWL_API_KEY: str = os.getenv("OWL_API_KEY", "")

        # Whisper settings
        self.WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
        self.WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
        self.WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

        # TTS settings
        self.TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-GuyNeural")

        # LLM settings
        self.OWL_API_URL: str = os.getenv("OWL_API_URL", "https://api.owl.ai/v1/chat/completions")
        self.OWL_MODEL: str = os.getenv("OWL_MODEL", "gpt-3.5-turbo")

        # RAG settings
        self.EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        self.RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "3"))
        self.CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "400"))
        self.CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))

        # Paths
        self.BASE_DIR: Path = Path(__file__).resolve().parent.parent
        self.CANDIDATE_DIR: Path = self.BASE_DIR / "candidate"
        self.AUDIO_DIR: Path = self.BASE_DIR / "audio"
        self.FRONTEND_DIR: Path = self.BASE_DIR / "frontend"

        # Server
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "8000"))

        # Rate limiting
        self.RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

        # Audio limits
        self.MAX_AUDIO_DURATION: int = int(os.getenv("MAX_AUDIO_DURATION", "30"))


config = Config()
