"""
core/summarizer.py — Stage 4: LLM-based summarization.
Supports Groq (Llama 3.3) and Cohere (Command R+) with automatic fallback.
Handles long transcripts via chunked map-reduce summarization.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class LangSummary:
    lang_code: str
    lang_name: str
    plain: str
    markdown: str


@dataclass
class SummaryResult:
    summaries: dict[str, LangSummary]

    def get(self, lang_code: str) -> Optional[LangSummary]:
        return self.summaries.get(lang_code)


class SummarizerError(Exception):
    pass


class Summarizer:
    def __init__(self, fast_mode: bool = False):
        self._style = "detailed"
        self._tone = "professional"
        self.fast_mode = fast_mode

    def summarize(
        self,
        transcript: str,
        target_langs: list[str],
        groq_key: Optional[str] = None,
        cohere_key: Optional[str] = None,
        style: str = "detailed",
        tone: str = "professional",
    ) -> SummaryResult:
        self._style = style
        self._tone = tone
        
        client, provider_name = self._get_client(groq_key, cohere_key)
        logger.info("Summarizing with %s, langs=%s, style=%s, tone=%s", provider_name, target_langs, style, tone)

        summaries: dict[str, LangSummary] = {}
        for lang in target_langs:
            try:
                plain, markdown = self._summarize_for_lang(transcript, lang, client, provider_name)
                summaries[lang] = LangSummary(
                    lang_code=lang,
                    lang_name=config.LANG_NAMES.get(lang, lang.upper()),
                    plain=plain,
                    markdown=markdown,
                )
            except Exception as e:
                logger.error("Summarization failed for lang=%s: %s", lang, e)

        return SummaryResult(summaries=summaries)

    def _get_client(self, groq_key=None, cohere_key=None):
        key = groq_key or config.get_next_groq_key()
        if key:
            try:
                from groq import Groq
                return Groq(api_key=key), "groq"
            except Exception as e:
                logger.warning("Groq client init failed: %s", e)

        key = cohere_key or config.COHERE_API_KEY
        if key:
            try:
                import cohere
                return cohere.Client(api_key=key), "cohere"
            except Exception as e:
                logger.warning("Cohere client init failed: %s", e)

        raise SummarizerError("No LLM provider available. Set GROQ_API_KEYS or COHERE_API_KEY.")

    def _summarize_for_lang(self, transcript: str, lang: str, client, provider: str) -> tuple[str, str]:
        words = transcript.split()
        if len(words) <= config.SUMMARY_CHUNK_WORDS:
            plain = self._call_llm(client, provider, self._direct_prompt(transcript, lang))
            md = self._call_llm(client, provider, self._direct_prompt_md(transcript, lang))
        else:
            plain = self._map_reduce(transcript, lang, client, provider, structured=False)
            md = self._map_reduce(transcript, lang, client, provider, structured=True)
        return plain, md

    def _map_reduce(self, transcript: str, lang: str, client, provider: str, structured: bool) -> str:
        words = transcript.split()
        chunk_size = config.SUMMARY_CHUNK_WORDS
        overlap = config.SUMMARY_CHUNK_OVERLAP

        chunks = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunks.append(" ".join(words[start:end]))
            start += chunk_size - overlap
            if start <= 0:
                break

        logger.info("Map-reduce: %d chunks for lang=%s", len(chunks), lang)
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            logger.debug("Summarizing chunk %d/%d", i + 1, len(chunks))
            prompt = self._chunk_prompt(chunk, lang)
            summary = self._call_llm(client, provider, prompt, max_tokens=config.SUMMARY_CHUNK_MAX_TOKENS)
            chunk_summaries.append(summary)
            if not self.fast_mode and i < len(chunks) - 1:
                time.sleep(0.3)

        combined = " ".join(chunk_summaries)
        if structured:
            return self._call_llm(client, provider, self._final_prompt_md(combined, lang), max_tokens=config.SUMMARY_FINAL_MAX_TOKENS)
        return self._call_llm(client, provider, self._final_prompt(combined, lang), max_tokens=config.SUMMARY_FINAL_MAX_TOKENS)

    def _call_llm(self, client, provider: str, prompt: str, max_tokens: int = 1024) -> str:
        for attempt in range(config.PROVIDER_MAX_RETRIES):
            try:
                if provider == "groq":
                    resp = client.chat.completions.create(
                        model=config.GROQ_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=config.SUMMARY_TEMPERATURE,
                        max_tokens=max_tokens,
                    )
                    return resp.choices[0].message.content.strip()
                elif provider == "cohere":
                    try:
                        resp = client.chat(
                            model=config.COHERE_MODEL,
                            message=prompt,
                            temperature=config.SUMMARY_TEMPERATURE,
                            max_tokens=max_tokens,
                        )
                        return resp.text.strip()
                    except AttributeError:
                        resp = client.generate(
                            model="command",
                            prompt=prompt,
                            temperature=config.SUMMARY_TEMPERATURE,
                            max_tokens=max_tokens,
                        )
                        return resp.generations[0].text.strip()
            except Exception as e:
                err_str = str(e)
                # Parse retry-after from Groq 429 response
                wait_minutes = None
                import re as _re
                m = _re.search(r'try again in (\d+)m(\d+)', err_str, _re.IGNORECASE)
                if m:
                    wait_minutes = int(m.group(1)) + 1  # round up
                elif _re.search(r'429|rate.limit|too many requests', err_str, _re.IGNORECASE):
                    wait_minutes = 30  # safe fallback

                if wait_minutes:
                    raise SummarizerError(
                        f"RATE_LIMIT:{wait_minutes}"   # structured — caught upstream
                    )

                delay = config.PROVIDER_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("LLM call failed (attempt %d): %s. Retrying in %.1fs", attempt + 1, e, delay)
                time.sleep(delay)

        raise SummarizerError("All LLM retry attempts failed")

    def _direct_prompt(self, text: str, lang: str) -> str:
        style_i = self._get_style_instruction()
        tone_i = self._get_tone_instruction()
        
        if lang == "ar":
            return f"""Summarize in Arabic. {style_i} {tone_i}

Text:
{text}

Summary:"""
        if lang == "ar-eg":
            return f"""Summarize in Egyptian Arabic (العربية المصرية). {style_i} {tone_i}

Text:
{text}

Summary:"""
        return f"""Write a {self._style} summary in {config.LANG_NAMES.get(lang, 'English')}. {tone_i}

Transcript:
{text}

Summary:"""

    def _direct_prompt_md(self, text: str, lang: str) -> str:
        tone_i = self._get_tone_instruction()
        
        if lang == "ar":
            return f"""Summarize in Arabic with Markdown. {tone_i}

Text:
{text}

Summary:"""
        if lang == "ar-eg":
            return f"""Summarize in Egyptian Arabic with Markdown. {tone_i}

Text:
{text}

Summary:"""
        return f"""Write a structured Markdown summary in {config.LANG_NAMES.get(lang, 'English')}. {tone_i}
Use headers (##), bullet points, and clear sections.

Transcript:
{text}

Structured Summary:"""

    def _get_style_instruction(self) -> str:
        if self._style == "brief":
            return "Be concise, focus on key points only."
        return "Be comprehensive with important details."

    def _get_tone_instruction(self) -> str:
        if self._tone == "casual":
            return "Write in a friendly, casual tone."
        elif self._tone == "technical":
            return "Use precise technical terminology."
        return "Write in a clear professional tone."

    def _chunk_prompt(self, chunk: str, lang: str) -> str:
        tone_i = self._get_tone_instruction()
        
        if lang == "ar":
            return f"""Summarize this section briefly. {tone_i}

{chunk}"""
        if lang == "ar-eg":
            return f"""Summarize this section in Egyptian Arabic. {tone_i}

{chunk}"""
        return f"""Summarize this section as a {self._style} summary. {tone_i}

{chunk}"""

    def _final_prompt(self, combined: str, lang: str) -> str:
        tone_i = self._get_tone_instruction()
        
        if lang == "ar":
            return f"""Combine into one Arabic summary. {tone_i}

{combined}

Final Summary:"""
        if lang == "ar-eg":
            return f"""Combine into one Egyptian Arabic summary. {tone_i}

{combined}

Final Summary:"""
        return f"""Consolidate into one summary. {tone_i}

{combined}

Final Summary:"""

    def _final_prompt_md(self, combined: str, lang: str) -> str:
        tone_i = self._get_tone_instruction()
        
        if lang == "ar":
            return f"""Combine into one Markdown summary in Arabic. {tone_i}

{combined}

Final Summary:"""
        if lang == "ar-eg":
            return f"""Combine into one Markdown summary in Egyptian Arabic. {tone_i}

{combined}

Final Summary:"""
        return f"""Consolidate into one structured Markdown summary. {tone_i}
Use clear ## headers and bullet points.

{combined}

Final Structured Summary:"""

    def translate_segments(
        self,
        segments: list,
        target_lang: str,
        groq_key: Optional[str] = None,
        cohere_key: Optional[str] = None,
    ) -> str:
        """
        Translate transcript segments into target_lang and return a valid SRT string.

        Strategy: batch segments into groups of ~50 to stay within token limits,
        send each batch as a numbered list to the LLM, parse back translated lines,
        then reassemble into SRT with original timestamps.
        """
        client, provider = self._get_client(groq_key, cohere_key)
        lang_name = config.LANG_NAMES.get(target_lang, target_lang.upper())
        is_rtl = target_lang in ("ar", "ar-eg")

        BATCH = 50   # segments per LLM call
        translated_texts: list[str] = [""] * len(segments)

        for batch_start in range(0, len(segments), BATCH):
            batch = segments[batch_start: batch_start + BATCH]

            # Build numbered list for LLM
            numbered = "\n".join(
                f"{i+1}. {seg.text.strip()}"
                for i, seg in enumerate(batch)
            )

            prompt = (
                f"Translate each numbered line below into {lang_name}. "
                f"Return ONLY the translated lines, keeping the same numbers and format. "
                f"Do NOT merge, split, skip, or reorder lines.\n\n"
                f"{numbered}"
            )

            try:
                raw = self._call_llm(client, provider, prompt, max_tokens=2048)
            except SummarizerError as e:
                # Propagate RATE_LIMIT errors, skip others
                if "RATE_LIMIT:" in str(e):
                    raise
                logger.warning("Subtitle translation batch failed: %s", e)
                # Fall back: use original text for this batch
                for i, seg in enumerate(batch):
                    translated_texts[batch_start + i] = seg.text.strip()
                continue

            # Parse "N. translated text" lines
            import re as _re
            parsed: dict[int, str] = {}
            for line in raw.strip().splitlines():
                m = _re.match(r'^(\d+)\.\s*(.*)', line.strip())
                if m:
                    parsed[int(m.group(1))] = m.group(2).strip()

            for i, seg in enumerate(batch):
                idx = i + 1
                translated_texts[batch_start + i] = parsed.get(idx, seg.text.strip())

        # Build SRT from original timestamps + translated text
        srt_lines: list[str] = []
        for i, seg in enumerate(segments):
            srt_lines.append(str(i + 1))
            srt_lines.append(
                f"{self._srt_time(seg.start)} --> {self._srt_time(seg.end)}"
            )
            srt_lines.append(translated_texts[i])
            srt_lines.append("")

        return "\n".join(srt_lines)

    @staticmethod
    def _srt_time(seconds: float) -> str:
        """Convert float seconds to SRT timestamp HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
