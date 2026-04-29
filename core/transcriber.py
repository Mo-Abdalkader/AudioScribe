"""
core/transcriber.py — Stage 3: Transcription via Groq Whisper API.
No GPU needed — all inference is remote.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config
from core.audio import ChunkInfo

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    text: str
    start: float   # seconds
    end: float     # seconds
    confidence: float = 1.0
    avg_logprob: float = 0.0


@dataclass
class TranscriptionResult:
    segments: list[TranscriptionSegment]
    detected_language: str
    lang_probability: float
    full_text: str

    @property
    def language_name(self) -> str:
        return config.LANG_NAMES.get(self.detected_language, self.detected_language.upper())


class TranscriptionError(Exception):
    pass


class Transcriber:
    """
    Transcribes audio using Groq's hosted Whisper Large-v3 API.
    Handles chunked audio, deduplicates overlaps, merges results.
    """

    def __init__(self, api_key: str | None = None, language_hint: str | None = None):
        key = api_key or config.get_next_groq_key()
        if not key:
            raise TranscriptionError(
                "No Groq API key available. Provide a key or set GROQ_API_KEYS in config."
            )
        from groq import Groq
        self.client = Groq(api_key=key)
        self._api_key = key
        self._language_hint = language_hint or config.WHISPER_LANGUAGE_HINTS.get(language_hint, "arabic" if language_hint and language_hint.startswith("ar") else None)

    def transcribe_chunks(self, chunks: list[ChunkInfo]) -> TranscriptionResult:
        """Transcribe all chunks and merge into a single result."""
        all_segments: list[TranscriptionSegment] = []
        detected_lang = "en"
        lang_prob = 1.0

        for i, chunk in enumerate(chunks):
            logger.info("Transcribing chunk %d/%d ...", i + 1, len(chunks))
            try:
                result = self._transcribe_chunk(chunk)
                # Use language from first chunk
                if i == 0:
                    detected_lang = result.detected_language
                    lang_prob = result.lang_probability

                # Adjust segment timestamps to absolute positions
                for seg in result.segments:
                    abs_start = chunk.chunk_start_ms / 1000 + seg.start
                    abs_end = chunk.chunk_start_ms / 1000 + seg.end

                    # Only include segments within the content window (skip overlap zones)
                    content_start = chunk.start_ms / 1000
                    content_end = chunk.end_ms / 1000

                    if abs_end <= content_start and chunk.has_leading_overlap:
                        continue
                    if abs_start >= content_end and chunk.has_trailing_overlap:
                        continue

                    all_segments.append(TranscriptionSegment(
                        text=seg.text.strip(),
                        start=abs_start,
                        end=abs_end,
                        confidence=seg.avg_logprob + 1,  # normalise logprob
                    ))

            except Exception as e:
                logger.error("Chunk %d transcription failed: %s", i, e)
                # Continue with remaining chunks rather than failing entirely
                continue

            # Rate limit: Groq allows ~20 req/min on free tier
            if i < len(chunks) - 1:
                time.sleep(0.5)

        full_text = self._merge_segments(all_segments)

        return TranscriptionResult(
            segments=all_segments,
            detected_language=detected_lang,
            lang_probability=lang_prob,
            full_text=full_text,
        )

    def _transcribe_chunk(self, chunk: ChunkInfo) -> TranscriptionResult:
        """Send one chunk to Groq Whisper API."""
        with open(chunk.path, "rb") as f:
            kwargs = {
                "file": (chunk.path.name, f, "audio/wav"),
                "model": config.WHISPER_MODEL,
                "response_format": "verbose_json",
                "temperature": 0.0,
            }
            if self._language_hint:
                kwargs["language"] = self._language_hint
            response = self.client.audio.transcriptions.create(**kwargs)

        response_dict = dict(response) if hasattr(response, "__iter__") else {"text": str(response), "segments": [], "language": "en", "language_probability": 1.0}

        segs = response_dict.get("segments", [])
        if segs:
            segments = []
            for s in segs:
                if hasattr(s, "__iter__"):
                    s_dict = dict(s)
                    segments.append(TranscriptionSegment(
                        text=s_dict.get("text", ""),
                        start=s_dict.get("start", 0),
                        end=s_dict.get("end", 0),
                        avg_logprob=s_dict.get("avg_logprob", 0),
                    ))
                else:
                    segments.append(TranscriptionSegment(
                        text=str(s),
                        start=chunk.start_ms / 1000,
                        end=chunk.end_ms / 1000,
                        avg_logprob=0,
                    ))
        else:
            segments.append(TranscriptionSegment(
                text=response_dict.get("text", ""),
                start=chunk.start_ms / 1000,
                end=chunk.end_ms / 1000,
                avg_logprob=0,
            ))

        return TranscriptionResult(
            segments=segments,
            detected_language=response_dict.get("language", "en"),
            lang_probability=response_dict.get("language_probability", 1.0),
            full_text=response_dict.get("text", ""),
        )

    def _merge_segments(self, segments: list[TranscriptionSegment]) -> str:
        """Merge segments into clean full text, trimming overlaps."""
        if not segments:
            return ""

        segments = sorted(segments, key=lambda s: s.start)

        merged = []
        prev_text = ""
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            if prev_text:
                text = self._trim_overlap(prev_text, text)
            if text:
                merged.append(text)
                prev_text = text

        return " ".join(merged)

    def _trim_overlap(self, prev: str, current: str) -> str:
        """Trim overlapping text from current segment."""
        if not prev or not current:
            return current

        prev_words = prev.split()
        curr_words = current.split()

        prev_tail = prev_words[-config.DEDUP_WINDOW_WORDS:]
        curr_head = curr_words[:config.DEDUP_WINDOW_WORDS]

        if not prev_tail or not curr_head:
            return current

        for i in range(len(curr_head), 0, -1):
            overlap_text = " ".join(curr_head[:i]).lower()
            prev_ending = " ".join(prev_tail[-i:]).lower()
            if overlap_text == prev_ending:
                return " ".join(curr_words[i:])

        return current

    def transcribe_file(self, audio_path: Path) -> TranscriptionResult:
        """Convenience method: transcribe a single file directly (no chunking)."""
        from core.audio import AudioHandler, ChunkInfo
        import uuid

        handler = AudioHandler(job_id=str(uuid.uuid4())[:8])
        try:
            chunks = handler.chunk(audio_path)
            return self.transcribe_chunks(chunks)
        finally:
            handler.cleanup()
