"""
core/audio.py — Stage 1 + 2: Input handling and audio chunking.
Uses ffmpeg directly. No GPU required.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class AudioFileInfo:
    path: Path
    file_type: str          # "audio" or "video"
    extension: str
    size_bytes: int
    duration_ms: int
    duration_seconds: float

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass
class ChunkInfo:
    path: Path
    index: int
    start_ms: int
    end_ms: int
    chunk_start_ms: int
    chunk_end_ms: int
    has_leading_overlap: bool
    has_trailing_overlap: bool


class AudioValidationError(Exception):
    pass


class AudioHandler:
    def __init__(self, job_id: Optional[str] = None):
        self.job_id = job_id or str(uuid.uuid4())[:8]
        self.temp_dir = config.OUTPUT_TEMP_DIR / self.job_id
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._created_files: list[Path] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cleanup()

    def cleanup(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def load(self, path: Path | str) -> AudioFileInfo:
        path = Path(path)
        if not path.exists():
            raise AudioValidationError(f"File not found: {path.name}")

        extension = path.suffix.lower()
        if extension not in config.ALL_SUPPORTED_EXTENSIONS:
            raise AudioValidationError(
                f"Unsupported format: {extension}. "
                f"Supported: {', '.join(sorted(config.ALL_SUPPORTED_EXTENSIONS))}"
            )

        size_bytes = path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > config.MAX_FILE_SIZE_MB:
            raise AudioValidationError(
                f"File too large: {size_mb:.1f} MB. Maximum: {config.MAX_FILE_SIZE_MB} MB"
            )

        file_type = "video" if extension in config.VIDEO_EXTENSIONS else "audio"
        duration_ms = self._get_duration_ms(path)
        duration_seconds = duration_ms / 1000

        if duration_seconds / 60 > config.MAX_AUDIO_DURATION_MINUTES:
            raise AudioValidationError(
                f"Audio too long: {duration_seconds/60:.1f} min. "
                f"Maximum: {config.MAX_AUDIO_DURATION_MINUTES} min"
            )

        return AudioFileInfo(
            path=path, file_type=file_type, extension=extension,
            size_bytes=size_bytes, duration_ms=duration_ms,
            duration_seconds=duration_seconds,
        )

    def _get_duration_ms(self, path: Path) -> int:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30
            )
            duration = float(result.stdout.strip())
            return int(duration * 1000)
        except Exception as e:
            logger.warning("ffprobe failed: %s — estimating from file size", e)
            size_mb = path.stat().st_size / (1024 * 1024)
            return int(size_mb * 60 * 1000)

    def extract_audio(self, video_info: AudioFileInfo, save_to: Optional[Path] = None) -> Path:
        if video_info.file_type != "video":
            raise ValueError("extract_audio() called on non-video file")

        out_path = self.temp_dir / f"extracted_{self.job_id}.mp3"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_info.path), "-vn", "-acodec", "libmp3lame",
                 "-q:a", "2", str(out_path)],
                capture_output=True, timeout=120
            )
        except Exception as e:
            raise AudioValidationError(f"Failed to extract audio: {e}")

        self._created_files.append(out_path)
        if save_to:
            shutil.copy2(out_path, save_to)
        return out_path

    def chunk(
        self,
        audio_path: Path,
        chunk_ms: int = config.CHUNK_DURATION_MS,
        overlap_ms: int = config.CHUNK_OVERLAP_MS,
    ) -> list[ChunkInfo]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=30
        )
        total_ms = int(float(result.stdout.strip()) * 1000)

        if total_ms <= chunk_ms + overlap_ms:
            chunk_path = self.temp_dir / f"chunk_000_{self.job_id}.wav"
            self._export_wav(audio_path, chunk_path)
            self._created_files.append(chunk_path)
            return [ChunkInfo(
                path=chunk_path, index=0,
                start_ms=0, end_ms=total_ms,
                chunk_start_ms=0, chunk_end_ms=total_ms,
                has_leading_overlap=False, has_trailing_overlap=False,
            )]

        chunks = []
        content_start = 0
        idx = 0

        while content_start < total_ms:
            content_end = min(content_start + chunk_ms, total_ms)
            chunk_start = max(0, content_start - overlap_ms)
            chunk_end = min(total_ms, content_end + overlap_ms)

            chunk_path = self.temp_dir / f"chunk_{idx:03d}_{self.job_id}.wav"
            self._extract_segment(audio_path, chunk_path, chunk_start, chunk_end)
            self._created_files.append(chunk_path)

            chunks.append(ChunkInfo(
                path=chunk_path, index=idx,
                start_ms=content_start, end_ms=content_end,
                chunk_start_ms=chunk_start, chunk_end_ms=chunk_end,
                has_leading_overlap=chunk_start < content_start,
                has_trailing_overlap=chunk_end > content_end,
            ))

            content_start = content_end
            idx += 1
            if content_end >= total_ms:
                break

        logger.info("Chunked into %d segments", len(chunks))
        return chunks

    def _extract_segment(self, input_path: Path, output_path: Path, start_ms: int, end_ms: int):
        duration_ms = end_ms - start_ms
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-ss", str(start_ms / 1000),
             "-t", str(duration_ms / 1000), "-acodec", "pcm_s16le", "-ar", "16000",
             str(output_path)],
            capture_output=True, timeout=60
        )

    def _export_wav(self, input_path: Path, output_path: Path):
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-acodec", "pcm_s16le",
             "-ar", "16000", str(output_path)],
            capture_output=True, timeout=60
        )

    def prepare(self, input_path: Path | str) -> tuple[AudioFileInfo, list[ChunkInfo]]:
        info = self.load(input_path)
        audio_path = self.extract_audio(info) if info.file_type == "video" else info.path
        chunks = self.chunk(audio_path)
        return info, chunks
