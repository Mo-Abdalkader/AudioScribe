# ◈ AudioScribe

**AI-powered audio & video transcription and summarization.**
Upload any audio or video file — get a full transcript, intelligent summary in English, Arabic, or Egyptian dialect, and a ZIP bundle. No GPU required.

---

## Features

| Feature | Details |
|---|---|
| 🎙 Transcription | Whisper Large-v3 via Groq API — fast, accurate, 90+ languages |
| 🧠 Summarization | Llama 3.3 70B — structured summaries, map-reduce for long files |
| 🇪🇬 Egyptian Dialect | Special support for Egyptian Arabic (العامية المصرية) |
| 🌐 Multi-Language | English, Arabic (MSA), or Egyptian output |
| 📝 Multiple Formats | Export as TXT, SRT (subtitles), VTT |
| 📦 Bundle Output | ZIP with transcript, summaries (TXT + Markdown), subtitles |
| 🔑 Your API Key | Use your own Groq API key for unlimited processing |
| 🤖 Telegram Bot | Send files directly via Telegram, get results back |
| 🌍 Web Interface | Dark, responsive web UI with drag-and-drop upload |
| ⚡ Smart Chunking | 25s overlapping chunks — no words lost at boundaries |
| 🎭 Summary Tone | Professional, casual, or technical |
| 📋 Brief/Detailed | Choose summary length |
| 🚀 Railway-Ready | One-command deploy, ffmpeg included, no GPU needed |

---

## How It Works

```
User (Web or Telegram)
        │
        ▼
   Upload file
        │
        ▼
  [1] Validate (format, size, duration)
        │
        ▼
  [2] Extract audio (if video — ffmpeg)
        │
        ▼
  [3] Chunk audio (25s segments, 3s overlap)
        │
        ▼
  [4] Transcribe each chunk (Groq Whisper Large-v3 API)
        │
        ▼
  [5] Merge + trim overlaps
        │
        ▼
  [6] Summarize (Groq Llama 3.3 70B — map-reduce for long content)
        │
        ▼
  [7] Package outputs → ZIP (transcript + summaries + subtitles)
        │
        ▼
  Deliver to user (download links / Telegram files)
```

---

## Project Structure

```
audioscribe/
├── main.py                   # Entry point (starts web server + bot)
├── config.py                 # All config constants, loaded from .env
├── requirements.txt
├── Procfile                  # Railway process definition
├── nixpacks.toml             # Railway build config (installs ffmpeg)
├── .env.example              # Environment variable template
├── .env                     # Your configuration (create from .env.example)
│
├── core/
│   ├── audio.py              # Stage 1+2: File validation, video extraction, chunking
│   ├── transcriber.py        # Stage 3: Groq Whisper API transcription
│   ├── summarizer.py         # Stage 4: LLM summarization (Groq/Cohere)
│   ├── output_manager.py     # Stage 5: Write files, create ZIP
│   └── pipeline.py           # Orchestrates all stages end-to-end
│
├── bot/
│   ├── main.py               # Telegram Application setup, command registration
│   └── handlers.py           # All command + file message handlers
│
├── web/
│   ├── app.py                # FastAPI server + REST API + webhook receiver
│   ├── templates/
│   │   └── index.html        # Web UI
│   └── static/
│       ├── images/
│       │   └── logo.png     # Your logo
│       ├── css/style.css    # Dark editorial design
│       └── js/app.js        # Upload, polling, download logic
```

---

## Quick Deploy to Railway

### 1. Fork / Clone

```bash
git clone https://github.com/yourusername/audioscribe.git
cd audioscribe
```

### 2. Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token

### 3. Get a Groq API Key

1. Sign up at [console.groq.com](https://console.groq.com)
2. Create an API key (free tier is sufficient)
3. For more quota, add multiple keys separated by commas

### 4. Deploy to Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select your forked repo
3. In **Variables**, add:

```
TELEGRAM_BOT_TOKEN   = your_telegram_bot_token
GROQ_API_KEYS        = your_groq_api_key_1,your_groq_api_key_2
COHERE_API_KEY       = your_cohere_api_key   # optional
MAX_FILE_SIZE_MB     = 50                  # change if needed
```

4. Railway auto-detects `nixpacks.toml` → installs ffmpeg + Python deps
5. Your app is live at `https://your-app.up.railway.app`

> **Telegram webhook is set automatically** when `RAILWAY_PUBLIC_DOMAIN` is detected.

---

## Run Locally

### Prerequisites

- Python 3.10+
- ffmpeg installed (`brew install ffmpeg` / `sudo apt install ffmpeg`)

### Setup

```bash
git clone https://github.com/yourusername/audioscribe.git
cd audioscribe

python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt

copy .env.example .env
# Edit .env and add your keys
```

### Start

```bash
python main.py
```

Web UI: `http://localhost:8000`
Telegram bot runs in polling mode automatically.

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Web interface |
| `GET /health` | GET | Health check |
| `POST /api/process` | POST | Upload file for processing |
| `GET /api/status/{job_id}` | GET | Poll job status + progress |
| `GET /api/download/{job_id}/{filename}` | GET | Download output file |
| `DELETE /api/job/{job_id}` | DELETE | Clean up job from memory |
| `POST /webhook` | POST | Telegram webhook receiver |

### POST /api/process

```
Content-Type: multipart/form-data

file         — audio/video file (required)
langs        — "en", "ar", "ar-eg", or "en,ar" (default: "en")
source_lang — source language hint: "", "en", "ar", "ar-eg" (default: auto-detect)
style        — "plain", "md", or "both" (default: "both")
summary_style — "brief" or "detailed" (default: "detailed")
summary_tone — "professional", "casual", or "technical" (default: "professional")
groq_key    — user's own Groq key (optional)
```

### GET /api/status/{job_id}

```json
{
  "job_id": "abc123",
  "status": "done",
  "progress": ["Chunking...", "Transcribing...", "Done!"],
  "detected_lang": "en",
  "lang_name": "English",
  "transcript_preview": "First 500 chars...",
  "files": ["output_transcript.txt", "output_transcript.srt", "output_transcript.vtt", "output_summary_en.txt", "output_audioscribe.zip"]
}
```

---

## Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | Full help |
| `/info` | Project & developer info |
| `/lang auto` | Auto-detect language (default) |
| `/lang en` | English output only |
| `/lang ar` | Arabic (MSA) output only |
| `/lang ar-eg` | Egyptian Arabic output |
| `/lang both` | English + Arabic |
| `/style plain` | Plain text output |
| `/style md` | Structured Markdown output |
| `/style both` | Both formats (default) |
| `/summary_style brief` | Brief summary |
| `/summary_style detailed` | Detailed summary (default) |
| `/summary_tone professional` | Professional tone |
| `/summary_tone casual` | Casual/friendly tone |
| `/summary_tone technical` | Technical tone |
| `/key gsk_...` | Set your own Groq API key |
| `/key clear` | Clear your API key |
| `/cancel` | Cancel current processing |

---

## Supported Formats

**Audio:** `.mp3` `.wav` `.m4a` `.ogg` `.flac` `.aac` `.wma` `.opus` `.webm`

**Video:** `.mp4` `.mkv` `.avi` `.mov` `.wmv` `.webm` `.flv` `.m4v`

**Limits:** Configurable via .env (default: 50 MB max file size · 60 min max duration)

---

## Configuration (.env)

```bash
# ═══ Required ═══
TELEGRAM_BOT_TOKEN=your_bot_token
GROQ_API_KEYS=key1,key2,key3  # Multiple keys (comma-separated)

# ═══ Optional ═══
COHERE_API_KEY=fallback_key

# ═══ Audio Limits ═══
MAX_FILE_SIZE_MB=50           # Max upload size (MB)
MAX_AUDIO_DURATION_MINUTES=60  # Max audio length (minutes)
MAX_UPLOAD_SIZE_MB=50          # Max API upload size

# ═══ Processing ═══
CHUNK_DURATION_MS=25000       # 25 seconds per chunk
CHUNK_OVERLAP_MS=3000         # 3 seconds overlap

# ═══ Summarization ═══
SUMMARY_CHUNK_WORDS=3500
SUMMARY_TEMPERATURE=0.7
SUMMARY_STYLE=detailed        # "brief" or "detailed"
SUMMARY_TONE=professional     # "professional", "casual", "technical"

# ═══ Directories ═══
OUTPUT_TEMP_DIR=/tmp/audioscribe

# ═══ Railway (auto-set) ═══
# PORT=8000
# RAILWAY_PUBLIC_DOMAIN=your-app.up.railway.app
```

---

## Tech Stack

- **Transcription:** [Groq](https://groq.com) → Whisper Large-v3
- **Summarization:** [Groq](https://groq.com) → Llama 3.3 70B · [Cohere](https://cohere.com) → Command R+
- **Audio processing:** ffmpeg (direct subprocess)
- **Web framework:** [FastAPI](https://fastapi.tiangolo.com) + [uvicorn](https://www.uvicorn.org)
- **Telegram:** [python-telegram-bot](https://python-telegram-bot.org)
- **Deployment:** [Railway](https://railway.app)

---

## Author

**Mohamed Abdalkader** — AI Engineer & Developer  
[LinkedIn](https://www.linkedin.com/in/mo-abdalkader/)

---

*No audio data is retained after processing. All temporary files are deleted immediately after results are delivered.*