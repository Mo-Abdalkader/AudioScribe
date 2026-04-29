"""
core/pipeline.py — Main processing pipeline.
Orchestrates: audio → chunk → transcribe → summarize → output.
Used by both the Telegram bot and the web API.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

import config
from core.audio import AudioHandler, AudioFileInfo, AudioValidationError
from core.transcriber import Transcriber, TranscriptionResult
from core.summarizer import Summarizer, SummaryResult
from core.output_manager import OutputManager, OutputBundle

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    job_id: str
    file_info: AudioFileInfo
    transcript: TranscriptionResult
    summary: SummaryResult
    bundle: OutputBundle


class PipelineError(Exception):
    pass


class Pipeline:
    """
    Full audio processing pipeline.
    Accepts a file path, produces transcript + summaries + ZIP bundle.
    """

    def __init__(self, source_lang: str | None = None):
        self.source_lang = source_lang
        self.transcriber = Transcriber(language_hint=source_lang)
        self.summarizer = Summarizer()
        self.output_mgr = OutputManager()

    def run(
        self,
        input_path: Path,
        target_langs: list[str] = None,
        base_name: str = "",
        job_id: str = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        groq_key: Optional[str] = None,
        cohere_key: Optional[str] = None,
    ) -> PipelineResult:
        """
        Run the full pipeline on an input file.

        Args:
            input_path: Path to audio or video file.
            target_langs: Language codes to summarize into, e.g. ["en", "ar"].
            base_name: Base name for output files.
            job_id: Optional job identifier for temp file naming.
            progress_cb: Optional callback(message) for progress updates.
            groq_key: User's own Groq API key (optional).
            cohere_key: User's own Cohere API key (optional).

        Returns:
            PipelineResult with all outputs.
        """
        if target_langs is None:
            target_langs = ["en"]

        job_id = job_id or str(uuid.uuid4())[:12]
        base_name = base_name or input_path.stem

        def _progress(msg: str):
            logger.info("[%s] %s", job_id, msg)
            if progress_cb:
                progress_cb(msg)

        handler = AudioHandler(job_id=job_id)
        try:
            # Stage 1+2: Validate + chunk
            _progress("Validating and chunking audio...")
            try:
                file_info, chunks = handler.prepare(input_path)
            except AudioValidationError as e:
                raise PipelineError(str(e))

            _progress(f"Split into {len(chunks)} chunk(s) for transcription...")

            # Stage 3: Transcribe
            _progress("Transcribing with Whisper (Groq API)...")
            transcript = self.transcriber.transcribe_chunks(chunks)
            _progress(
                f"Transcription complete. Detected: {transcript.language_name} "
                f"({transcript.lang_probability:.0%} confidence)"
            )

            # Stage 4: Summarize
            lang_display = " + ".join(config.LANG_NAMES.get(l, l.upper()) for l in target_langs)
            _progress(f"Summarizing in {lang_display} (style={summary_style}, tone={summary_tone})...")
            summary = self.summarizer.summarize(
                transcript.full_text,
                target_langs=target_langs,
                groq_key=groq_key,
                cohere_key=cohere_key,
                style=summary_style,
                tone=summary_tone,
            )

            # Stage 5: Write outputs
            _progress("Packaging outputs...")
            bundle = self.output_mgr.write(job_id, base_name, transcript, summary)

            _progress("Done!")
            return PipelineResult(
                job_id=job_id,
                file_info=file_info,
                transcript=transcript,
                summary=summary,
                bundle=bundle,
            )

        except PipelineError:
            raise
        except Exception as e:
            logger.exception("Unexpected pipeline error")
            raise PipelineError(f"Processing failed: {e}")
        finally:
            handler.cleanup()

    def run_in_memory(
        self,
        input_path: Path,
        target_langs: list[str] = None,
        base_name: str = "",
        job_id: str = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        groq_key: Optional[str] = None,
        cohere_key: Optional[str] = None,
        summary_style: str = "detailed",
        summary_tone: str = "professional",
    ) -> tuple[TranscriptionResult, SummaryResult, dict[str, bytes]]:
        """
        Like run() but returns output files as in-memory bytes (for web API).
        """
        if target_langs is None:
            target_langs = ["en"]

        job_id = job_id or str(uuid.uuid4())[:12]
        base_name = base_name or input_path.stem

        def _progress(msg: str):
            logger.info("[%s] %s", job_id, msg)
            if progress_cb:
                progress_cb(msg)

        handler = AudioHandler(job_id=job_id)
        try:
            _progress("Validating and chunking audio...")
            try:
                file_info, chunks = handler.prepare(input_path)
            except AudioValidationError as e:
                raise PipelineError(str(e))

            _progress(f"Split into {len(chunks)} chunk(s). Transcribing...")
            transcript = self.transcriber.transcribe_chunks(chunks)
            _progress(f"Transcribed. Detected: {transcript.language_name}. Summarizing...")

            summary = self.summarizer.summarize(
                transcript.full_text,
                target_langs=target_langs,
                groq_key=groq_key,
                cohere_key=cohere_key,
                style=summary_style,
                tone=summary_tone,
            )
            _progress("Packaging...")

            files = self.output_mgr.write_to_memory(job_id, base_name, transcript, summary)
            _progress("Done!")
            return transcript, summary, files

        except PipelineError:
            raise
        except Exception as e:
            logger.exception("Unexpected pipeline error")
            raise PipelineError(f"Processing failed: {e}")
        finally:
            handler.cleanup()
