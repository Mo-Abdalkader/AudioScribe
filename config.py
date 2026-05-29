"""
config.py — Central configuration for AudioScribe.
All tuneable constants from .env file - ONE source of truth.
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
# API Keys
# ─────────────────────────────────────────────
GROQ_API_KEYS: list[str] = [k.strip() for k in _get("GROQ_API_KEYS", _get("GROQ_API_KEY")).split(",") if k.strip()]
COHERE_API_KEY: str = _get("COHERE_API_KEY")

def get_next_groq_key() -> str | None:
    if not GROQ_API_KEYS:
        return None
    return GROQ_API_KEYS[0]


# ─────────────────────────────────────────────
# LLM Settings
# ─────────────────────────────────────────────
GROQ_MODEL: str = "llama-3.3-70b-versatile"
COHERE_MODEL: str = "command-r-plus"


# ─────────────────────────────────────────────
# Whisper
# ─────────────────────────────────────────────
WHISPER_MODEL: str = "whisper-large-v3"
WHISPER_LANGUAGE_HINTS: dict[str, str] = {
    "ar": "arabic", "ar-eg": "arabic", "en": "english", "fr": "french",
}


# ─────────────────────────────────────────────
# Limits (from .env - ONE source)
# ─────────────────────────────────────────────
# File sizes (MB)
MAX_FILE_SIZE_MB = _get_int("MAX_FILE_SIZE_MB", 50)
FREE_MAX_URL_SIZE_MB = _get_int("FREE_MAX_URL_SIZE_MB", 200)
PREMIUM_MAX_URL_SIZE_MB = _get_int("PREMIUM_MAX_URL_SIZE_MB", 2000)

# Duration (minutes)
MAX_AUDIO_DURATION_MINUTES = _get_int("MAX_AUDIO_DURATION_MINUTES", 60)

# Daily requests
FREE_DAILY_LIMIT = _get_int("FREE_DAILY_LIMIT", 10)

# Rate limits
RATE_LIMIT_WINDOW_SECONDS = _get_int("RATE_LIMIT_WINDOW_SECONDS", 3600)


def get_user_limits(has_own_key: bool) -> dict:
    """Get limits based on user tier."""
    if has_own_key:
        return {
            "max_file_mb": MAX_FILE_SIZE_MB,
            "max_url_mb": PREMIUM_MAX_URL_SIZE_MB,
            "max_duration_min": MAX_AUDIO_DURATION_MINUTES,
        }
    return {
        "max_file_mb": MAX_FILE_SIZE_MB,
        "max_url_mb": FREE_MAX_URL_SIZE_MB,
        "max_duration_min": MAX_AUDIO_DURATION_MINUTES,
    }


# ─────────────────────────────────────────────
# Audio processing
# ─────────────────────────────────────────────
CHUNK_DURATION_MS = _get_int("CHUNK_DURATION_MS", 25000)
CHUNK_OVERLAP_MS = _get_int("CHUNK_OVERLAP_MS", 3000)
DEDUP_WINDOW_WORDS = _get_int("DEDUP_WINDOW_WORDS", 20)


# ─────────────────────────────────────────────
# Summarization
# ─────────────────────────────────────────────
SUMMARY_CHUNK_WORDS = _get_int("SUMMARY_CHUNK_WORDS", 3500)
SUMMARY_CHUNK_OVERLAP = _get_int("SUMMARY_CHUNK_OVERLAP", 500)
SUMMARY_CHUNK_MAX_TOKENS = _get_int("SUMMARY_CHUNK_MAX_TOKENS", 1024)
SUMMARY_FINAL_MAX_TOKENS = _get_int("SUMMARY_FINAL_MAX_TOKENS", 2048)
SUMMARY_TEMPERATURE = _get_float("SUMMARY_TEMPERATURE", 0.7)


# ─────────────────────────────────────────────
# API Retry Settings
# ─────────────────────────────────────────────
PROVIDER_MAX_RETRIES = _get_int("PROVIDER_MAX_RETRIES", 3)
PROVIDER_RETRY_BASE_DELAY = _get_float("PROVIDER_RETRY_BASE_DELAY", 2.0)


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────
OUTPUT_TEMP_DIR = Path(_get("OUTPUT_TEMP_DIR", "/tmp/audioscribe"))
TRANSCRIPT_FORMATS = ["txt", "srt", "vtt"]


# ─────────────────────────────────────────────
# Web API
# ─────────────────────────────────────────────
WEB_SECRET_KEY = _get("WEB_SECRET_KEY", "change-me")
WEB_PORT = _get_int("PORT", 8000)
MAX_UPLOAD_SIZE_MB = _get_int("MAX_UPLOAD_SIZE_MB", 50)


# ─────────────────────────────────────────────
# Supported formats
# ─────────────────────────────────────────────
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus", ".webm"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v"})
ALL_SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


# ─────────────────────────────────────────────
# Language names
# ─────────────────────────────────────────────
LANG_NAMES = {
    "en": "English", "ar": "Arabic", "fr": "French",
    "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "zh": "Chinese",
}


def validate() -> list[str]:
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN not set")
    if not GROQ_API_KEYS and not COHERE_API_KEY:
        errors.append("No API key configured (GROQ_API_KEYS or COHERE_API_KEY)")
    return errors


if __name__ == "__main__":
    issues = validate()
    if issues:
        print("ERROR: Config issues:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("OK: Config loaded")
        print(f"MAX_FILE_SIZE_MB: {MAX_FILE_SIZE_MB}")
        print(f"MAX_AUDIO_DURATION_MINUTES: {MAX_AUDIO_DURATION_MINUTES}")
        print(f"FREE_DAILY_LIMIT: {FREE_DAILY_LIMIT}")