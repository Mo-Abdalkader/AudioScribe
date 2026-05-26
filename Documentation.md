# AudioScribe — Complete Feature Documentation

> **Version:** v9 Final  
> **Author:** Mohamed Abdalkader  
> **License:** MIT  
> **Last Updated:** 2026

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Core Pipeline (core/)](#3-core-pipeline-core)
   - 3.1 [audio.py — AudioHandler](#31-audiopy---audiohandler)
   - 3.2 [transcriber.py — Transcriber](#32-transcriberpy---transcriber)
   - 3.3 [summarizer.py — Summarizer](#33-summarizerpy---summarizer)
   - 3.4 [output_manager.py — OutputManager](#34-output_managerpy---outputmanager)
   - 3.5 [pipeline.py — Pipeline](#35-pipelinepy---pipeline)
4. [Web Interface (web/)](#4-web-interface-web)
   - 4.1 [app.py — FastAPI Server](#41-apppy---fastapi-server)
   - 4.2 [templates/index.html](#42-templatesindexhtml)
   - 4.3 [static/js/app.js — Frontend Logic](#43-staticjsappjs---frontend-logic)
   - 4.4 [static/css/style.css](#44-staticcssstylecss)
   - 4.5 [PWA (manifest.json + sw.js)](#45-pwa-manifestjson--swjs)
5. [Telegram Bot (bot/)](#5-telegram-bot-bot)
   - 5.1 [main.py — Bot Setup](#51-mainpy---bot-setup)
   - 5.2 [handlers.py — All Handlers](#52-handlerspy---all-handlers)
6. [Configuration Reference](#6-configuration-reference)
7. [Complete API Reference](#7-complete-api-reference)
8. [URL Sources & Downloading](#8-url-sources--downloading)
9. [Output Formats & File Naming](#9-output-formats--file-naming)
10. [Processing Modes](#10-processing-modes)
11. [Language Support](#11-language-support)
12. [Security Features](#12-security-features)
13. [Rate Limiting](#13-rate-limiting)
14. [Deployment](#14-deployment)
15. [Version History](#15-version-history)
16. [Known Limitations](#16-known-limitations)
17. [Future Ideas](#17-future-ideas)

---

## 1. Project Overview

**AudioScribe** is an AI-powered audio/video transcription and summarization application. It accepts audio files, video files, and URLs (YouTube, Google Drive, Dropbox, direct links), transcribes speech to text using Groq's Whisper Large-v3 API, then generates intelligent summaries using Llama 3.3 70B (via Groq) with Cohere Command R+ as fallback.

### Core Idea

Originally built as a personal tool to navigate LLM post-training lecture recordings without rewatching full videos. Drop any audio/video → get a searchable transcript + structured summary.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Groq API for both Whisper + Llama** | Fast inference, generous free tier (100K tokens/day), no GPU needed |
| **FastAPI** | Async-native, easy background tasks, automatic OpenAPI docs at `/api/docs` |
| **python-telegram-bot v21** | Mature, async, webhook support |
| **ffmpeg via subprocess** | Universal format support, no Python binding dependency issues |
| **yt-dlp** | The only reliable way to extract audio from YouTube |
| **Shared architecture** | Both web API and Telegram bot use the same `core/pipeline.py` |
| **Single process** | FastAPI manages the event loop, Telegram bot runs in the same process |

---

## 2. System Architecture

```
main.py (entry point)
  └── web/app.py (FastAPI server)
          ├── Telegram bot init (polling or webhook depending on env)
          ├── GET  /                    → serves index.html
          ├── POST /api/process         → _run_pipeline_background()
          ├── POST /api/process-url     → _run_url_pipeline_background()
          ├── GET  /api/status/{id}     → poll job status
          ├── GET  /api/download/{id}/{file} → download result
          ├── GET  /api/limits          → rate limit info
          ├── GET  /health              → smart health check
          ├── POST /webhook             → Telegram webhook receiver
          └── core/pipeline.py (orchestrates everything)
                  ├── core/audio.py           (Stage 1+2: validation + chunking)
                  ├── core/transcriber.py     (Stage 3: Groq Whisper)
                  ├── core/summarizer.py      (Stage 4: Groq Llama / Cohere)
                  └── core/output_manager.py  (Stage 5: file writing + ZIP)

bot/main.py       (Telegram Application setup, command registration)
bot/handlers.py   (all user-facing logic: files, URLs, commands, keyboards)

web/static/       (CSS, JS, PWA resources, images)
web/templates/    (HTML templates for web UI)
```

### Process Flow

```
User Input (File or URL)
    │
    ▼
┌─────────────────────────────┐
│ 1. AudioHandler.load()      │  Validates format, size, duration
│    AudioHandler.extract()   │  Extracts audio from video (if needed)
│    AudioHandler.chunk()     │  Splits into 25s WAV chunks (3s overlap)
└─────────────────────────────┘
    │
    ▼
┌─────────────────────────────┐
│ 2. Transcriber              │  Sends chunks to Groq Whisper API
│    .transcribe_chunks()     │  Handles rate limits with retry + backoff
│    ._trim_overlap()         │  Deduplicates overlap zones (sliding window)
│    ._merge_segments()       │  Two-pass dedup (timestamp + text)
└─────────────────────────────┘
    │
    ▼
┌─────────────────────────────┐
│ 3. Summarizer.summarize()   │  Llama 3.3 via Groq (or Cohere fallback)
│    ._map_reduce()           │  For long content: split → summarize → combine
│    .translate_segments()    │  Optional translated subtitles (batch SRT)
└─────────────────────────────┘
    │
    ▼
┌─────────────────────────────┐
│ 4. OutputManager            │  Writes transcript, script, SRT, VTT,
│    .write() / .write_to_memory()  │  summaries (plain + MD), ZIP bundle
└─────────────────────────────┘
    │
    ▼
  Delivery (Web download / Telegram file send)
```

---

## 3. Core Pipeline (core/)

### 3.1 audio.py — AudioHandler

**File:** `core/audio.py` (216 lines)

#### Data Classes

- **`AudioFileInfo`** — Stores metadata about the input file.
  - `path: Path` — Path to the file
  - `file_type: str` — `"audio"` or `"video"`
  - `extension: str` — Lowercase file extension (e.g. `.mp3`, `.mp4`)
  - `size_bytes: int`
  - `duration_ms: int`
  - `duration_seconds: float`
  - `duration_minutes: float` (property)
  - `size_mb: float` (property)

- **`ChunkInfo`** — Metadata about a single audio chunk.
  - `path: Path` — Path to WAV chunk file
  - `index: int` — Chunk sequence number
  - `start_ms: int` — Content start time (ms, excluding overlap)
  - `end_ms: int` — Content end time (ms, excluding overlap)
  - `chunk_start_ms: int` — Actual chunk start time (including overlap)
  - `chunk_end_ms: int` — Actual chunk end time (including overlap)
  - `has_leading_overlap: bool` — True if chunk extends before content start
  - `has_trailing_overlap: bool` — True if chunk extends after content end

#### Constants

- **`CHUNK_DURATION_MS = 25000`** — 25 seconds of content per chunk
- **`CHUNK_OVERLAP_MS = 3000`** — 3 seconds of overlap on each side
- Chunks exported as WAV (PCM 16-bit, 16kHz sample rate) for Whisper compatibility

#### Methods

| Method | Description |
|---|---|
| `__init__(job_id, max_size_mb, max_duration_min)` | Creates temp directory for this job |
| `cleanup()` | Removes entire temp directory |
| `load(path)` | Validates file exists, extension supported (`config.ALL_SUPPORTED_EXTENSIONS`), size ≤ max, duration ≤ max. Returns `AudioFileInfo`. |
| `_get_duration_ms(path)` | Uses `ffprobe` to get duration. Falls back to estimating from file size (~1 min per MB). |
| `extract_audio(video_info)` | Extracts audio from video using `ffmpeg -vn -acodec libmp3lame -q:a 2`. Only for video files. |
| `chunk(audio_path)` | Splits audio into overlapping WAV chunks using `ffmpeg pcm_s16le -ar 16000`. Returns list of `ChunkInfo`. |
| `_extract_segment(input, output, start_ms, end_ms)` | Extracts a specific segment from audio via ffmpeg. |
| `_export_wav(input, output)` | Converts audio to WAV format. |
| `prepare(input_path)` | Convenience: `load()` → `extract_audio()` (if video) → `chunk()`. Returns `(AudioFileInfo, list[ChunkInfo])`. |

#### Why Overlapping Chunks?

Whisper can cut words at chunk boundaries. The 3-second overlap on each side ensures every word appears in at least one full context window. The overlap zone is later excluded during the merging step in the transcriber.

---

### 3.2 transcriber.py — Transcriber

**File:** `core/transcriber.py` (317 lines)

#### Data Classes

- **`TranscriptionSegment`** — A single transcribed segment with timing.
  - `text: str`
  - `start: float` — Start time in seconds (absolute, adjusted)
  - `end: float` — End time in seconds (absolute, adjusted)
  - `confidence: float`
  - `avg_logprob: float`

- **`TranscriptionResult`** — Complete transcription result.
  - `segments: list[TranscriptionSegment]`
  - `detected_language: str` — e.g. `"en"`, `"ar"`
  - `lang_probability: float` — Confidence in language detection
  - `full_text: str` — Merged full transcript text
  - `language_name: str` (property) — Human-readable name from `config.LANG_NAMES`

#### Methods

| Method | Description |
|---|---|
| `__init__(api_key, language_hint)` | Creates Groq client. `language_hint` is passed to Whisper for consistency. |
| `transcribe_chunks(chunks, progress_callback)` | Main method: iterates all chunks, transcribes each, adjusts timestamps to absolute positions, filters overlap segments (using `TOLERANCE = 0.05s`), handles rate limits with retry (3 attempts, 2/4/6s backoff), sleeps 0.3s between chunks (Groq free tier: ~20 req/min). Returns merged `TranscriptionResult`. |
| `_transcribe_chunk(chunk)` | Sends single chunk to Groq Whisper with `response_format=verbose_json`, `temperature=0.0`. Returns `TranscriptionResult` with segments. Supports Groq rate limit detection and has 3 internal retries (3/6/9s backoff). |
| `transcribe_file(audio_path)` | Convenience: chunks a file then transcribes. |

#### Overlap Deduplication

Two-pass algorithm in `_merge_segments()`:

1. **Timestamp-based pass:** Drop segments whose `start` time overlaps with the previous segment's `end` (100ms tolerance). Catches exact chunk boundary duplicates.
2. **Text-based pass (`_trim_overlap`):** For each consecutive pair of segments, compares the tail of the previous text with the head of the current text using a sliding window of `DEDUP_WINDOW_WORDS = 20`. Uses normalized comparison (lowercase, no punctuation, Arabic character support). Tries decreasing overlap lengths from max down to 3 words. When a match is found, trims the duplicated words from the current segment.

Language detection is taken from the first chunk's response. All subsequent chunks inherit the same language hint for consistency.

---

### 3.3 summarizer.py — Summarizer

**File:** `core/summarizer.py` (381 lines)

#### Data Classes

- **`LangSummary`** — Summary in one language.
  - `lang_code: str` — e.g. `"en"`, `"ar"`, `"ar-eg"`
  - `lang_name: str` — e.g. `"English"`, `"Arabic"`
  - `plain: str` — Plain text summary
  - `markdown: str` — Markdown-formatted summary

- **`SummaryResult`** — All summaries for a job.
  - `summaries: dict[str, LangSummary]` — Keyed by language code
  - `get(lang_code)` — Get summary for a specific language

#### LLM Providers

| Provider | Model | Key Config | Priority |
|---|---|---|---|
| **Groq** | `llama-3.3-70b-versatile` | `GROQ_API_KEYS` | Primary |
| **Cohere** | `command-r-plus` | `COHERE_API_KEY` | Fallback |

If Groq fails (non-rate-limit), the system automatically falls back to Cohere if configured.

#### Methods

| Method | Description |
|---|---|
| `summarize(transcript, target_langs, groq_key, cohere_key, style, tone)` | Orchestrates per-language summarization. Returns `SummaryResult`. |
| `_get_client(groq_key, cohere_key)` | Tries Groq first, falls back to Cohere. |
| `_summarize_for_lang(transcript, lang, client, provider)` | If ≤ `SUMMARY_CHUNK_WORDS` (3500): single LLM call. If > 3500: map-reduce. Returns `(plain, markdown)`. |
| `_map_reduce(transcript, lang, client, provider, structured)` | Splits transcript into overlapping chunks of 3500 words (500 word overlap), summarizes each (Map), then combines and summarizes again (Reduce). |
| `_call_llm(client, provider, prompt, max_tokens)` | Calls Groq or Cohere. Handles rate limits by parsing `"try again in Xm"` from Groq 429 responses. Raises structured `SummarizerError("RATE_LIMIT:X")` on rate limit. Retries on failure with exponential backoff (base delay 2s). |
| `_direct_prompt(text, lang)` | Single-shot prompt template (supports English, Arabic, Egyptian Arabic). |
| `_direct_prompt_md(text, lang)` | Markdown version of direct prompt. |
| `_chunk_prompt(chunk, lang)` | Map-phase prompt for chunk summarization. |
| `_final_prompt(combined, lang)` | Reduce-phase prompt to combine chunk summaries. |
| `_final_prompt_md(combined, lang)` | Markdown reduce-phase prompt. |
| `translate_segments(segments, target_lang)` | Batch-translates transcript segments into another language. Returns valid SRT string with original timestamps. Batches of 50 segments per LLM call. Handles RTL languages (Arabic). Parses numbered list responses ("N. translated text"). Falls back to original text on failure. |
| `_srt_time(seconds)` | Static helper: converts float seconds to `HH:MM:SS,mmm` SRT format. |

#### Style & Tone Options

| Parameter | Values | Effect |
|---|---|---|
| `style` | `"detailed"` (default), `"brief"` | Controls depth of summary |
| `tone` | `"professional"` (default), `"casual"`, `"technical"` | Controls writing style |

#### Rate Limit Error Propagation

When Groq returns 429, the error message contains `"try again in Xm"`. The summarizer extracts the wait time and raises `SummarizerError("RATE_LIMIT:X")` with a structured prefix. This propagates up through `pipeline.py` (wrapped as `PipelineError("Processing failed: RATE_LIMIT:X")`) and is caught by handlers in `web/app.py` and `bot/handlers.py` which parse the `RATE_LIMIT:` prefix to show the user exactly how many minutes to wait.

---

### 3.4 output_manager.py — OutputManager

**File:** `core/output_manager.py` (244 lines)

#### Data Class

- **`OutputBundle`** — References to all output files on disk.
  - `job_id`, `output_dir`
  - `transcript_txt`, `script_txt` — Paths to transcript files
  - `summaries_txt: dict[str, Path]` — Plain text summaries by language
  - `summaries_md: dict[str, Path]` — Markdown summaries by language
  - `zip_path: Path` — ZIP bundle
  - `cleanup()` — Removes output directory

#### Methods

| Method | Description |
|---|---|
| `write(job_id, base_name, transcript, summary, output_dir)` | Writes all outputs to disk. Used by Telegram bot. Returns `OutputBundle`. |
| `write_to_memory(job_id, base_name, transcript, summary, include_transcript, include_subtitles, include_summary, translated_srt)` | Writes all outputs to in-memory bytes dict. Used by web API. Returns `dict[str, bytes]` (filename → content). |
| `_safe_filename(name)` | Sanitizes filename: removes special chars, truncates to 40 chars. |
| `_format_time(seconds)` | Converts to `MM:SS.mmm` or `HH:MM:SS.mmm`. |
| `_to_srt(segments)` | Converts segments to SubRip subtitle format. |
| `_to_vtt(segments)` | Converts segments to WebVTT subtitle format. |
| `_srt_to_vtt(srt_content)` | Converts SRT string to VTT format (for translated subtitles). |

#### In-Memory File Naming Convention

```
{cleanBase}-{SHORTID}-{DURmin}-{TYPE}-{LANG}.ext
```

Examples:
- `lecture-A3F9B1-04min-transcript.txt`
- `lecture-A3F9B1-04min-script.txt`
- `lecture-A3F9B1-04min-subtitles-EN.srt`
- `lecture-A3F9B1-04min-subtitles-EN.vtt`
- `lecture-A3F9B1-04min-summary-EN.txt`
- `lecture-A3F9B1-04min-summary-EN.md`
- `lecture-A3F9B1-04min-audioscribe.zip`

Where:
- `cleanBase`: Original filename cleaned (max 20 chars)
- `SHORTID`: First 6 chars of job ID (uppercased)
- `DURmin`: Duration rounded to minutes (zero-padded, e.g. `04min`)
- `TYPE`: `transcript`, `script`, `subtitles`, `summary`, `audioscribe`
- `LANG`: Language code uppercased (e.g. `EN`, `AR`, `AREG`)

#### Branding Footer

All text files appended with:
```
---
🎙 Generated by AudioScribe · github.com/Mo-Abdalkader/AudioScribe
```

#### Output Files per Job

| File | Description | Generated Conditions |
|---|---|---|
| `*_transcript.txt` | Full transcript with `[MM:SS.mmm]` timestamps | `include_transcript=True` |
| `*_script.txt` | Clean transcript without timestamps | `include_transcript=True` |
| `*_transcript.srt` | SubRip subtitle format | `include_subtitles=True` |
| `*_transcript.vtt` | WebVTT subtitle format | `include_subtitles=True` |
| `*_subtitles-{LANG}.srt` | Translated subtitles (SRT) | `subtitle_langs` specified |
| `*_subtitles-{LANG}.vtt` | Translated subtitles (VTT) | `subtitle_langs` specified |
| `*_summary-{LANG}.txt` | Plain text summary | `include_summary=True` |
| `*_summary-{LANG}.md` | Markdown summary | `include_summary=True` |
| `*_audioscribe.zip` | All files bundled | Always (if any files exist) |

---

### 3.5 pipeline.py — Pipeline

**File:** `core/pipeline.py` (249 lines)

#### Data Classes

- **`PipelineResult`** — Full result from disk-based pipeline.
  - `job_id`, `file_info`, `transcript`, `summary`, `bundle`

#### Methods

| Method | Description | Used By |
|---|---|---|
| `run(input_path, target_langs, base_name, job_id, progress_cb, groq_key, cohere_key, summary_style, summary_tone)` | Full pipeline → writes to disk → returns `PipelineResult`. Stages: validate → chunk → transcribe → summarize → output. | Legacy, not actively used |
| `run_in_memory(input_path, target_langs, base_name, job_id, progress_cb, groq_key, cohere_key, summary_style, summary_tone, max_size_mb, max_duration_min, mode, subtitle_langs)` | Full pipeline → returns in-memory bytes. Returns `(TranscriptionResult, SummaryResult|None, dict[str, bytes])`. | Web API + Telegram URL processing |

#### `run_in_memory()` Modes

| `mode` Value | Transcript | Subtitles (SRT/VTT) | Summary |
|---|---|---|---|
| `"full"` (default) | ✓ | ✓ | ✓ |
| `"transcript"` | ✓ | ✓ | ✗ |
| `"subtitles"` | ✗ | ✓ | ✗ |
| `"summary"` | ✗ | ✗ | ✓ |

---

## 4. Web Interface (web/)

### 4.1 app.py — FastAPI Server

**File:** `web/app.py` (907 lines)

#### Application Setup

- **FastAPI app** with CORS middleware (all origins allowed), static file mounting at `/static`
- **Startup event** (`@app.on_event("startup")`): Creates temp directory, starts background job cleanup loop, initializes Telegram bot
- **Shutdown event** (`@app.on_event("shutdown")`): Stops Telegram bot gracefully
- **OpenAPI docs** available at `/api/docs`

#### Job Store

- **In-memory dict** (`_jobs: dict[str, dict]`): Stores all job data
- Each job entry contains: `status`, `progress` (list of messages), `files` (dict of filename→bytes), `error`, `created_at`
- **TTL:** 2 hours (`JOB_TTL_SECONDS = 7200`)
- **Cleanup loop:** Runs every 30 minutes, removes expired jobs

#### Routes

**Web UI:**
| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `index.html` |
| GET | `/favicon.ico` | Serves favicon (falls back to logo.png) |
| GET | `/manifest.json` | PWA manifest |
| GET | `/sw.js` | Service Worker (with `Service-Worker-Allowed: /` header) |
| GET | `/offline.html` | PWA offline fallback |

**Health & Limits:**
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Smart health check — checks ffmpeg, yt-dlp, Groq keys, Telegram. Returns 200 (ok) or 503 (degraded). |
| GET | `/api/limits` | Rate limit status for the requesting IP: `limit`, `used`, `remaining`, `reset_in_seconds`, `window_hours` |

**Processing:**
| Method | Path | Description |
|---|---|---|
| POST | `/api/process` | Upload file for processing |
| POST | `/api/process-url` | Submit URL for processing |
| GET | `/api/status/{job_id}` | Poll job status & progress |
| GET | `/api/download/{job_id}/{filename}` | Download result file |
| DELETE | `/api/job/{job_id}` | Delete job from memory |

**Telegram:**
| Method | Path | Description |
|---|---|---|
| POST | `/webhook` | Telegram webhook receiver (validates secret token in production) |

#### `/api/process` Request Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file` | UploadFile | required | Audio/video file |
| `langs` | Form | `"en"` | Target languages for summary: `"en"`, `"ar"`, `"ar-eg"`, `"en,ar"` |
| `source_lang` | Form | `""` | Source language hint: `""` (auto), `"en"`, `"ar"`, `"ar-eg"` |
| `style` | Form | `"both"` | Summary format: `"plain"`, `"md"`, `"both"` |
| `summary_style` | Form | `"detailed"` | Summary detail: `"brief"`, `"detailed"` |
| `summary_tone` | Form | `"professional"` | Summary tone: `"professional"`, `"casual"`, `"technical"` |
| `groq_key` | Form | `""` | User's own Groq API key (optional, bypasses rate limit) |
| `cohere_key` | Form | `""` | User's own Cohere API key (optional) |
| `mode` | Form | `"full"` | Processing mode: `"full"`, `"transcript"`, `"subtitles"`, `"summary"` |
| `subtitle_langs` | Form | `""` | Extra subtitle languages: `"en"`, `"ar"`, `"ar-eg"` |

**Response:** `{"job_id": "uuid", "status": "processing"}`

#### `/api/process-url` Request Parameters

Same as `/api/process` but with `url: Form` instead of `file`.

#### `/api/status/{job_id}` Response

```json
{
  "job_id": "abc123",
  "status": "processing" | "done" | "error",
  "progress": ["Validating and chunking...", "Transcribing 3/12 (25%)", "..."],
  "error": null | "error message",
  "detected_lang": "en",
  "lang_name": "English",
  "transcript_preview": "First 500 chars...",
  "files": ["lecture-A3F9-04min-transcript.txt", "lecture-A3F9-04min-summary-EN.md", "lecture-A3F9-04min-audioscribe.zip"]
}
```

#### Background Processing

Both `/api/process` and `/api/process-url` use `BackgroundTasks` to run the pipeline asynchronously. The background tasks:
1. Run `pipeline.run_in_memory()` in a thread pool (`asyncio.to_thread`)
2. Update `_jobs[job_id]` with progress messages, final results, and error states
3. Filter output files by the requested `style` (plain/md/both)
4. Clean up temp files in `finally` blocks
5. Parse `RATE_LIMIT:` structured errors and convert to user-friendly messages

#### Health Check (`/health`) Response

```json
{
  "status": "ok" | "degraded",
  "checks": {
    "ffmpeg": "ok" | "missing" | "error",
    "yt_dlp": "ok" | "missing (YouTube disabled)",
    "groq_keys": "2 key(s) configured" | "missing",
    "telegram": "configured" | "missing",
    "active_jobs": 3
  }
}
```

Returns HTTP 200 if all critical components (ffmpeg, Groq key) are healthy, HTTP 503 if degraded.

---

### 4.2 templates/index.html

**File:** `web/templates/index.html` (655 lines)

#### HTML Structure

| Section | Lines | Description |
|---|---|---|
| `<head>` | 1-28 | PWA meta tags, SEO meta tags, Google Fonts (Syne + JetBrains Mono), stylesheet |
| Navigation (`<nav>`) | 35-58 | Sticky nav with logo, links (Features, How It Works, Try Now), theme toggle (light/dark) |
| Hero | 61-100 | Background grid pattern, glowing orbs, badge ("Powered by Whisper + Llama 3.3"), title, subtitle, CTA buttons ("Start Processing" + "Use on Telegram"), format tags (MP3, WAV, M4A, etc.), animated waveform |
| Features | 103-156 | 8 feature cards in a grid: Whisper Large-v3, AI Summarization, Egyptian Dialect, Multi-Language, Multiple Formats, Your API Key, Telegram Bot, ZIP Bundle |
| How It Works | 159-263 | Animated flow diagram with staggered CSS animations. Steps: Input (File Upload or URL) → Chunk → Transcribe → Merge → Summarize → Package → Output (Transcript, Summary, Subtitles, ZIP). Stats bar: 90+ Languages, 50 MB, 60 min, 0 GPU |
| Developer / About | 266-299 | Author card: photo, name, bio, LinkedIn/GitHub/Repo links |
| Upload / Process | 302-600 | Main processing card with 5 steps (see below) |
| Telegram CTA | 603-619 | Call-to-action card to use the Telegram bot |
| Footer | 622-651 | Brand, author info with LinkedIn link, tech stack tags (Groq, Whisper, Llama 3.3, FastAPI), copyright |

#### Process Card UI (5 Steps)

| Step | Content |
|---|---|
| **Step 1: Source** | Drop zone (drag-and-drop or file picker) with file preview (name, size, estimated processing time) + URL input with platform hints (🎬 YouTube, 📁 Google Drive, 📦 Dropbox, 🔗 Direct URL). File/URL mutex — selecting one clears the other. |
| **Step 2: Mode** | 4 mode cards: Full Processing ⚡, Transcript+Subtitles 📄, Subtitles Only 🎞, Summary Only 🧠. Selecting a mode hides/shows relevant options. |
| **Step 3: Language** | 3-column grid: Audio Language (Auto/English/Arabic/Egyptian), Summary Language (English/Arabic/Egyptian/Both), Translated Subtitles (None/English/Arabic/Egyptian with optional badge) |
| **Step 4: Summary Options** | 3-column grid: Detail Level (Brief ⚡ / Detailed 📝), Tone (Professional 💼 / Casual 😊 / Technical 🔬), File Format (Plain .txt / Markdown .md / Both) |
| **Step 5: API Key** | Optional password input for user's own Groq key. Link to console.groq.com |

Rate limit info bar at the top (green = OK, amber = near limit).

#### Results Area

| Component | Description |
|---|---|
| Progress Card | Spinner, status text, scrolling log, animated gradient progress bar |
| Results Card | Checkmark animation, metadata (language, duration), transcript preview (first 500 chars), download list (ZIP first, then transcript, then summaries), export actions |
| Export Actions | "Export to Notion" (fetches MD → copies to clipboard → opens notion.so/new), "Copy Markdown" (copies MD summary to clipboard) |
| Session History | Shows last 10 jobs with ZIP download links, "Clear" button |
| Error Card | Error icon, message, "Try Again" button |

---

### 4.3 static/js/app.js — Frontend Logic

**File:** `web/static/js/app.js` (790 lines)

#### State Variables

| Variable | Description |
|---|---|
| `selectedFile` | Currently selected File object |
| `currentJobId` | Active job being polled |
| `pollInterval` | setInterval handle for status polling |
| `_sessionHistory[]` | In-memory array of completed jobs (max 10) |
| `_lastMarkdownContent` | Cached markdown for export (lazy-loaded) |
| `_lastJobData` | `{jobId, files[]}` of last completed job |

#### Key Functions

| Function | Description |
|---|---|
| `initWaveform()` | Creates animated CSS waveform bars with random heights and keyframes in `#heroWaveform` |
| `toggleTheme()` / `initTheme()` | Toggles dark/light mode via CSS class on `<html>`, persists to `sessionStorage` |
| `fetchLimits()` | Fetches `/api/limits`, renders rate limit info bar (green/amber/red) |
| `handleFileDrop()`, `handleFileSelect()` | Drag-and-drop + file picker. Validates file type and size client-side. Shows preview with estimated time. |
| `submitURL()` | Handles URL input (Enter key + button click). Validates URL format. |
| `clearSource()` | File/URL mutex: clears one when the other is used. Shows toast notification. |
| `startProcessing()` | Validates inputs, POSTs to `/api/process` with FormData, starts polling |
| `startProcessingUrl()` | POSTs to `/api/process-url` with URL data, starts polling |
| `pollStatus()` | Polls `/api/status/{job_id}` every 2 seconds. Updates progress bar with keyword-based stage detection. Parses "Transcribing 3/12 (25%)" for accurate mid-transcription progress percentage. |
| `showResults()` | Renders download links (ZIP first, then transcript, then summaries), adds to session history. Shows/hides export actions based on available file types. |
| `exportToNotion()` | Fetches `.md` summary file, copies to clipboard via `navigator.clipboard.writeText()`, opens `notion.so/new` in new tab |
| `copyMarkdown()` | Fetches `.md` summary, copies to clipboard, shows toast confirmation |
| `showToast(message, type)` | Custom toast notification with slide-in animation and auto-dismiss |
| `resetUI()` | Resets the UI to initial state |
| `clearHistory()` | Clears in-memory session history |

#### Progress Bar Accuracy

The progress bar uses keyword-based stage detection:
- "Chunking" → 5%
- "Transcribing X/Y (Z%)" → parses Z% and maps to 5-85% range
- "Summarizing" → 85-95%
- "Packaging" → 95-99%
- "Done!" → 100%

This provides accurate real-time progress feedback to the user.

---

### 4.4 static/css/style.css

**File:** `web/static/css/style.css` (1399 lines)

#### Design System

**CSS Custom Properties:**
```css
--bg: #080b0f;           /* Dark background */
--bg-card: #0e1218;      /* Card background */
--bg-elevated: #141a24;  /* Elevated surfaces */
--text: #e8edf5;         /* Primary text */
--text-sub: #8892a4;     /* Secondary text */
--accent: #4ade80;       /* Green accent (primary action) */
--accent-dim: #22c55e;   /* Dimmer accent */
--radius: 12px;           /* Border radius */
--radius-sm: 8px;
--transition: 0.2s ease; /* Default transition */
```

**Light mode:** Warm off-white background (`#f5f0ea`), adjusted contrast throughout.

#### Key Sections

| Section | Lines | Description |
|---|---|---|
| Reset & Variables | 1-80 | CSS reset, custom properties, light mode overrides |
| Navigation | 81-130 | Sticky, backdrop-blur, logo + links + theme toggle |
| Buttons | 131-180 | `.btn--primary` (green), `.btn--ghost`, `.btn--outline`, `.btn--full`, `.btn--sm`, `.btn--xs` |
| Hero | 181-300 | 2-column grid, bg grid pattern, glowing orbs, animated badge dot, waveform container, format tags |
| Features | 301-370 | 3-column grid with hover effects (card lifts, icon scales) |
| How It Works | 371-480 | Animated flow diagram with `.flowIn` keyframes, staggered `animation-delay` via `--d` CSS variable, flow arrows, stats bar with dividers |
| Developer Section | 481-530 | Avatar, bio, social links |
| Process Section | 531-900 | Dropzone (drag-over state: dashed border + scale), settings steps with numbered circles, mode cards with active state, language settings (3-column grid), summary options (3-column grid), advanced options (collapsible `<details>`), progress card (spinner + gradient shimmer), results card (checkmark + stagger), error card, session history |
| Telegram CTA | 901-950 | Card with glowing orb background |
| Footer | 951-1050 | 3-column grid with tech stack tags |
| Export Actions | 1051-1120 | Notion + Copy Markdown buttons |
| Responsive | 1121-1399 | Breakpoints at 900px, 700px, 600px, 500px, 400px |
| Theme Toggle | throughout | `html.light` class overrides all colors |

#### Animations

- `.flowIn` — Elements slide up and fade in (used in flow diagram steps)
- `.progress-bar-shimmer` — Animated gradient shift on the progress bar
- `.badge-dot-pulse` — Pulsing green dot on the hero badge
- `.hero-orb` — Slow floating/rotating background orbs
- Staggered delays via CSS `--d` custom property

---

### 4.5 PWA (manifest.json + sw.js)

#### manifest.json

```json
{
  "name": "AudioScribe",
  "short_name": "AudioScribe",
  "description": "AI-powered audio & video transcription and summarization",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#080b0f",
  "theme_color": "#080b0f",
  "orientation": "portrait-primary",
  "icons": [
    { "src": "/static/images/logo.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
    { "src": "/static/images/logo.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ],
  "categories": ["productivity", "utilities"],
  "shortcuts": [
    {
      "name": "Transcribe File",
      "short_name": "Transcribe",
      "url": "/#process",
      "icons": [{ "src": "/static/images/logo.png", "sizes": "192x192" }]
    }
  ]
}
```

Enables "Add to Home Screen" on iOS/Android and "Install" prompt on Chrome desktop.

#### sw.js — Service Worker

| Event | Behavior |
|---|---|
| **Install** | Precaches `/`, CSS, JS, offline.html |
| **Activate** | Cleans old caches |
| **Fetch (navigation)** | Network-first with offline.html fallback |
| **Fetch (static assets)** | Cache-first |
| **Fetch (`/api/*`, `/webhook`)** | Network only (never cached) |

#### offline.html

Minimal styled page with centered message and "Try Again" button, shown when the app is opened offline.

---

## 5. Telegram Bot (bot/)

### 5.1 main.py — Bot Setup

**File:** `bot/main.py` (106 lines)

#### Registered Commands

| Command | Description |
|---|---|
| `/start` | Welcome & intro |
| `/help` | Full help |
| `/info` | Project & developer info |
| `/settings` | Open settings panel |
| `/mode` | Set processing mode (full/transcript/subtitles/summary) |
| `/lang` | Set output language |
| `/subtitle_lang` | Set translated subtitle language |
| `/style` | Set summary format |
| `/summary_style` | Set detail level (brief/detailed) |
| `/summary_tone` | Set tone (professional/casual/technical) |
| `/key` | Set your Groq API key |
| `/cancel` | Cancel current processing |

#### Media Handlers

- **File handler** (`filters.AUDIO | VOICE | VIDEO | VIDEO_NOTE | Document.ALL`): Processes uploaded files
- **Text handler** (`filters.TEXT & ~filters.COMMAND`): Detects and processes URLs (YouTube, Google Drive, Dropbox, direct links)

#### Logging

- Console: WARNING level
- File (`audioscribe.log`): DEBUG level with timestamp, level, module name

#### Bot Lifecycle

When running via `web/app.py`:
- **Production** (RAILWAY_PUBLIC_DOMAIN or PUBLIC_DOMAIN set): Webhook mode with secret token validation
- **Local dev**: Polling mode

When running via `bot/main.py`: Polling mode only.

---

### 5.2 handlers.py — All Handlers

**File:** `bot/handlers.py` (1251 lines)

#### In-Memory State

| Structure | Key | Values |
|---|---|---|
| `_user_prefs` | `user_id` | `lang`, `style`, `summary_style`, `summary_tone`, `groq_key`, `mode`, `subtitle_lang` |
| `_user_usage` | `user_id` | `daily_used`, `last_reset` (date) |
| `_processing` | `user_id` | `bool` — processing lock |

#### User Preferences (defaults)

```python
{
    "lang": "auto",
    "style": "md",
    "summary_style": "detailed",
    "summary_tone": "professional",
    "groq_key": None,
    "mode": "full",
    "subtitle_lang": "none",
}
```

#### Command Handlers

| Handler | Description |
|---|---|
| `cmd_start()` | Welcome message with supported formats, free limits, premium info |
| `cmd_help()` | Full usage instructions, command list, user's current limits |
| `cmd_info()` | Project info, how it works (5 steps), tech stack, developer info |
| `cmd_lang()` | Sets output language with inline keyboard (5 options) |
| `cmd_style()` | Sets summary format with inline keyboard (3 options) |
| `cmd_summary_style()` | Sets detail level with inline keyboard (2 options) |
| `cmd_summary_tone()` | Sets tone with inline keyboard (3 options) |
| `cmd_mode()` | Sets processing mode with inline keyboard (4 options) |
| `cmd_subtitle_lang()` | Sets translated subtitle language with inline keyboard (4 options) |
| `cmd_settings()` | Shows all current settings with inline buttons to change each |
| `cmd_key()` | Sets/clears user's own Groq API key |
| `cmd_cancel()` | Cancels current processing |

#### Inline Keyboard Callbacks (`handle_callback`)

Callback data format: `setting:value` or `open:menu`.

Supported settings: `lang`, `style`, `summary_style`, `summary_tone`, `mode`, `subtitle_lang`.

Quick actions: `action:send_file` (post-processing "Process another" button).

#### File Upload Flow (`handle_file`)

1. Check `_processing` lock (prevents concurrent processing per user)
2. Check daily usage against `FREE_DAILY_LIMIT` (default 10)
3. Validate file extension is in `ALL_SUPPORTED_EXTENSIONS`
4. Validate file size against user's limits
5. Increment usage counter
6. Set processing lock
7. Send status message with daily usage counter
8. Start background asyncio task: `_run_pipeline()`

#### URL Detection (`_extract_url`)

Detection order (priority):
1. **YouTube** — `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/`
2. **Direct media link** — URL ending in known extension (.mp3, .wav, .m4a, etc.)
3. **Google Drive** — `/file/d/{id}` or `?id={id}` forms
4. **Dropbox** — `dropbox.com/s/...`

Returns `(normalized_url, url_type)` where `url_type` is one of: `"youtube"`, `"direct"`, `"gdrive"`, `"dropbox"`.

#### URL Pipeline Flow (`_run_pipeline_from_url`)

1. Call `_download_from_url()` with URL type
2. Run `pipeline.run_in_memory()` with user's preferences
3. Send results via `_send_results_from_url()`

#### Results Sending Flow (`_send_files_to_chat`)

Order of delivery (optimized for mobile):
1. **Summaries first** — Most readable, sent as document files with language caption
2. **Transcript** — With timestamps, sent as document
3. **SRT subtitles** — Directly playable in VLC/MX Player
4. **ZIP bundle** — Last, all files in one package
5. **Done message** — Summary with elapsed time, language, file count, action keyboard ("Process another" + "Settings")

#### Language Detection Card

Sent after transcription completes (before summarization). Shows:
- Confidence color: 🟢 ≥90%, 🟡 ≥70%, 🔴 <70%
- Detected language name + confidence percentage
- Word count
- First 250 characters as preview (cut at last space)

#### Error Handling for Rate Limits

When `RATE_LIMIT:X` structured error is caught:
- If user has their own key: "Your Groq key also reached its daily limit. Wait ~X minutes."
- If user doesn't have a key: "Wait ~X minutes, or add your own free Groq key." Includes instructions for getting a key.

#### URL Download Functions

**`_download_youtube(url, status_msg, max_size_mb)`:**
1. Pre-checks video via `yt-dlp --print` for title, duration, size
2. Rejects videos exceeding duration limit before downloading
3. Downloads audio-only with `--extract-audio --audio-format m4a --audio-quality 0`
4. Sets `--max-filesize` flag
5. Handles: private/unavailable, age-restricted, too large
6. Returns `(tmp_path, error_message)`

**`_download_from_url(url, status_msg, url_type, max_size_mb)`:**
- **Google Drive**: Detects private files via `Content-Type: text/html` check. Converts to direct download URL.
- **Dropbox**: Replaces `?dl=0` with `?dl=1`.
- **Direct URLs**: Streams with httpx in 256KB chunks, checks size per chunk (no full RAM load). Resolves extension from Content-Type or URL suffix.

---

## 6. Configuration Reference

**File:** `config.py` and `.env`

### Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Yes* | Telegram bot token from @BotFather. Required unless `WEB_ONLY=1`. |
| `GROQ_API_KEYS` | — | Yes* | Groq API key(s). Comma-separated for multiple keys. Required unless `COHERE_API_KEY` is set. Also reads from `GROQ_API_KEY` for backward compatibility. |
| `COHERE_API_KEY` | — | No | Cohere API key for fallback summarizer. |

(*At least one API key must be configured — either Groq or Cohere.)

### Audio Processing

| Variable | Default | Description |
|---|---|---|
| `MAX_FILE_SIZE_MB` | 50 | Maximum direct file upload size (MB) |
| `MAX_AUDIO_DURATION_MINUTES` | 60 | Maximum audio/video duration (minutes) |
| `MAX_UPLOAD_SIZE_MB` | 50 | FastAPI upload limit (MB) |
| `FREE_MAX_URL_SIZE_MB` | 200 | Max URL download for free users (MB) |
| `PREMIUM_MAX_URL_SIZE_MB` | 2000 | Max URL download for own-key users (MB) |
| `CHUNK_DURATION_MS` | 25000 | Audio chunk duration (milliseconds) |
| `CHUNK_OVERLAP_MS` | 3000 | Overlap between consecutive chunks (milliseconds) |
| `DEDUP_WINDOW_WORDS` | 20 | Sliding window size for overlap deduplication (# of words) |

### Summarization

| Variable | Default | Description |
|---|---|---|
| `SUMMARY_CHUNK_WORDS` | 3500 | Max words before map-reduce is triggered |
| `SUMMARY_CHUNK_OVERLAP` | 500 | Word overlap between map-reduce chunks |
| `SUMMARY_CHUNK_MAX_TOKENS` | 1024 | Max tokens per chunk summary |
| `SUMMARY_FINAL_MAX_TOKENS` | 2048 | Max tokens for final combined summary |
| `SUMMARY_TEMPERATURE` | 0.7 | LLM creativity (0=deterministic, 1=creative) |

### Retry Settings

| Variable | Default | Description |
|---|---|---|
| `PROVIDER_MAX_RETRIES` | 3 | LLM call retry attempts |
| `PROVIDER_RETRY_BASE_DELAY` | 2.0 | Base delay for exponential backoff (seconds). Actual delay = base × 2^attempt |

### Rate Limiting

| Variable | Default | Description |
|---|---|---|
| `FREE_DAILY_LIMIT` | 10 | Daily request limit per Telegram user |
| `RATE_LIMIT_WINDOW_SECONDS` | 3600 | Rate limit window (seconds) |

### Web Server

| Variable | Default | Description |
|---|---|---|
| `PORT` | 8000 | Web server port (Railway sets automatically) |
| `WEB_SECRET_KEY` | `"change-me"` | Webhook validation secret token |
| `RAILWAY_PUBLIC_DOMAIN` | — | Railway public domain (triggers webhook mode) |
| `PUBLIC_DOMAIN` | — | Custom domain (alternative to RAILWAY_PUBLIC_DOMAIN) |
| `WEB_ONLY` | — | Set to `1` to disable Telegram bot entirely |

### Output

| Variable | Default | Description |
|---|---|---|
| `OUTPUT_TEMP_DIR` | `/tmp/audioscribe` | Temp directory for output files |

### Supported Formats

Defined in `config.py`:

**Audio:** `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.aac`, `.wma`, `.opus`, `.webm`

**Video:** `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`

### LLM Models

| Model | Config Key | Value |
|---|---|---|
| Whisper | `WHISPER_MODEL` | `"whisper-large-v3"` |
| Groq LLM | `GROQ_MODEL` | `"llama-3.3-70b-versatile"` |
| Cohere LLM | `COHERE_MODEL` | `"command-r-plus"` |

### Language Names

```python
LANG_NAMES = {
    "en": "English",
    "ar": "Arabic",
    "ar-eg": "Arabic",  # Displayed as "Egyptian Arabic"
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
}
```

Whisper language hints:
```python
WHISPER_LANGUAGE_HINTS = {
    "ar": "arabic",
    "ar-eg": "arabic",
    "en": "english",
    "fr": "french",
}
```

---

## 7. Complete API Reference

### POST /api/process — Upload File

**Request:** `multipart/form-data`

| Field | Type | Default | Description |
|---|---|---|---|
| `file` | File | required | Audio/video file |
| `langs` | str | `"en"` | Target summary languages (comma-separated) |
| `source_lang` | str | `""` | Source language hint (empty = auto-detect) |
| `style` | str | `"both"` | Summary format: `plain`, `md`, `both` |
| `summary_style` | str | `"detailed"` | `brief` or `detailed` |
| `summary_tone` | str | `"professional"` | `professional`, `casual`, `technical` |
| `groq_key` | str | `""` | Your Groq API key (optional) |
| `cohere_key` | str | `""` | Your Cohere API key (optional) |
| `mode` | str | `"full"` | `full`, `transcript`, `subtitles`, `summary` |
| `subtitle_langs` | str | `""` | Extra subtitle languages (comma-separated) |

**Response (200):** `{"job_id": "uuid", "status": "processing"}`

**Errors:** 400 (invalid format), 413 (file too large), 429 (rate limit)

### POST /api/process-url — Submit URL

Same fields as above, but:
| Field | Type | Default | Description |
|---|---|---|---|
| `url` | str | required | YouTube, Google Drive, Dropbox, or direct media URL |

Additional validation: SSRF protection (blocks private IPs, metadata endpoints).

### GET /api/status/{job_id} — Poll Status

**Response (200):** See section 4.1 for full schema.

**Error:** 404 (job not found)

### GET /api/download/{job_id}/{filename} — Download File

**Response (200):** File content as binary with `Content-Disposition: attachment`.

**Errors:** 404 (job or file not found), 400 (job not complete)

### DELETE /api/job/{job_id} — Delete Job

**Response (200):** `{"deleted": job_id}`

### GET /api/limits — Rate Limit Status

**Response (200):**
```json
{
  "limit": 10,
  "used": 3,
  "remaining": 7,
  "reset_in_seconds": 2847,
  "window_hours": 1
}
```

### GET /health — Health Check

**Response (200 or 503):** See section 4.1 for full schema.

---

## 8. URL Sources & Downloading

### 8.1 YouTube

| Aspect | Detail |
|---|---|
| **Detection** | `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/` |
| **Tool** | yt-dlp subprocess |
| **Format** | `bestaudio/best`, extracted to M4A (best quality) |
| **Pre-check** | `yt-dlp --print` gets title, duration, approximate size before downloading |
| **Size limit** | `--max-filesize` flag |
| **Error handling** | Private/unavailable → clear error message. Age-restricted → clear error message. Timeout → clear error message. |
| **Duration limit** | Rejected during pre-check if > `MAX_AUDIO_DURATION_MINUTES` |

### 8.2 Google Drive

| Aspect | Detail |
|---|---|
| **Detection** | `/file/d/{id}`, `/uc?id={id}`, `docs.google.com/uc?id=` |
| **Normalization** | `https://drive.google.com/uc?export=download&id={id}` |
| **Private file detection** | Checks `Content-Type` before reading body. If `text/html` → file is private/invalid. |
| **Extension detection** | From `Content-Type` header |
| **Limitation** | Files > ~750MB may trigger Google's virus scan warning page |

### 8.3 Dropbox

| Aspect | Detail |
|---|---|
| **Detection** | `dropbox.com/s/...` |
| **Normalization** | Replaces `?dl=0` with `?dl=1` (or appends `?dl=1`) |
| **Extension detection** | From `Content-Type` header |

### 8.4 Direct URLs

| Aspect | Detail |
|---|---|
| **Detection** | URL ending in known audio/video extension |
| **Download** | Streaming httpx with 256KB chunks, size check per chunk |
| **Size limit** | Downloaded size checked incrementally — no full RAM load |
| **Extension** | From URL suffix if in `ALL_SUPPORTED_EXTENSIONS`, otherwise from Content-Type |

### SSRF Validation (both web + bot)

- Resolves hostname to IP via `socket.gethostbyname()`
- Blocks private networks: 10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x
- Blocks IPv6 loopback/link-local: ::1, fc00::/7
- Blocks metadata endpoints by hostname: `169.254.169.254`, `metadata.google.internal`
- Only allows `http` and `https` schemes

---

## 9. Output Formats & File Naming

### Transcript Formats

| Format | Description | Example |
|---|---|---|
| **Timestamped TXT** | Full transcript with `[MM:SS.mmm]` timestamps | `[01:23.456] This is a transcript line` |
| **Clean Script** | Concatenated text, no timestamps | `This is a transcript line.` |
| **SRT** | SubRip subtitle format with `HH:MM:SS,mmm` | `1\n00:01:23,456 --> 00:01:25,789\nText` |
| **VTT** | WebVTT subtitle format with `HH:MM:SS.mmm` | `00:01:23.456 --> 00:01:25.789\nText` |

### Summary Formats

| Format | Description |
|---|---|
| **Plain text (.txt)** | Straightforward summary without formatting |
| **Markdown (.md)** | Structured with `##` headers, bullet points, sections |

### ZIP Bundle

Contains all generated files in a single compressed archive using `ZIP_DEFLATED` compression.

---

## 10. Processing Modes

| Mode | Transcript Files | Subtitles (SRT/VTT) | AI Summary | Use Case |
|---|---|---|---|---|
| `full` (default) | ✓ | ✓ | ✓ | Full processing — everything |
| `transcript` | ✓ | ✓ | ✗ | Just text, no AI summary (saves API quota) |
| `subtitles` | ✗ | ✓ | ✗ | Only subtitle files for video players |
| `summary` | ✗ | ✗ | ✓ | Fastest — only AI-generated summary |

Modes are available both via the web UI (radio buttons in Step 2) and Telegram bot (`/mode` command).

---

## 11. Language Support

### Source Languages

Whisper Large-v3 supports 90+ languages. The web UI and Telegram bot offer language hints for:
- **Auto-detect** (recommended — Whisper detects automatically)
- **English** (`en`)
- **Arabic / MSA** (`ar`)
- **Egyptian Arabic** (`ar-eg`)

### Summary Output Languages

| Selection | Behavior |
|---|---|
| **English** | Summarize in English |
| **Arabic** | Summarize in Modern Standard Arabic |
| **Egyptian Arabic** | Summarize in Egyptian dialect (العامية المصرية) |
| **Both (EN + AR)** | Generate summaries in both English and Arabic simultaneously |

### Translated Subtitles

When enabled, generates an additional SRT/VTT pair in a different language alongside the original. Uses `summarizer.translate_segments()` which batches segments (50 per LLM call) and preserves original timestamps.

---

## 12. Security Features

### Rate Limiting

- **Algorithm:** Sliding window (in-memory)
- **Storage:** `_rate_store: dict[str, list[float]]` — IP → list of request timestamps
- **Window:** 1 hour (configurable)
- **Limit:** 10 requests/hour per IP
- **Bypass:** Users providing their own Groq API key bypass rate limit entirely
- **Web UI:** Rate limit info bar shows remaining requests
- **API endpoint:** `/api/limits` returns remaining count + reset time
- **Telegram:** Daily limit tracked per `user_id` in `_user_usage` dict

### SSRF Protection

Implemented in both `web/app.py` (`_validate_url_safe()`) and `bot/handlers.py` (lightweight check).

- Resolves hostname to IP via `socket.gethostbyname()`
- Blocks private network ranges using `ipaddress` module
- Blocks known metadata endpoints by hostname
- Only allows `http`/`https` schemes

### Webhook Validation

- `set_webhook()` called with `secret_token=WEB_SECRET_KEY[:32]`
- Every request to `/webhook` validates `X-Telegram-Bot-Api-Secret-Token` header
- Only active in production (when `RAILWAY_PUBLIC_DOMAIN` or `PUBLIC_DOMAIN` is set)

### Data Retention

- **Web:** Files stored in-memory in `_jobs` dict, expire after 2 hours
- **Cleanup:** Background task runs every 30 minutes
- **Telegram:** Temp files deleted in `finally` blocks after sending
- **Audio chunks:** Cleaned in `AudioHandler.cleanup()`
- **No database:** No disk persistence of user content
- All temp directories are removed after processing completes

---

## 13. Rate Limiting

### Web API (per IP)

| Parameter | Value |
|---|---|
| Algorithm | Sliding window |
| Limit | 10 requests/hour |
| Window | 3600 seconds |
| Storage | In-memory dict (IP → timestamps) |
| Exemption | Own-key users |
| API | `/api/limits` endpoint |

### Telegram Bot (per user)

| Parameter | Value |
|---|---|
| Limit | 10 files/day (configurable via `FREE_DAILY_LIMIT`) |
| Reset | Daily (based on calendar date) |
| Exemption | Users who set their own key via `/key` |
| Tracking | `_user_usage` dict |

### LLM API Rate Limits

**Groq free tier:**
- Whisper: ~20 requests/minute
- Llama 3.3: Varies by usage
- Retry strategy: Exponential backoff (2s × 2^attempt), max 3 retries
- Error propagation: Structured `RATE_LIMIT:X` messages with wait minutes

---

## 14. Deployment

### Railway (Recommended)

**One-click deploy:**
1. Fork this repository
2. Create a new Railway project → **Deploy from GitHub**
3. Set environment variables:
   - `TELEGRAM_BOT_TOKEN` — From @BotFather
   - `GROQ_API_KEYS` — From console.groq.com
   - `WEB_SECRET_KEY` — Random string for webhook validation
4. Railway automatically:
   - Detects `Procfile` → runs `python main.py`
   - Detects `nixpacks.toml` → installs ffmpeg + Python 3.11
   - Installs dependencies from `requirements.txt`
5. For Telegram webhook mode: Railway generates a `RAILWAY_PUBLIC_DOMAIN` automatically

### Manual / VPS

```bash
# Prerequisites
sudo apt install ffmpeg python3.11 python3.11-venv
# or brew install ffmpeg on macOS
# or download ffmpeg from https://ffmpeg.org/download.html on Windows

# Setup
git clone https://github.com/Mo-Abdalkader/AudioScribe.git
cd AudioScribe
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys

# Run
python main.py
# Web UI → http://localhost:8000
# Telegram bot starts in polling mode automatically
```

### nixpacks.toml

```toml
[phases.setup]
nixPkgs = ["ffmpeg", "python311"]
```

This ensures Railway/Nixpacks-based deployments automatically include ffmpeg and Python 3.11.

### Procfile

```
web: python main.py
```

Standard Heroku/Railway process definition.

### Environment Modes

| Configuration | Telegram Mode | Description |
|---|---|---|
| No `PUBLIC_DOMAIN` set | Polling | Local development |
| `RAILWAY_PUBLIC_DOMAIN` or `PUBLIC_DOMAIN` set | Webhook | Production (Railway/VPS) |
| `WEB_ONLY=1` | Disabled | Web-only mode (no Telegram) |

---

## 15. Version History

| Version | Key Changes |
|---|---|
| **v1** | Initial project — transcription + summarization, web UI, Telegram bot |
| **v2** | Bug fixes: `pipeline.run()` NameError, `url_type` not passed to download, `currentJobId` DELETE bug, duplicate config variable |
| **v3** | YouTube support (yt-dlp), Dropbox URL support, improved Google Drive private file detection (Content-Type check), Telegram inline keyboards for all settings, `ar-eg` in language keyboard, `handle_message` for URL text messages, streaming download instead of full RAM load |
| **v4** | Web URL input section (was missing from HTML), file/URL mutex, rate limit error messages with wait time and key instructions |
| **v5** | Job auto-cleanup (2hr TTL), rate limiting (10/hr per IP), SSRF protection (private IP blocking), `/api/limits` endpoint, rate limit info bar in web UI |
| **v6** | Smart health check (ffmpeg/yt-dlp/keys), webhook secret token validation, collapsible Advanced Options, estimated processing time under file preview |
| **v7** | Language detection card in Telegram (confidence + word count + preview), session history panel in web UI (last 10 jobs, ZIP download links) |
| **v8** | Dark/Light mode toggle (CSS variables + sessionStorage), Notion export, Copy Markdown button, custom toast duration |
| **v9** | Bug fixes: `pipeline.run()` missing `summary_style`/`summary_tone`, messy rate limit logic for own-key users, `_run_pipeline_from_url` error handling. Features: PWA support (manifest + service worker + offline page), visual flow diagram with animation, stats bar, developer card in UI, final README polish |

---

## 16. Known Limitations

### YouTube
- Age-restricted or private videos cannot be downloaded
- Very long videos (>60 min) are rejected by the duration limit
- yt-dlp must be installed on the server

### Google Drive
- Files larger than ~750MB may trigger a "virus scan warning" page from Google instead of a direct download
- The current code detects `text/html` content-type but not all warning page variants
- File must be shared with "Anyone with the link" permission

### Telegram
- Bot API limits files sent to users to 50MB. Large result ZIPs above 50MB will fail to send.
- Telegram's Bot API file upload limit is 20MB for receiving files from users

### In-Memory Job Storage
- If the server restarts while a job is processing, the result is lost
- Users would need to re-process
- Acceptable for demo/light use, not for production scale

### Rate Limiting
- Sliding window uses server memory. Multiple server instances would not share rate limit state
- A Redis-backed solution would be needed for horizontal scaling

### Summary Quality
- Map-reduce chunking can lose cross-chunk context for very long content (>3 hours)
- The 500-word overlap helps but doesn't fully solve the context fragmentation issue

---

## 17. Future Ideas

### Speaker Diarization
- `pyannote.audio` can identify "who said what"
- Would require running locally (not via API) and adds significant complexity

### Google Docs Export
- Similar to Notion export: fetch MD, convert to Google Docs format via API
- Requires OAuth which adds complexity

### Persistent Job Storage
- SQLite or Redis to survive server restarts
- Would also enable job history across sessions

### Multiple Groq Key Rotation
- `config.py` supports multiple keys in `GROQ_API_KEYS` but `get_next_groq_key()` always returns the first
- A round-robin or least-recently-used strategy would better distribute load

### WebSocket Progress
- Replace polling (`setInterval`) with WebSocket for real-time progress updates
- Reduces server load, feels more responsive

### Audio Preview
- Show a waveform visualization of the uploaded file before processing using Web Audio API

---

*No audio data is retained. All temporary files are deleted immediately after processing.*
