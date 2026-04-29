"""
core/output_manager.py — Stage 5: Write output files and bundle as ZIP.
"""
from __future__ import annotations

import io
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config
from core.transcriber import TranscriptionResult
from core.summarizer import SummaryResult

logger = logging.getLogger(__name__)


@dataclass
class OutputBundle:
    job_id: str
    output_dir: Path
    transcript_txt: Optional[Path] = None
    summaries_txt: dict[str, Path] = field(default_factory=dict)
    summaries_md: dict[str, Path] = field(default_factory=dict)
    zip_path: Optional[Path] = None

    def cleanup(self):
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir, ignore_errors=True)


class OutputManager:
    def write(
        self,
        job_id: str,
        base_name: str,
        transcript: TranscriptionResult,
        summary_result: SummaryResult,
        output_dir: Optional[Path] = None,
    ) -> OutputBundle:
        out_dir = output_dir or (config.OUTPUT_TEMP_DIR / f"out_{job_id}")
        out_dir.mkdir(parents=True, exist_ok=True)

        bundle = OutputBundle(job_id=job_id, output_dir=out_dir)
        safe_name = self._safe_filename(base_name)

        # ── Transcript ──────────────────────────────────────────────────
        transcript_lines = []
        for seg in transcript.segments:
            start_time = self._format_time(seg.start)
            transcript_lines.append(f"[{start_time}] {seg.text}")

        transcript_content = "\n\n".join(transcript_lines)
        transcript_path = out_dir / f"{safe_name}_transcript.txt"
        transcript_path.write_text(transcript_content, encoding="utf-8")
        bundle.transcript_txt = transcript_path

        # ── Summaries ───────────────────────────────────────────────────
        for lang_code, lang_summary in summary_result.summaries.items():
            if lang_summary.plain:
                txt_path = out_dir / f"{safe_name}_summary_{lang_code}.txt"
                txt_path.write_text(lang_summary.plain, encoding="utf-8")
                bundle.summaries_txt[lang_code] = txt_path

            if lang_summary.markdown:
                md_path = out_dir / f"{safe_name}_summary_{lang_code}.md"
                md_path.write_text(lang_summary.markdown, encoding="utf-8")
                bundle.summaries_md[lang_code] = md_path

        # ── ZIP bundle ──────────────────────────────────────────────────
        zip_path = out_dir / f"{safe_name}_audioscribe.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if bundle.transcript_txt:
                zf.write(bundle.transcript_txt, bundle.transcript_txt.name)
            for p in bundle.summaries_txt.values():
                zf.write(p, p.name)
            for p in bundle.summaries_md.values():
                zf.write(p, p.name)

        bundle.zip_path = zip_path
        logger.info("Output bundle written to %s", out_dir)
        return bundle

    def write_to_memory(
        self,
        job_id: str,
        base_name: str,
        transcript: TranscriptionResult,
        summary_result: SummaryResult,
    ) -> dict[str, bytes]:
        """
        Write all outputs to in-memory bytes (for web API responses).
        Returns dict of {filename: bytes}.
        """
        safe_name = self._safe_filename(base_name)
        files: dict[str, bytes] = {}

        transcript_lines = []
        for seg in transcript.segments:
            start_time = self._format_time(seg.start)
            transcript_lines.append(f"[{start_time}] {seg.text}")

        transcript_content = "\n\n".join(transcript_lines)
        files[f"{safe_name}_transcript.txt"] = transcript_content.encode("utf-8")

        files[f"{safe_name}_transcript.srt"] = self._to_srt(transcript.segments).encode("utf-8")
        files[f"{safe_name}_transcript.vtt"] = self._to_vtt(transcript.segments).encode("utf-8")

        for lang_code, lang_summary in summary_result.summaries.items():
            if lang_summary.plain:
                files[f"{safe_name}_summary_{lang_code}.txt"] = lang_summary.plain.encode("utf-8")
            if lang_summary.markdown:
                files[f"{safe_name}_summary_{lang_code}.md"] = lang_summary.markdown.encode("utf-8")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in files.items():
                zf.writestr(fname, data)
        zip_buf.seek(0)
        files[f"{safe_name}_audioscribe.zip"] = zip_buf.read()

        return files

    def _safe_filename(self, name: str) -> str:
        import re
        name = re.sub(r"[^\w\-_]", "_", name)
        return name[:40] if len(name) > 40 else name or "output"

    def _format_time(self, seconds: float) -> str:
        """Format seconds to HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    def _format_srt_time(self, seconds: float) -> str:
        """Format seconds to SRT timestamp: HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        millis = int((seconds % 60) * 1000)
        return f"{hours:02d}:{minutes:02d}:{millis:03d}"

    def _format_vtt_time(self, seconds: float) -> str:
        """Format seconds to VTT timestamp: HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        millis = int((seconds % 60) * 1000)
        return f"{hours:02d}:{minutes:02d}:{millis:03d}"

    def _to_srt(self, segments: list) -> str:
        """Convert segments to SRT subtitle format."""
        lines = []
        for i, seg in enumerate(segments, 1):
            start = self._format_srt_time(seg.start)
            end = self._format_srt_time(seg.end)
            lines.append(f"{i}\n{start} --> {end}\n{seg.text}\n")
        return "\n".join(lines)

    def _to_vtt(self, segments: list) -> str:
        """Convert segments to VTT subtitle format."""
        lines = ["WEBVTT\n"]
        for seg in segments:
            start = self._format_vtt_time(seg.start)
            end = self._format_vtt_time(seg.end)
            lines.append(f"{start} --> {end}\n{seg.text}\n")
        return "\n".join(lines)
