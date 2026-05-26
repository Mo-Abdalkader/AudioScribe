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
    script_txt: Optional[Path] = None
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

        # ── Transcript with timestamps ──────────────────────────────────
        transcript_lines = []
        for seg in transcript.segments:
            start_time = self._format_time(seg.start)
            transcript_lines.append(f"[{start_time}] {seg.text}")

        transcript_with_timestamps = "\n".join(transcript_lines)
        transcript_ts_path = out_dir / f"{safe_name}_transcript.txt"
        transcript_ts_path.write_text(transcript_with_timestamps, encoding="utf-8")
        bundle.transcript_txt = transcript_ts_path

        # ── Clean script (no timestamps, concatenated) ──────────────────
        clean_lines = [seg.text.strip() for seg in transcript.segments if seg.text.strip()]
        clean_script = "\n".join(clean_lines)
        script_path = out_dir / f"{safe_name}_script.txt"
        script_path.write_text(clean_script, encoding="utf-8")
        bundle.script_txt = script_path

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
            zf.write(transcript_ts_path, transcript_ts_path.name)
            zf.write(script_path, script_path.name)
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
        include_transcript: bool = True,
        include_subtitles: bool = True,
        include_summary: bool = True,
        translated_srt: dict[str, str] = None,
    ) -> dict[str, bytes]:
        """
        Write all outputs to in-memory bytes.
        File naming format: {JOB6}-{DURmin}min-{TYPE}-{LANG}.ext
        Example: a3f9b1-04min-transcript.txt
                 a3f9b1-04min-summary-EN.md
        """
        safe_name = self._safe_filename(base_name)
        files: dict[str, bytes] = {}
        if translated_srt is None:
            translated_srt = {}

        # Short ID prefix: first 6 chars of job_id
        short_id = (job_id or "000000")[:6].upper()

        # Duration from transcript segments
        if transcript.segments:
            total_sec = transcript.segments[-1].end
            dur_min = max(1, round(total_sec / 60))
        else:
            dur_min = 1
        dur_str = f"{dur_min:02d}min"

        # Detected language abbreviation
        src_lang = transcript.detected_language.upper().replace("-", "")

        # Clean base name from original filename (max 20 chars)
        import re as _re
        clean_base = _re.sub(r"[^\w\-]", "_", base_name)[:20].strip("_") or "audio"

        # Branding tag appended to text files
        BRAND = "AudioScribe-github.com-Mo-Abdalkader"

        def fname(ftype: str, lang: str = "", ext: str = "txt") -> str:
            """Build: cleanname-ID-DURmin-TYPE[-LANG].ext"""
            parts = [clean_base, short_id, dur_str, ftype]
            if lang:
                parts.append(lang.upper().replace("-", ""))
            return "-".join(parts) + "." + ext

        BRAND_FOOTER = f"\n\n---\n🎙 Generated by AudioScribe · github.com/Mo-Abdalkader/AudioScribe"

        # ── Transcript files ───────────────────────────────────────────
        if include_transcript:
            transcript_lines = []
            for seg in transcript.segments:
                start_time = self._format_time(seg.start)
                transcript_lines.append(f"[{start_time}] {seg.text}")
            transcript_content = "\n".join(transcript_lines) + BRAND_FOOTER
            files[fname("transcript")] = transcript_content.encode("utf-8")

            clean_lines = [seg.text.strip() for seg in transcript.segments if seg.text.strip()]
            script_content = "\n".join(clean_lines) + BRAND_FOOTER
            files[fname("script")] = script_content.encode("utf-8")

        # ── Subtitle files ─────────────────────────────────────────────
        if include_subtitles:
            files[fname("subtitles", src_lang, "srt")] = self._to_srt(transcript.segments).encode("utf-8")
            files[fname("subtitles", src_lang, "vtt")] = self._to_vtt(transcript.segments).encode("utf-8")

            for lang_code, srt_content in translated_srt.items():
                tl = lang_code.upper().replace("-", "")
                files[fname("subtitles", tl, "srt")] = srt_content.encode("utf-8")
                files[fname("subtitles", tl, "vtt")] = self._srt_to_vtt(srt_content).encode("utf-8")

        # ── Summary files ──────────────────────────────────────────────
        if include_summary:
            for lang_code, lang_summary in summary_result.summaries.items():
                ll = lang_code.upper().replace("-", "")
                if lang_summary.plain:
                    content = lang_summary.plain + BRAND_FOOTER
                    files[fname("summary", ll, "txt")] = content.encode("utf-8")
                if lang_summary.markdown:
                    content = lang_summary.markdown + BRAND_FOOTER
                    files[fname("summary", ll, "md")] = content.encode("utf-8")

        # ── ZIP bundle ─────────────────────────────────────────────────
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fn, data in files.items():
                zf.writestr(fn, data)
        zip_buf.seek(0)
        files[fname("audioscribe", "", "zip")] = zip_buf.read()

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

    def _srt_to_vtt(self, srt_content: str) -> str:
        """Convert an SRT string to VTT format (for translated subtitles)."""
        # SRT timestamps use , for ms; VTT uses .
        vtt = srt_content.replace(",", ".")
        # Remove sequence numbers (lines that are just digits)
        import re as _re
        vtt = _re.sub(r'(?m)^\d+\s*\n', '', vtt)
        return "WEBVTT\n\n" + vtt.strip() + "\n"
