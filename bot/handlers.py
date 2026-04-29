"""
bot/handlers.py — All Telegram command + message handlers.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from telegram import Update, Document, Audio, Video, Voice, VideoNote, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

import config

logger = logging.getLogger(__name__)

# ── Simple in-memory user prefs & processing state ─────────────────────────
_processing: dict[int, bool] = {}
_user_prefs: dict[int, dict] = {}   # user_id → {lang, style, summary_style, summary_tone, groq_key}


def _prefs(user_id: int) -> dict:
    if user_id not in _user_prefs:
        _user_prefs[user_id] = {
            "lang": "auto",
            "style": "both",
            "summary_style": "detailed",
            "summary_tone": "professional",
            "groq_key": None,
        }
    return _user_prefs[user_id]


# ── Message helpers ─────────────────────────────────────────────────────────

async def _reply(update: Update, text: str, **kwargs):
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **kwargs)


async def _edit(message: Message, text: str):
    try:
        await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


# ── Commands ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, (
        "🎙 *Welcome to AudioScribe!*\n\n"
        "Send me any audio or video file and I'll:\n"
        "• 📝 Transcribe it using Whisper AI\n"
        "• 🧠 Summarize it with Llama 3.3\n"
        "• 📦 Send you a ZIP with transcript + summaries\n\n"
        "Supported: MP3, WAV, M4A, OGG, FLAC, MP4, MKV, MOV, and more.\n"
        f"Max size: {config.MAX_FILE_SIZE_MB} MB | Max duration: {config.MAX_AUDIO_DURATION_MINUTES} min\n\n"
        "Commands:\n"
        "/lang — Set output language\n"
        "/style — Set summary format\n"
        "/help — Full help\n"
    ))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, (
        "📖 *AudioScribe Help*\n\n"
        "*How to use:*\n"
        "Just send an audio or video file. I'll handle the rest.\n\n"
        "*Commands:*\n"
        "`/lang auto` — Auto-detect language (default)\n"
        "`/lang en` — English summary only\n"
        "`/lang ar` — Arabic summary only\n"
        "`/lang both` — English + Arabic\n\n"
        "`/style plain` — Plain text (.txt)\n"
        "`/style md` — Structured Markdown (.md)\n"
        "`/style both` — Both formats (default)\n\n"
        "`/cancel` — Cancel current processing\n\n"
        f"*Limits:* {config.MAX_FILE_SIZE_MB} MB · {config.MAX_AUDIO_DURATION_MINUTES} min\n\n"
        "*Supported formats:*\n"
        "Audio: MP3, WAV, M4A, OGG, FLAC, AAC, OPUS\n"
        "Video: MP4, MKV, MOV, AVI, WEBM"
    ))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("auto", "en", "ar", "both")
    if not args or args[0].lower() not in valid:
        await _reply(update,
            "🌐 *Set output language:*\n\n"
            "`/lang auto` — Auto-detect\n"
            "`/lang en` — English only\n"
            "`/lang ar` — Arabic only\n"
            "`/lang both` — English + Arabic"
        )
        return
    lang = args[0].lower()
    _prefs(user_id)["lang"] = lang
    labels = {"auto": "Auto-detect 🔍", "en": "English 🇬🇧", "ar": "Arabic 🇸🇦", "both": "English + Arabic 🌐"}
    await _reply(update, f"✅ Language set to *{labels[lang]}*")


async def cmd_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("plain", "md", "both")
    if not args or args[0].lower() not in valid:
        await _reply(update,
            "📄 *Set summary style:*\n\n"
            "`/style plain` — Plain text (.txt)\n"
            "`/style md` — Structured Markdown (.md)\n"
            "`/style both` — Both formats (default)"
        )
        return
    style = args[0].lower()
    _prefs(user_id)["style"] = style
    labels = {"plain": "Plain text", "md": "Structured Markdown", "both": "Both formats"}
    await _reply(update, f"✅ Summary style set to *{labels[style]}*")


async def cmd_summary_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("brief", "detailed")
    if not args or args[0].lower() not in valid:
        await _reply(update,
            "📝 *Set summary detail level:*\n\n"
            "`/summary_style brief` — Brief summary\n"
            "`/summary_style detailed` — Detailed summary (default)"
        )
        return
    style = args[0].lower()
    _prefs(user_id)["summary_style"] = style
    await _reply(update, f"✅ Summary style set to *{style}*")


async def cmd_summary_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("professional", "casual", "technical")
    if not args or args[0].lower() not in valid:
        await _reply(update,
            "🎭 *Set summary tone:*\n\n"
            "`/summary_tone professional` — Professional tone\n"
            "`/summary_tone casual` — Casual/friendly\n"
            "`/summary_tone technical` — Technical tone"
        )
        return
    tone = args[0].lower()
    _prefs(user_id)["summary_tone"] = tone
    await _reply(update, f"✅ Summary tone set to *{tone}*")


async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        current = _prefs(user_id).get("groq_key")
        if current:
            await _reply(update, f"🔑 Your Groq API key is set.\n\nTo clear it, send: `/key clear`")
        else:
            await _reply(update,
                "🔑 *Set your Groq API key:*\n\n"
                "Send your key like: `/key gsk_...`\n\n"
                "Get a free key at https://console.groq.com\n"
                "This will be used instead of the default key."
            )
        return
    if args[0].lower() == "clear":
        _prefs(user_id)["groq_key"] = None
        await _reply(update, "✅ API key cleared.")
        return
    key = args[0]
    if not key.startswith("gsk_"):
        await _reply(update, "❌ Invalid key format. Keys start with `gsk_`")
        return
    _prefs(user_id)["groq_key"] = key
    await _reply(update, "✅ API key saved! It will be used for your transcriptions.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if _processing.get(user_id):
        _processing[user_id] = False
        await _reply(update, "⛔ Cancellation requested. Finishing current step then stopping.")
    else:
        await _reply(update, "Nothing to cancel.")


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, (
        "ℹ️ *AudioScribe — Project Info*\n\n"
        "*What it does:*\n"
        "AudioScribe transcribes and summarizes audio/video files using AI. "
        "It converts speech to text with Whisper and creates smart summaries with Llama 3.3.\n\n"
        "*How it works:*\n"
        "1️⃣ Upload — Send any audio/video file\n"
        "2️⃣ Chunk — Splits into 25s segments\n"
        "3️⃣ Transcribe — Whisper Large-v3 via Groq API\n"
        "4️⃣ Summarize — Llama 3.3 generates summaries\n"
        "5️⃣ Download — Get transcript + summaries + ZIP\n\n"
        "*Tech Stack:*\n"
        "Whisper Large-v3 · Llama 3.3 70B · FastAPI · Python\n"
        "Groq API · Telegram Bot API · Railway\n\n"
        "_ _ _\n\n"
        "👨‍💻 *Developer:*\n"
        "*Mohamed Abdalkader*\n"
        "AI Engineer & Developer\n"
        "[LinkedIn](https://www.linkedin.com/in/mo-abdalkader/)"
    ))


# ── File handler ─────────────────────────────────────────────────────────────

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message = update.effective_message

    if _processing.get(user_id):
        await _reply(update, "⚠️ I'm already processing a file for you. Use /cancel to abort.")
        return

    tg_file = (
        message.audio or message.voice or message.video
        or message.video_note or message.document
    )
    if tg_file is None:
        await _reply(update, "❓ Please send an audio or video file.")
        return

    file_name = _get_file_name(tg_file, message)
    ext = Path(file_name).suffix.lower()
    if ext not in config.ALL_SUPPORTED_EXTENSIONS:
        await _reply(update, f"❌ Unsupported format: `{ext}`\nSupported: MP3, WAV, M4A, MP4, MKV, and more.")
        return

    file_size_mb = (getattr(tg_file, "file_size", 0) or 0) / (1024 * 1024)
    if file_size_mb > config.MAX_FILE_SIZE_MB:
        await _reply(update, f"❌ File too large: {file_size_mb:.1f} MB. Max: {config.MAX_FILE_SIZE_MB} MB")
        return

    job_id = str(uuid.uuid4())[:12]
    status_msg = await message.reply_text(f"⬇️ Downloading *{file_name}*...", parse_mode=ParseMode.MARKDOWN)
    _processing[user_id] = True

    asyncio.create_task(_run_pipeline(
        update=update, context=context,
        user_id=user_id, job_id=job_id,
        tg_file=tg_file, file_name=file_name,
        status_msg=status_msg,
    ))


def _get_file_name(tg_file, message: Message) -> str:
    if isinstance(tg_file, Document):
        return tg_file.file_name or f"document.{tg_file.file_unique_id}"
    if isinstance(tg_file, Audio):
        return tg_file.file_name or f"audio_{tg_file.file_unique_id}.mp3"
    if isinstance(tg_file, Voice):
        return f"voice_{tg_file.file_unique_id}.ogg"
    if isinstance(tg_file, Video):
        return tg_file.file_name or f"video_{tg_file.file_unique_id}.mp4"
    if isinstance(tg_file, VideoNote):
        return f"videonote_{tg_file.file_unique_id}.mp4"
    return f"file_{getattr(tg_file, 'file_unique_id', 'unknown')}"


async def _run_pipeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    job_id: str,
    tg_file,
    file_name: str,
    status_msg: Message,
):
    start_time = time.monotonic()
    tmp_path: Optional[Path] = None

    try:
        # ── Download ──────────────────────────────────────────────────
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
        suffix = Path(file_name).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        tg_file_obj = await context.bot.get_file(tg_file.file_id)
        await tg_file_obj.download_to_drive(str(tmp_path))
        await _edit(status_msg, f"📁 *{file_name}* downloaded. Starting pipeline...")

        if not _processing.get(user_id):
            return

        # ── Pipeline ──────────────────────────────────────────────────
        from core.pipeline import Pipeline, PipelineError

        prefs = _prefs(user_id)
        lang_pref = prefs["lang"]
        style_pref = prefs["style"]

        # Resolve target languages
        if lang_pref == "auto":
            target_langs = None   # pipeline will transcribe and use detected lang
        elif lang_pref == "both":
            target_langs = ["en", "ar"]
        else:
            target_langs = [lang_pref]

        steps = [
            "🔪 Chunking audio...",
            "🎙 Transcribing with Whisper...",
            "🧠 Summarizing...",
            "📦 Packaging outputs...",
        ]
        step_idx = [0]

        # Capture the running event loop BEFORE entering the thread
        _loop = asyncio.get_event_loop()

        async def progress(msg: str):
            await _edit(status_msg, f"⚙️ {msg}")

        def sync_progress(msg: str):
            # Safe cross-thread progress update using captured loop
            _loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(progress(msg), loop=_loop)
            )

        await _edit(status_msg, "🎙 Transcribing with Whisper AI (no GPU — via Groq API)...")

        pipeline = Pipeline()

        summary_style = prefs.get("summary_style", "detailed")
        summary_tone = prefs.get("summary_tone", "professional")
        user_key = prefs.get("groq_key")

        # Run in thread to avoid blocking the event loop
        transcript, summary, files = await asyncio.to_thread(
            pipeline.run_in_memory,
            tmp_path,
            target_langs or ["en"],
            Path(file_name).stem[:30],
            job_id,
            sync_progress,
            groq_key=user_key,
            cohere_key=None,
            summary_style=summary_style,
            summary_tone=summary_tone,
        )

        if not _processing.get(user_id):
            return

        # ── Send results ──────────────────────────────────────────────
        elapsed = time.monotonic() - start_time
        await _edit(status_msg, f"✅ Done in {elapsed:.0f}s! Sending files...")

        chat_id = update.effective_chat.id
        include_plain = style_pref in ("plain", "both")
        include_md = style_pref in ("md", "both")

        # Send ZIP first
        zip_key = next((k for k in files if k.endswith(".zip")), None)
        if zip_key:
            import io
            bio = io.BytesIO(files[zip_key])
            bio.name = zip_key
            await context.bot.send_document(chat_id, document=bio, filename=zip_key,
                                            caption="📦 All outputs in one ZIP")

        # Send transcript
        txt_key = next((k for k in files if k.endswith("_transcript.txt")), None)
        if txt_key:
            import io
            bio = io.BytesIO(files[txt_key])
            bio.name = txt_key
            await context.bot.send_document(chat_id, document=bio, filename=txt_key,
                                            caption="📄 Full transcript")

        # Send summaries
        for fname, data in files.items():
            if "_summary_" not in fname:
                continue
            if fname.endswith(".txt") and not include_plain:
                continue
            if fname.endswith(".md") and not include_md:
                continue

            import io
            lang_code = "en"
            for lc in config.LANG_NAMES:
                if f"_{lc}." in fname:
                    lang_code = lc
                    break
            lang_name = config.LANG_NAMES.get(lang_code, lang_code.upper())
            fmt = "Markdown" if fname.endswith(".md") else "plain"
            bio = io.BytesIO(data)
            bio.name = fname
            await context.bot.send_document(chat_id, document=bio, filename=fname,
                                            caption=f"📝 {lang_name} summary ({fmt})")

        await context.bot.send_message(
            chat_id,
            f"✅ *All done!*\n\n"
            f"🌐 Detected language: *{transcript.language_name}*\n"
            f"📊 Confidence: *{transcript.lang_probability:.0%}*\n"
            f"⏱ Processing time: *{elapsed:.0f}s*",
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:
        logger.error("Pipeline error for user %s: %s", user_id, traceback.format_exc())
        await _edit(status_msg, f"❌ Error: {str(e)}\n\nPlease try again or use /help.")
    finally:
        _processing[user_id] = False
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass