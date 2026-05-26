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
            raise TranscriptionError("No Groq API key available.")
        from groq import Groq
        self.client = Groq(api_key=key)
        self._api_key = key
        self._language_hint = language_hint

    def transcribe_chunks(self, chunks: list[ChunkInfo], progress_callback: callable = None) -> TranscriptionResult:
        """Transcribe all chunks and merge into a single result."""
        all_segments: list[TranscriptionSegment] = []
        detected_lang = "en"
        lang_prob = 1.0
        total_chunks = len(chunks)
        next_update = 0
        update_interval = max(1, total_chunks // 20)  # Update about 20 times total

        for i, chunk in enumerate(chunks):
            if i >= next_update:
                pct = int((i / total_chunks) * 100)
                if progress_callback:
                    progress_callback(f"Transcribing {i+1}/{total_chunks} ({pct}%)")
                next_update += update_interval
            
            logger.info("Transcribing chunk %d/%d", i + 1, total_chunks)
            try:
                result = self._transcribe_chunk(chunk)
                # Use language from first chunk
                if i == 0:
                    detected_lang = result.detected_language
                    lang_prob = result.lang_probability

                # Adjust segment timestamps to absolute positions
                for seg in result.segments:
                    abs_start = chunk.chunk_start_ms / 1000 + seg.start
                    abs_end   = chunk.chunk_start_ms / 1000 + seg.end

                    # Skip segments that fall entirely inside the overlap zone.
                    # Use a small tolerance (0.05s) so boundary segments are kept.
                    content_start = chunk.start_ms / 1000
                    content_end   = chunk.end_ms   / 1000
                    TOLERANCE = 0.05

                    if chunk.has_leading_overlap and abs_end < content_start - TOLERANCE:
                        continue
                    if chunk.has_trailing_overlap and abs_start > content_end + TOLERANCE:
                        continue

                    all_segments.append(TranscriptionSegment(
                        text=seg.text.strip(),
                        start=abs_start,
                        end=abs_end,
                        confidence=seg.avg_logprob + 1,
                    ))

            except Exception as e:
                error_msg = str(e).lower()
                # Check for rate limit errors
                if "rate_limit" in error_msg or "too many requests" in error_msg or "429" in error_msg:
                    logger.warning(f"Rate limit hit on chunk {i+1}. Retrying...")
                    if progress_callback:
                        progress_callback(f"Rate limit. Retrying chunk {i+1}...")
                    # Retry up to 3 times
                    for retry in range(3):
                        try:
                            time.sleep(2 + retry * 2)
                            result = self._transcribe_chunk(chunk)
                            break
                        except Exception as retry_e:
                            logger.warning(f"Retry {retry+1} failed: {retry_e}")
                            if retry == 2:
                                logger.error(f"Chunk {i+1} failed after retries")
                    else:
                        continue
                else:
                    logger.error("Chunk %d transcription failed: %s", i, e)
                    continue

            # Rate limit: Groq allows ~20 req/min on free tier
            if i < len(chunks) - 1:
                time.sleep(0.3)

        full_text = self._merge_segments(all_segments)

        # Also deduplicate the segments list itself by timestamp
        # so SRT/VTT files don't contain overlapping entries
        all_segments_sorted = sorted(all_segments, key=lambda s: s.start)
        deduped_segments: list[TranscriptionSegment] = []
        last_end = -1.0
        for seg in all_segments_sorted:
            if seg.start < last_end - 0.1:
                continue
            deduped_segments.append(seg)
            last_end = seg.end

        return TranscriptionResult(
            segments=deduped_segments,
            detected_language=detected_lang,
            lang_probability=lang_prob,
            full_text=full_text,
        )

    def _transcribe_chunk(self, chunk: ChunkInfo, max_retries: int = 3) -> TranscriptionResult:
        """Send one chunk to Groq Whisper API with retry logic."""
        last_error = None
        for attempt in range(max_retries):
            try:
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
                break  # Success
                
            except Exception as e:
                error_msg = str(e).lower()
                last_error = e
                # Check for rate limit
                if "rate_limit" in error_msg or "429" in error_msg or "too many requests" in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 3
                        logger.warning(f"Rate limit, waiting {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        raise TranscriptionError(f"Rate limit exceeded. Use your own API key.")
                else:
                    raise
        
        if last_error and attempt == max_retries - 1:
            raise TranscriptionError(f"Failed after {max_retries} attempts: {last_error}")

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
        """
        Merge transcript segments into clean full text.

        Two-pass deduplication:
        1. Timestamp-based: drop segments whose start time overlaps with the
           previous segment's end (catches exact chunk boundary duplicates).
        2. Text-based: sliding window comparison to catch near-duplicate phrases
           that Whisper sometimes repeats across chunk boundaries.
        """
        if not segments:
            return ""

        segments = sorted(segments, key=lambda s: s.start)

        # Pass 1: Drop timestamp-overlapping segments
        # If a segment starts before the previous one ended, it's a duplicate
        deduped: list[TranscriptionSegment] = []
        last_end = -1.0
        for seg in segments:
            if seg.start < last_end - 0.1:   # 100ms tolerance
                continue
            deduped.append(seg)
            last_end = seg.end

        # Pass 2: Text-level dedup with sliding window
        merged = []
        prev_text = ""
        for seg in deduped:
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
        """
        Remove leading words from `current` that overlap with the tail of `prev`.

        Uses both exact matching and fuzzy matching (ignoring punctuation/case)
        to catch Whisper's mid-sentence repetitions.
        """
        if not prev or not current:
            return current

        import re as _re

        def normalize(s: str) -> str:
            """Lowercase, strip punctuation for comparison."""
            return _re.sub(r"[^a-z0-9\u0600-\u06ff\s]", "", s.lower()).split()

        prev_words  = prev.split()
        curr_words  = current.split()
        prev_norm   = normalize(prev)
        curr_norm   = normalize(current)

        window = config.DEDUP_WINDOW_WORDS

        prev_tail_norm = prev_norm[-window:]
        curr_head_norm = curr_norm[:window]

        # Try decreasing overlap lengths from max down to 3 words
        for overlap_len in range(min(len(prev_tail_norm), len(curr_head_norm)), 2, -1):
            if prev_tail_norm[-overlap_len:] == curr_head_norm[:overlap_len]:
                # Skip `overlap_len` words from the start of `current`
                # (count in original words, not normalized)
                skipped = 0
                orig_idx = 0
                for orig_idx, w in enumerate(curr_words):
                    if normalize(w):
                        skipped += 1
                    if skipped >= overlap_len:
                        orig_idx += 1
                        break
                remainder = " ".join(curr_words[orig_idx:]).strip()
                return remainder if remainder else ""

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
