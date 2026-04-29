"""
config.py — Central configuration for AudioScribe.
All tuneable constants live here. Loaded from environment / .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path, override=True)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _get_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN")

# ─────────────────────────────────────────────
# LLM Providers
# ─────────────────────────────────────────────
GROQ_API_KEYS: list[str] = [k.strip() for k in _get("GROQ_API_KEYS", _get("GROQ_API_KEY")).split(",") if k.strip()]
COHERE_API_KEY: str = _get("COHERE_API_KEY")
GROQ_MODEL: str = "llama-3.3-70b-versatile"
COHERE_MODEL: str = "command-r-plus"
PROVIDER_MAX_RETRIES: int = 3
PROVIDER_RETRY_BASE_DELAY: float = 2.0

def get_next_groq_key() -> str | None:
    """Get the next available Groq API key (rotation)."""
    if not GROQ_API_KEYS:
        return None
    return GROQ_API_KEYS[0]

def get_next_groq_key() -> str | None:
    """Get the next available Groq API key (rotation)."""
    if not GROQ_API_KEYS:
        return None
    return GROQ_API_KEYS[0]

# ─────────────────────────────────────────────
# Whisper (via Groq API — no GPU needed)
# ─────────────────────────────────────────────
WHISPER_MODEL: str = "whisper-large-v3"
WHISPER_BEAM_SIZE: int = 5
WHISPER_WORD_TIMESTAMPS: bool = True
WHISPER_LANGUAGE_HINTS: dict[str, str] = {
    "ar": "arabic",
    "ar-eg": "arabic",
    "en": "english",
    "fr": "french",
}

# ─────────────────────────────────────────────
# Audio Chunking
# ─────────────────────────────────────────────
CHUNK_DURATION_MS: int = _get_int("CHUNK_DURATION_MS", 25_000)   # 25 s
CHUNK_OVERLAP_MS: int = _get_int("CHUNK_OVERLAP_MS", 3_000)      # 3 s per side

# ─────────────────────────────────────────────
# Transcription
# ─────────────────────────────────────────────
CONFIDENCE_THRESHOLD: float = _get_float("CONFIDENCE_THRESHOLD", 0.6)
DEDUP_WINDOW_WORDS: int = _get_int("DEDUP_WINDOW_WORDS", 20)

# ─────────────────────────────────────────────
# Summarization
# ─────────────────────────────────────────────
SUMMARY_CHUNK_WORDS: int = _get_int("SUMMARY_CHUNK_WORDS", 3_500)
SUMMARY_CHUNK_OVERLAP: int = _get_int("SUMMARY_CHUNK_OVERLAP", 500)
SUMMARY_CHUNK_MAX_TOKENS: int = _get_int("SUMMARY_CHUNK_MAX_TOKENS", 1_024)
SUMMARY_FINAL_MAX_TOKENS: int = _get_int("SUMMARY_FINAL_MAX_TOKENS", 2_048)
SUMMARY_TEMPERATURE: float = _get_float("SUMMARY_TEMPERATURE", 0.7)
SUMMARY_STYLE: str = _get("SUMMARY_STYLE", "detailed")  # "brief" or "detailed"
SUMMARY_TONE: str = _get("SUMMARY_TONE", "professional")  # "professional", "casual", "technical"

# ─────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────
RATE_LIMIT_WINDOW_SECONDS: int = _get_int("RATE_LIMIT_WINDOW_SECONDS", 3_600)
RATE_LIMIT_DEFAULT_SECONDS: int = _get_int("RATE_LIMIT_DEFAULT_SECONDS", 1_800)
RATE_LIMIT_OWN_KEY_SECONDS: int = _get_int("RATE_LIMIT_OWN_KEY_SECONDS", 7_200)

# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────
TRANSCRIPT_MAX_LINE_LEN: int = _get_int("TRANSCRIPT_MAX_LINE_LEN", 120)
OUTPUT_TEMP_DIR: Path = Path(_get("OUTPUT_TEMP_DIR", "/tmp/audioscribe"))
MAX_FILE_SIZE_MB: int = _get_int("MAX_FILE_SIZE_MB", 50)  # Set to 50MB default
MAX_AUDIO_DURATION_MINUTES: int = _get_int("MAX_AUDIO_DURATION_MINUTES", 60)
TRANSCRIPT_FORMATS: list[str] = ["txt", "srt", "vtt"]

# ─────────────────────────────────────────────
# Web API
# ─────────────────────────────────────────────
WEB_SECRET_KEY: str = _get("WEB_SECRET_KEY", "change-me-in-production")
WEB_PORT: int = _get_int("PORT", 8000)
MAX_UPLOAD_SIZE_MB: int = _get_int("MAX_UPLOAD_SIZE_MB", 50)

# ─────────────────────────────────────────────
# Security
# ─────────────────────────────────────────────
ENCRYPTION_KEY: str = _get("ENCRYPTION_KEY", "default-key-change-me")

# ─────────────────────────────────────────────
# Supported file extensions
# ─────────────────────────────────────────────
AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus", ".webm"
})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v"
})
ALL_SUPPORTED_EXTENSIONS: frozenset[str] = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# ─────────────────────────────────────────────
# Language support
# ─────────────────────────────────────────────
NLTK_SUPPORTED_LANGS: dict[str, str] = {
    "en": "english", "ar": "arabic", "de": "german",
    "es": "spanish", "fr": "french", "it": "italian",
    "pt": "portuguese", "ru": "russian", "nl": "dutch", "pl": "polish",
}

LANG_NAMES: dict[str, str] = {
    "en": "English", "ar": "Arabic (العربية)", "fr": "French",
    "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "tr": "Turkish",
    "hi": "Hindi", "fa": "Persian", "ur": "Urdu",
    "ar-eg": "Egyptian Arabic (العامية المصرية)",
}


def validate() -> list[str]:
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set")
    if not GROQ_API_KEYS and not COHERE_API_KEY:
        errors.append("At least one of GROQ_API_KEYS or COHERE_API_KEY must be set")
    return errors


if __name__ == "__main__":
    issues = validate()
    if issues:
        print("❌ Configuration issues:")
        for issue in issues:
            print(f"  • {issue}")
    else:
        print("✅ Configuration OK")
