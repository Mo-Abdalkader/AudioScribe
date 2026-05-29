"""
bot/handlers.py — All Telegram command + message handlers.
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

import httpx

from telegram import Update, Document, Audio, Video, Voice, VideoNote, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

import config

logger = logging.getLogger(__name__)

# ── Simple in-memory user prefs & processing state ─────────────────────────
_user_prefs: dict[int, dict] = {}   # user_id → {lang, style, summary_style, summary_tone, groq_key}
_user_usage: dict[int, dict] = {}   # user_id → {daily_used: int, last_reset: date}
_processing: dict[int, bool] = {}  # user_id → is_processing

def _prefs(user_id: int) -> dict:
    if user_id not in _user_prefs:
        _user_prefs[user_id] = {
            "lang": "auto",
            "style": "md",
            "summary_style": "detailed",
            "summary_tone": "professional",
            "groq_key": None,
            "fast_mode": False,
            "mode": "full",          # full / transcript / subtitles / summary
            "subtitle_lang": "none", # none / en / ar / ar-eg
        }
    return _user_prefs[user_id]


def _has_own_key(user_id: int) -> bool:
    """Check if user provided their own API key."""
    return bool(_prefs(user_id).get("groq_key"))


def _get_user_usage(user_id: int) -> dict:
    from datetime import date
    today = date.today()
    if user_id not in _user_usage or _user_usage[user_id].get("last_reset") != today:
        _user_usage[user_id] = {"daily_used": 0, "last_reset": today}
    return _user_usage[user_id]


def _increment_usage(user_id: int):
    from datetime import date
    today = date.today()
    usage = _get_user_usage(user_id)
    usage["daily_used"] += 1


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
        f"📊 Free limits: {config.FREE_MAX_FILE_SIZE_MB} MB file · {config.FREE_MAX_URL_SIZE_MB} MB URL · {config.FREE_DAILY_LIMIT} files/day\n"
        "💎 Premium: Use /key to add your own API key for higher limits\n\n"
        "Commands:\n"
        "/lang — Set output language\n"
        "/key — Add your own Groq API key\n"
        "/help — Full help\n"
    ))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limits = config.get_user_limits(_has_own_key(update.effective_user.id))
    await _reply(update, (
        "📖 *AudioScribe Help*\n\n"
        "*How to use:*\n"
        "Send an audio/video file, or a link — I'll transcribe and summarize it.\n\n"
        "⚙️ Use /settings to change all options with buttons (no typing needed)\n\n"
        "*Commands:*\n"
        "`/settings` — Open settings panel with inline buttons\n"
        "`/lang` — Set output language (auto/en/ar/ar-eg/both)\n"
        "`/style` — Set output format (plain/md/both)\n"
        "`/summary_style` — Brief or detailed\n"
        "`/summary_tone` — Professional, casual, or technical\n"
        "`/key YOUR_GROQ_KEY` — Add your own API key for unlimited processing\n"
        "`/cancel` — Cancel current processing\n\n"
        f"*Your limits:*\n"
        f"📎 File: {limits['max_file_mb']} MB | URL: {limits['max_url_mb']} MB | Duration: {limits['max_duration_min']} min\n"
        f"📊 Daily: {config.FREE_DAILY_LIMIT - _get_user_usage(update.effective_user.id)['daily_used']} files remaining\n\n"
        "*Supported formats:*\n"
        "Audio: MP3, WAV, M4A, OGG, FLAC, AAC, OPUS\n"
        "Video: MP4, MKV, AVI, MOV, WMV\n\n"
        "*URL sources:*\n"
        "🎬 YouTube · 📁 Google Drive · 📦 Dropbox · 🔗 Direct media links"
    ))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("auto", "en", "ar", "ar-eg", "both")
    if args and args[0].lower() in valid:
        # Direct command still works: /lang en
        lang = args[0].lower()
        _prefs(user_id)["lang"] = lang
        labels = {
            "auto": "Auto-detect 🔍", "en": "English 🇬🇧",
            "ar": "Arabic 🇸🇦", "ar-eg": "Egyptian Arabic 🇪🇬", "both": "English + Arabic 🌐"
        }
        await _reply(update, f"✅ Language set to *{labels[lang]}*")
        return

    # Show inline keyboard
    current = _prefs(user_id).get("lang", "auto")
    keyboard = [
        [
            InlineKeyboardButton("🔍 Auto" + (" ✓" if current == "auto" else ""), callback_data="lang:auto"),
            InlineKeyboardButton("🇬🇧 English" + (" ✓" if current == "en" else ""), callback_data="lang:en"),
        ],
        [
            InlineKeyboardButton("🇸🇦 Arabic" + (" ✓" if current == "ar" else ""), callback_data="lang:ar"),
            InlineKeyboardButton("🇪🇬 Egyptian" + (" ✓" if current == "ar-eg" else ""), callback_data="lang:ar-eg"),
        ],
        [
            InlineKeyboardButton("🌐 EN + AR" + (" ✓" if current == "both" else ""), callback_data="lang:both"),
        ],
    ]
    await update.effective_message.reply_text(
        "🌐 *Select output language:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("plain", "md", "both")
    if args and args[0].lower() in valid:
        style = args[0].lower()
        _prefs(user_id)["style"] = style
        labels = {"plain": "Plain text", "md": "Structured Markdown", "both": "Both formats"}
        await _reply(update, f"✅ Summary style set to *{labels[style]}*")
        return

    current = _prefs(user_id).get("style", "md")
    keyboard = [[
        InlineKeyboardButton("📄 Plain" + (" ✓" if current == "plain" else ""), callback_data="style:plain"),
        InlineKeyboardButton("📋 Markdown" + (" ✓" if current == "md" else ""), callback_data="style:md"),
        InlineKeyboardButton("📦 Both" + (" ✓" if current == "both" else ""), callback_data="style:both"),
    ]]
    await update.effective_message.reply_text(
        "📄 *Select summary format:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_summary_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("brief", "detailed")
    if args and args[0].lower() in valid:
        style = args[0].lower()
        _prefs(user_id)["summary_style"] = style
        await _reply(update, f"✅ Summary detail set to *{style}*")
        return

    current = _prefs(user_id).get("summary_style", "detailed")
    keyboard = [[
        InlineKeyboardButton("⚡ Brief" + (" ✓" if current == "brief" else ""), callback_data="summary_style:brief"),
        InlineKeyboardButton("📝 Detailed" + (" ✓" if current == "detailed" else ""), callback_data="summary_style:detailed"),
    ]]
    await update.effective_message.reply_text(
        "📝 *Select summary detail level:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_summary_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    valid = ("professional", "casual", "technical")
    if args and args[0].lower() in valid:
        tone = args[0].lower()
        _prefs(user_id)["summary_tone"] = tone
        await _reply(update, f"✅ Summary tone set to *{tone}*")
        return

    current = _prefs(user_id).get("summary_tone", "professional")
    keyboard = [[
        InlineKeyboardButton("💼 Pro" + (" ✓" if current == "professional" else ""), callback_data="summary_tone:professional"),
        InlineKeyboardButton("😊 Casual" + (" ✓" if current == "casual" else ""), callback_data="summary_tone:casual"),
        InlineKeyboardButton("🔬 Technical" + (" ✓" if current == "technical" else ""), callback_data="summary_tone:technical"),
    ]]
    await update.effective_message.reply_text(
        "🎭 *Select summary tone:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set processing mode with inline keyboard."""
    user_id = update.effective_user.id
    current = _prefs(user_id).get("mode", "full")
    mode_labels = {
        "full":       ("⚡", "Full"),
        "transcript": ("📄", "Transcript+Subtitles"),
        "subtitles":  ("🎞", "Subtitles Only"),
        "summary":    ("🧠", "Summary Only"),
    }
    keyboard = [
        [
            InlineKeyboardButton(
                f"{icon} {label}" + (" ✓" if current == key else ""),
                callback_data=f"mode:{key}"
            )
            for key, (icon, label) in mode_labels.items()
        ]
    ]
    await update.effective_message.reply_text(
        "⚙️ *Select processing mode:*\n\n"
        "⚡ *Full* — Transcript + Summary + Subtitles\n"
        "📄 *Transcript+Subtitles* — No AI summary (saves quota)\n"
        "🎞 *Subtitles Only* — SRT/VTT files only\n"
        "🧠 *Summary Only* — AI summary, no transcript file",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_subtitle_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set translated subtitle language with inline keyboard."""
    user_id = update.effective_user.id
    current = _prefs(user_id).get("subtitle_lang", "none")
    keyboard = [[
        InlineKeyboardButton("🔤 Original only" + (" ✓" if current == "none" else ""),  callback_data="subtitle_lang:none"),
        InlineKeyboardButton("🇬🇧 + English"     + (" ✓" if current == "en" else ""),    callback_data="subtitle_lang:en"),
    ], [
        InlineKeyboardButton("🇸🇦 + Arabic"      + (" ✓" if current == "ar" else ""),    callback_data="subtitle_lang:ar"),
        InlineKeyboardButton("🇪🇬 + Egyptian"    + (" ✓" if current == "ar-eg" else ""), callback_data="subtitle_lang:ar-eg"),
    ]]
    await update.effective_message.reply_text(
        "🎞 *Translated Subtitles:*\n\nGenerate an additional SRT/VTT in a different language alongside the original.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all current settings with inline buttons to change each one."""
    user_id = update.effective_user.id
    p = _prefs(user_id)
    lang_labels  = {"auto": "Auto 🔍", "en": "English 🇬🇧", "ar": "Arabic 🇸🇦", "ar-eg": "Egyptian 🇪🇬", "both": "Both 🌐"}
    style_labels = {"plain": "Plain 📄", "md": "Markdown 📋", "both": "Both 📦"}
    mode_labels  = {"full": "Full ⚡", "transcript": "Transcript 📄", "subtitles": "Subtitles 🎞", "summary": "Summary 🧠"}
    sub_labels   = {"none": "Original only 🔤", "en": "+English 🇬🇧", "ar": "+Arabic 🇸🇦", "ar-eg": "+Egyptian 🇪🇬"}
    has_key      = "✅ Set" if p.get("groq_key") else "❌ Not set"
    fast_status  = "⚡ On" if p.get("fast_mode") else "🐢 Off"

    keyboard = [
        [InlineKeyboardButton(f"⚡ Mode: {mode_labels.get(p['mode'], p['mode'])}",        callback_data="open:mode")],
        [InlineKeyboardButton(f"🌐 Language: {lang_labels.get(p['lang'], p['lang'])}",     callback_data="open:lang")],
        [InlineKeyboardButton(f"🎞 Subtitles: {sub_labels.get(p['subtitle_lang'], '🔤')}", callback_data="open:subtitle_lang")],
        [InlineKeyboardButton(f"📄 Format: {style_labels.get(p['style'], p['style'])}",    callback_data="open:style")],
        [InlineKeyboardButton(f"📝 Detail: {p['summary_style'].title()}",                  callback_data="open:summary_style")],
        [InlineKeyboardButton(f"🎭 Tone: {p['summary_tone'].title()}",                     callback_data="open:summary_tone")],
        [InlineKeyboardButton(f"⚡ Fast Mode: {fast_status}",                              callback_data="open:fast_mode")],
        [InlineKeyboardButton(f"🔑 API Key: {has_key}",                                    callback_data="open:key")],
    ]
    await update.effective_message.reply_text(
        "⚙️ *Your current settings* — tap to change:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all inline keyboard callbacks.
    Callback data format: 'setting:value' or 'open:menu'.
    """
    query = update.callback_query
    await query.answer()   # acknowledge immediately to stop the loading spinner

    user_id = query.from_user.id
    data = query.data or ""

    if not data or ":" not in data:
        return

    key, value = data.split(":", 1)

    # ── open:* — show the inline menu for that setting ───────────────────────
    if key == "open":
        fake_update = update
        if value == "lang":
            await cmd_lang(fake_update, context)
        elif value == "style":
            await cmd_style(fake_update, context)
        elif value == "summary_style":
            await cmd_summary_style(fake_update, context)
        elif value == "summary_tone":
            await cmd_summary_tone(fake_update, context)
        elif value == "mode":
            await cmd_mode(fake_update, context)
        elif value == "subtitle_lang":
            await cmd_subtitle_lang(fake_update, context)
        elif value == "settings":
            await cmd_settings(fake_update, context)
        elif value == "key":
            await query.message.reply_text(
                "🔑 Send your Groq key with: `/key gsk_...`\n"
                "Or clear it with: `/key clear`",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif value == "fast_mode":
            if not _prefs(user_id).get("groq_key"):
                await query.message.reply_text(
                    "🔑 Set your own API key first with `/key` to use fast mode.\n\n"
                    "Fast Mode reduces delays between API calls for quicker processing, "
                    "but requires your own key since the shared key has tight rate limits.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            current = _prefs(user_id).get("fast_mode", False)
            _prefs(user_id)["fast_mode"] = not current
            status = "enabled ⚡" if not current else "disabled 🐢"
            await query.message.reply_text(
                f"⚡ Fast Mode *{status}*.\n\n"
                + ("Delays between API calls are reduced. Processing will be faster.\n"
                   "⚠️ If you hit rate limit errors, disable this."
                   if not current
                   else "Delays restored. Safer for limited API quotas."),
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ── action:* — quick actions from the post-processing keyboard ───────────
    if key == "action":
        if value == "send_file":
            await query.message.reply_text(
                "📎 Send me your next audio or video file, or paste a link!\n\n"
                "Supported: file upload · YouTube · Google Drive · Dropbox · direct URL"
            )
        return

    # ── setting:value — apply the preference ────────────────────────────────
    setting_map = {
        "lang":          ("lang",          {"auto": "Auto-detect 🔍", "en": "English 🇬🇧", "ar": "Arabic 🇸🇦", "ar-eg": "Egyptian Arabic 🇪🇬", "both": "Both 🌐"}),
        "style":         ("style",         {"plain": "Plain text 📄", "md": "Markdown 📋", "both": "Both formats 📦"}),
        "summary_style": ("summary_style", {"brief": "Brief ⚡", "detailed": "Detailed 📝"}),
        "summary_tone":  ("summary_tone",  {"professional": "Professional 💼", "casual": "Casual 😊", "technical": "Technical 🔬"}),
        "mode":          ("mode",          {"full": "Full ⚡", "transcript": "Transcript+Subtitles 📄", "subtitles": "Subtitles Only 🎞", "summary": "Summary Only 🧠"}),
        "subtitle_lang": ("subtitle_lang", {"none": "Original only 🔤", "en": "+English 🇬🇧", "ar": "+Arabic 🇸🇦", "ar-eg": "+Egyptian 🇪🇬"}),
    }

    if key in setting_map:
        pref_key, labels = setting_map[key]
        _prefs(user_id)[pref_key] = value
        label = labels.get(value, value.title())
        try:
            await query.edit_message_text(
                f"✅ *{pref_key.replace('_', ' ').title()}* set to *{label}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass   # message unchanged — that's fine


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
    await _reply(
        update,
        "✅ API key saved! It will be used for your transcriptions.\n\n"
        "⚡ *Fast Mode available* — reduces delays for quicker processing.\n"
        "Send `/fastmode` to enable it.\n\n"
        "⚠️ Free keys have TPM/RPM limits. If you hit errors, disable with `/fastmode`."
    )


async def cmd_fastmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _prefs(user_id).get("groq_key"):
        await _reply(update, "❌ Set your own API key first with `/key` to use fast mode.")
        return
    current = _prefs(user_id).get("fast_mode", False)
    _prefs(user_id)["fast_mode"] = not current
    status = "enabled ⚡" if not current else "disabled 🐢"
    await _reply(
        update,
        f"⚡ Fast Mode *{status}*.\n\n"
        + ("Delays between API calls are reduced. Processing will be faster.\n"
           "⚠️ If you hit rate limit errors, disable this."
           if not current
           else "Delays restored. Safer for limited API quotas.")
    )


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
        "Groq API · Telegram Bot API\n\n"
        "_ _ _\n\n"
        "👨‍💻 *Developer:*\n"
        "*Mohamed Abdalkader*\n"
        "AI Engineer & Developer\n"
        "[LinkedIn](https://www.linkedin.com/in/mo-abdalkader/)"
    ))


# ── URL detection & download ────────────────────────────────────────────────

# Direct link ending with a known audio/video extension
_URL_PATTERN = re.compile(
    r'https?://[^\s<>"]+\.(mp3|wav|m4a|ogg|flac|aac|opus|webm|mp4|mkv|avi|mov|wmv|flv|m4v)',
    re.IGNORECASE
)

# Google Drive: supports /file/d/<id>, /file/d/<id>/view, and ?id= forms
_GDRIVE_PATTERN = re.compile(
    r'https?://(?:drive\.google\.com/(?:file/d/|uc\?(?:export=\w+&)?id=)|docs\.google\.com/uc\?(?:export=\w+&)?id=)'
    r'([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)

# Dropbox: convert ?dl=0 → ?dl=1 for direct download
_DROPBOX_PATTERN = re.compile(
    r'https?://(?:www\.)?dropbox\.com/s/[^\s<>"]+',
    re.IGNORECASE
)

# YouTube: standard watch URLs, short youtu.be links, and /shorts/
_YOUTUBE_PATTERN = re.compile(
    r'https?://(?:(?:www\.)?youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})',
    re.IGNORECASE
)


def _extract_url(text: str) -> tuple[Optional[str], str]:
    """
    Detect and normalize URLs from user message text.
    Returns (normalized_url, url_type) where url_type is one of:
    'direct', 'gdrive', 'dropbox', 'youtube', or '' if nothing found.
    """
    # 1. YouTube (check before direct, since youtube.com URLs don't have media extensions)
    match = _YOUTUBE_PATTERN.search(text)
    if match:
        video_id = match.group(1)
        # Return canonical watch URL — yt-dlp will handle the actual download
        return f"https://www.youtube.com/watch?v={video_id}", "youtube"

    # 2. Direct media link (has audio/video extension)
    match = _URL_PATTERN.search(text)
    if match:
        return match.group(0), "direct"

    # 3. Google Drive
    match = _GDRIVE_PATTERN.search(text)
    if match:
        file_id = match.group(1)
        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        return direct_url, "gdrive"

    # 4. Dropbox — swap dl=0 for dl=1 to get direct download
    match = _DROPBOX_PATTERN.search(text)
    if match:
        url = match.group(0)
        url = re.sub(r'[?&]dl=\d', '', url)
        url = url + ('&dl=1' if '?' in url else '?dl=1')
        return url, "dropbox"

    return None, ""

async def _download_youtube(url: str, status_msg: Message, max_size_mb: int) -> tuple[Optional[Path], Optional[str]]:
    """
    Download audio from a YouTube URL using yt-dlp.
    Extracts audio only (best quality), saves as .m4a or .webm.
    Returns (tmp_path, error_message).
    """
    import subprocess
    import shutil

    if not shutil.which("yt-dlp"):
        return None, "❌ yt-dlp is not installed on this server. Ask the admin to run: pip install yt-dlp"

    await _edit(status_msg, "🎬 Fetching YouTube video info...")

    try:
        # First: get video info without downloading to check duration/size
        info_result = subprocess.run(
            ["yt-dlp", "--no-playlist", "--print", "%(title)s|%(duration)s|%(filesize_approx)s", url],
            capture_output=True, text=True, timeout=30
        )
        if info_result.returncode == 0 and "|" in info_result.stdout:
            parts = info_result.stdout.strip().split("|")
            title = parts[0][:50] if parts[0] else "YouTube video"
            duration_s = int(parts[1]) if parts[1].isdigit() else 0
            approx_mb = int(parts[2]) // (1024 * 1024) if parts[2].isdigit() else 0

            if duration_s > 0:
                duration_min = duration_s / 60
                if duration_min > config.MAX_AUDIO_DURATION_MINUTES:
                    return None, (
                        f"❌ Video too long: {duration_min:.0f} min "
                        f"(max {config.MAX_AUDIO_DURATION_MINUTES} min)."
                    )
            await _edit(status_msg, f"🎬 Downloading: *{title}*...")
        else:
            await _edit(status_msg, "🎬 Downloading YouTube audio...")

        # Download audio-only to a temp file
        tmp_dir = tempfile.mkdtemp()
        output_template = f"{tmp_dir}/audio.%(ext)s"

        dl_result = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "--age-limit", "99",
                "--geo-bypass",
                "--extractor-args", "youtube:player_client=android,web",
                "--extractor-retries", "3",
                "--retries", "3",
                "-f", "bestaudio/best",          # audio-only, best quality
                "--extract-audio",
                "--audio-format", "m4a",
                "--audio-quality", "0",           # best quality
                "--max-filesize", f"{max_size_mb}M",
                "-o", output_template,
                url,
            ],
            capture_output=True, text=True, timeout=300
        )

        if dl_result.returncode != 0:
            return None, f"❌ yt-dlp failed: {dl_result.stderr[:400]}"

        # Find the downloaded file (extension may vary)
        import glob
        matches = glob.glob(f"{tmp_dir}/audio.*")
        if not matches:
            return None, "❌ YouTube download produced no output file."

        tmp_path = Path(matches[0])

        # Final size check
        size_mb = tmp_path.stat().st_size / (1024 * 1024)
        if size_mb > max_size_mb:
            tmp_path.unlink(missing_ok=True)
            return None, f"❌ Downloaded audio is {size_mb:.0f} MB (max {max_size_mb} MB)."

        return tmp_path, None

    except subprocess.TimeoutExpired:
        return None, "❌ YouTube download timed out. Try a shorter video."
    except Exception as e:
        return None, f"❌ YouTube download error: {e}"


async def _download_from_url(
    url: str,
    status_msg: Message,
    url_type: str = "",
    max_size_mb: int = None
) -> tuple[Optional[Path], Optional[str]]:
    """
    Download a file from a URL to a temp path.
    Handles Google Drive (with private-file detection), Dropbox, YouTube, and direct links.
    Returns (tmp_path, error_message). If error_message is not None, download failed.
    """
    is_gdrive = url_type == "gdrive"
    is_dropbox = url_type == "dropbox"

    # YouTube is handled by a dedicated yt-dlp function
    if url_type == "youtube":
        return await _download_youtube(url, status_msg, max_size_mb or config.FREE_MAX_URL_SIZE_MB)

    try:
        max_size_mb = max_size_mb or config.FREE_MAX_URL_SIZE_MB
        max_size_bytes = max_size_mb * 1024 * 1024

        service_name = "Google Drive" if is_gdrive else ("Dropbox" if is_dropbox else "URL")
        await _edit(status_msg, f"⬇️ Downloading from {service_name}...")

        # Stream the download so we can check size without loading everything into RAM
        with httpx.Client(timeout=httpx.Timeout(600.0), follow_redirects=True) as client:
            with client.stream("GET", url) as resp:

                if resp.status_code != 200:
                    return None, f"Download failed: HTTP {resp.status_code}"

                # ── Google Drive private-file detection ──────────────────
                # GDrive returns 200 with an HTML page for private/invalid files.
                # We detect this by checking Content-Type before reading the body.
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

                if is_gdrive and content_type in ("text/html", "application/xhtml+xml"):
                    return None, (
                        "❌ Google Drive: file is private or link is invalid.\n"
                        "➡️ Open Drive → right-click file → Share → "
                        "change to *Anyone with the link* → copy link."
                    )

                # ── Resolve file extension from Content-Type ─────────────
                _ct_to_ext = {
                    "audio/mpeg": ".mp3",
                    "audio/mp4": ".m4a",
                    "audio/ogg": ".ogg",
                    "audio/wav": ".wav",
                    "audio/x-wav": ".wav",
                    "audio/flac": ".flac",
                    "audio/aac": ".aac",
                    "audio/webm": ".webm",
                    "video/mp4": ".mp4",
                    "video/x-matroska": ".mkv",
                    "video/webm": ".webm",
                    "video/quicktime": ".mov",
                    "video/x-msvideo": ".avi",
                    "application/octet-stream": ".mp4",  # GDrive default for binary
                }

                if is_gdrive or is_dropbox:
                    suffix = _ct_to_ext.get(content_type, ".mp4")
                else:
                    # For direct links, prefer the extension from the URL
                    url_suffix = Path(url.split("?")[0]).suffix.lower()
                    suffix = url_suffix if url_suffix in config.ALL_SUPPORTED_EXTENSIONS else \
                             _ct_to_ext.get(content_type, ".tmp")

                # ── Stream to temp file with size check ─────────────────
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp_path = Path(tmp.name)
                downloaded = 0

                try:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):  # 256 KB chunks
                        downloaded += len(chunk)
                        if downloaded > max_size_bytes:
                            tmp.close()
                            tmp_path.unlink(missing_ok=True)
                            return None, (
                                f"❌ File too large: >{max_size_mb} MB.\n"
                                f"Use /key to add your own Groq key for higher limits."
                            )
                        tmp.write(chunk)
                finally:
                    tmp.close()

        size_mb = downloaded / (1024 * 1024)
        await _edit(status_msg, f"⬇️ Downloaded {size_mb:.1f} MB. Starting pipeline...")
        return tmp_path, None

    except httpx.TimeoutException:
        return None, "❌ Download timed out. Try a smaller file or a faster source."
    except httpx.ConnectError:
        return None, "❌ Connection failed. Check the link and try again."
    except Exception as e:
        return None, f"❌ Download error: {e}"

# ── File handler ─────────────────────────────────────────────────────────────

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message = update.effective_message

    if _processing.get(user_id):
        await _reply(update, "⚠️ I'm already processing a file. Use /cancel first.")
        return

    limits = config.get_user_limits(_has_own_key(user_id))
    daily_usage = _get_user_usage(user_id)

    if not _has_own_key(user_id) and daily_usage["daily_used"] >= config.FREE_DAILY_LIMIT:
        await _reply(update, f"❌ Daily limit reached ({config.FREE_DAILY_LIMIT}). Use /key for unlimited.")
        return

    tg_file = (
        message.audio or message.voice or message.video
        or message.video_note or message.document
    )
    if tg_file is None:
        await _reply(update, "❓ Please send an audio/video file.")
        return

    file_name = _get_file_name(tg_file, message)
    ext = Path(file_name).suffix.lower()
    if ext not in config.ALL_SUPPORTED_EXTENSIONS:
        await _reply(update, f"❌ Unsupported format: `{ext}`\nSupported: MP3, WAV, M4A, MP4, MKV, and more.")
        return

    file_size_mb = (getattr(tg_file, "file_size", 0) or 0) / (1024 * 1024)
    if file_size_mb > limits["max_file_mb"]:
        await _reply(update, f"❌ File too large: {file_size_mb:.1f} MB. Max: {limits['max_file_mb']} MB\n💡 Use /key to add your own key for higher limits.")
        return

    _increment_usage(user_id)
    _processing[user_id] = True

    job_id = str(uuid.uuid4())[:12]
    status_msg = await message.reply_text(
        f"🔄 Processing started...\n📊 {daily_usage['daily_used']}/{config.FREE_DAILY_LIMIT} daily",
        parse_mode=ParseMode.MARKDOWN)

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


async def _handle_url_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, url_type: str = ""):
    user_id = update.effective_user.id

    # Basic SSRF protection for Telegram path too
    from urllib.parse import urlparse
    parsed_host = urlparse(url).hostname or ""
    _blocked = {"localhost", "0.0.0.0", "169.254.169.254", "metadata.google.internal"}
    if parsed_host.lower() in _blocked or parsed_host.startswith("192.168.") or parsed_host.startswith("10."):
        await _reply(update, "❌ That URL is not allowed.")
        return

    # For cloud storage / YouTube we don't know the extension yet — resolved during download
    if url_type in ("gdrive", "dropbox", "youtube"):
        service = {"gdrive": "gdrive", "dropbox": "dropbox", "youtube": "youtube"}[url_type]
        file_name = f"{service}_file_{user_id}.m4a"   # placeholder; real ext from download
    else:
        file_name = Path(url).name.split("?")[0] or f"file_{user_id}"

    if _processing.get(user_id):
        await _reply(update, "⚠️ I'm already processing a file for you. Use /cancel to abort.")
        return

    limits = config.get_user_limits(_has_own_key(user_id))
    daily_usage = _get_user_usage(user_id)

    if not _has_own_key(user_id) and daily_usage["daily_used"] >= config.FREE_DAILY_LIMIT:
        await _reply(update, f"❌ Daily limit reached ({config.FREE_DAILY_LIMIT} files/day).\n💡 Use /key to add your own key.")
        return

    ext = Path(file_name).suffix.lower()
    if ext not in config.ALL_SUPPORTED_EXTENSIONS:
        await _reply(update, f"❌ Unsupported format: `{ext}`\nSupported: MP3, WAV, M4A, MP4, MKV, and more.")
        return

    _increment_usage(user_id)
    _processing[user_id] = True

    job_id = str(uuid.uuid4())[:12]
    status_msg = await update.effective_message.reply_text(
        f"🔄 Processing started...\n📊 {daily_usage['daily_used']}/{config.FREE_DAILY_LIMIT} daily",
        parse_mode=ParseMode.MARKDOWN)

    asyncio.create_task(_run_pipeline_from_url(
        update=update, context=context,
        user_id=user_id, job_id=job_id,
        url=url, file_name=file_name,
        status_msg=status_msg,
        url_type=url_type,          # FIX: was missing, causing gdrive/dropbox detection to always be ""
    ))


async def _run_pipeline_from_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    job_id: str,
    url: str,
    file_name: str,
    status_msg: Message,
    url_type: str = "",
):
    limits = config.get_user_limits(_has_own_key(user_id))
    start_time = time.monotonic()
    tmp_path = None
    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
        tmp_path, err = await _download_from_url(url, status_msg, url_type, limits["max_url_mb"])
        if err:
            await _edit(status_msg, f"❌ Download failed: {err}")
            return

        if not _processing.get(user_id):
            return

        await _edit(status_msg, f"📁 *{file_name}* downloaded. Starting pipeline...")

        from core.pipeline import Pipeline, PipelineError
        prefs = _prefs(user_id)
        lang_pref = prefs["lang"]
        style_pref = prefs["style"]

        if lang_pref == "auto":
            target_langs = None
        elif lang_pref == "both":
            target_langs = ["en", "ar"]
        else:
            target_langs = [lang_pref]

        steps = ["🔪 Chunking audio...", "🎙 Transcribing with Whisper...", "🧠 Summarizing...", "📦 Packaging outputs..."]
        step_idx = [0]
        _loop = asyncio.get_event_loop()

        async def progress(msg: str):
            step_idx[0] += 1
            await _edit(status_msg, f"{steps[step_idx[0]] if step_idx[0] < len(steps) else '⏳'} {msg}")

        def sync_progress(msg: str):
            _loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(progress(msg), loop=_loop)
            )

        await _edit(status_msg, "🎙 Transcribing with Whisper AI...")

        user_key = prefs.get("groq_key")
        fast_mode = bool(user_key and prefs.get("fast_mode"))
        pipeline = Pipeline(fast_mode=fast_mode)
        summary_style = prefs.get("summary_style", "detailed")
        summary_tone  = prefs.get("summary_tone", "professional")
        mode          = prefs.get("mode", "full")
        subtitle_lang = prefs.get("subtitle_lang", "none")
        subtitle_langs = [] if subtitle_lang == "none" else [subtitle_lang]

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
            max_size_mb=limits["max_url_mb"],
            max_duration_min=limits["max_duration_min"],
            mode=mode,
            subtitle_langs=subtitle_langs,
        )

        if not _processing.get(user_id):
            return

        elapsed = time.monotonic() - start_time
        await _send_results_from_url(update, file_name, transcript, summary, files, elapsed, status_msg, context, style_pref)

    except PipelineError as e:
        err_str = str(e)
        if "RATE_LIMIT:" in err_str:
            # Parse wait minutes from structured error e.g. "RATE_LIMIT:22"
            try:
                wait_min = int(err_str.split("RATE_LIMIT:")[1].strip())
            except Exception:
                wait_min = 30
            has_key = bool(_prefs(user_id).get("groq_key"))
            if has_key:
                msg = (
                    f"⏳ *Summarization rate limit hit.*\n\n"
                    f"Your Groq key also reached its daily limit.\n"
                    f"Please wait ~{wait_min} minutes and try again."
                )
            else:
                msg = (
                    f"⏳ *Summarization rate limit hit.*\n\n"
                    f"The shared API key reached its daily token limit.\n\n"
                    f"*Option 1:* Wait ~{wait_min} minutes and try again.\n\n"
                    f"*Option 2:* Add your own free Groq key:\n"
                    f"1️⃣ Visit https://console.groq.com\n"
                    f"2️⃣ Sign up → API Keys → Create key\n"
                    f"3️⃣ Send: `/key gsk_your_key_here`\n\n"
                    f"_Free tier gives you 100K tokens/day — enough for hours of audio._"
                )
            await _edit(status_msg, msg)
        elif "rate_limit" in err_str.lower() or "429" in err_str or "too many requests" in err_str.lower():
            await _edit(status_msg,
                "⏳ *API rate limit reached.*\n\n"
                "Please wait a few minutes, or add your own key with:\n"
                "`/key gsk_your_key_here`\n"
                "Get a free key at: https://console.groq.com")
        else:
            await _edit(status_msg, f"❌ Error: {err_str[:200]}\n\nPlease try again or use /help.")
        logger.warning("Pipeline error user %s: %s", user_id, e)
    except Exception as e:
        logger.error("Pipeline error for user %s", user_id, exc_info=True)
        await _edit(status_msg, f"❌ An error occurred: {str(e)[:150]}\n\nPlease try again or use /help.")
    finally:
        _processing.pop(user_id, None)
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


async def _send_results_from_url(update, file_name: str, transcript, summary, files: dict, elapsed: float, status_msg: Message, context, style_pref: str = "both"):
    """
    Send pipeline results back to the user as document files.
    Sends a language detection card first, then files.
    """
    import io as _io
    chat_id = update.effective_chat.id
    include_plain = style_pref in ("plain", "both")
    include_md    = style_pref in ("md", "both")

    # ── Language detection card ──────────────────────────────────────────
    conf = transcript.lang_probability
    conf_emoji = "🟢" if conf >= 0.90 else ("🟡" if conf >= 0.70 else "🔴")
    word_count = len(transcript.full_text.split())

    # First ~250 chars as a readable preview snippet
    preview = transcript.full_text[:250].strip()
    if len(transcript.full_text) > 250:
        # cut at last space to avoid mid-word cut
        preview = preview.rsplit(" ", 1)[0] + "…"

    await context.bot.send_message(
        chat_id,
        f"🎙 *Transcription complete*\n\n"
        f"{conf_emoji} Language: *{transcript.language_name}* ({conf:.0%} confidence)\n"
        f"📄 *{word_count:,}* words transcribed\n\n"
        f"_{preview}_",
        parse_mode=ParseMode.MARKDOWN,
    )

    await _edit(status_msg, "📦 Sending your files...")
    await _send_files_to_chat(context, chat_id, files, transcript, elapsed, style_pref)


async def _send_files_to_chat(
    context,
    chat_id: int,
    files: dict,
    transcript,
    elapsed: float,
    style_pref: str = "both",
):
    """
    Send all result files to a Telegram chat.

    Strategy for mobile users:
    - Send the most readable files first (summary .txt, transcript .txt)
      so users can open them directly without unzipping
    - Send SRT subtitles for direct use in video players
    - Send ZIP last as a convenience bundle
    - Append a footer to text files directing users to the GitHub repo
    """
    import io as _io

    GITHUB_FOOTER = "\n\n---\n🎙 Generated by AudioScribe\nhttps://github.com/Mo-Abdalkader/AudioScribe"
    include_plain = style_pref in ("plain", "both")
    include_md    = style_pref in ("md", "both")

    sent_files: list[str] = []

    # ── 1. Summaries first (most useful for mobile users) ──────────────
    for fname, data in sorted(files.items()):
        if "summary" not in fname:
            continue
        if fname.endswith(".txt") and not include_plain:
            continue
        if fname.endswith(".md") and not include_md:
            continue
        if fname.endswith(".zip"):
            continue

        # Detect language from filename (e.g. A3F9-04min-summary-EN.txt)
        parts = fname.replace(".txt","").replace(".md","").split("-")
        lang_part = parts[-1] if parts else "EN"
        lang_map = {"EN": "🇬🇧 English", "AR": "🇸🇦 Arabic", "AREG": "🇪🇬 Egyptian", "AREG": "🇪🇬 Egyptian"}
        lang_label = lang_map.get(lang_part.upper(), lang_part)
        fmt = "Markdown" if fname.endswith(".md") else "plain text"

        # Add footer to text files
        enhanced = data

        bio = _io.BytesIO(enhanced); bio.name = fname
        await context.bot.send_document(
            chat_id, document=bio, filename=fname,
            caption=f"📝 *Summary — {lang_label}* ({fmt})"
        )
        sent_files.append(fname)

    # ── 2. Transcript (with timestamps) ────────────────────────────────
    for fname, data in files.items():
        if "transcript" not in fname or not fname.endswith(".txt"):
            continue
        enhanced = data + GITHUB_FOOTER.encode("utf-8")
        bio = _io.BytesIO(enhanced); bio.name = fname
        await context.bot.send_document(
            chat_id, document=bio, filename=fname,
            caption="📄 *Full transcript* with timestamps"
        )
        sent_files.append(fname)
        break   # send only the timestamped one, not the script

    # ── 3. SRT subtitles (directly playable in VLC, MX Player etc.) ────
    srt_sent = 0
    for fname, data in sorted(files.items()):
        if not fname.endswith(".srt"):
            continue
        parts = fname.replace(".srt","").split("-")
        lang_part = parts[-1] if parts else ""
        is_translated = len(lang_part) >= 2 and "subtitles" in fname
        caption = f"🎞 *Subtitles* — {lang_part}" if lang_part else "🎞 *Subtitles*"
        caption += "\n_Open directly in VLC, MX Player, or any video player_"
        bio = _io.BytesIO(data); bio.name = fname
        await context.bot.send_document(
            chat_id, document=bio, filename=fname, caption=caption,
            parse_mode=ParseMode.MARKDOWN,
        )
        srt_sent += 1
        sent_files.append(fname)

    # ── 4. ZIP bundle last (for users who want everything) ─────────────
    zip_key = next((k for k in files if k.endswith(".zip")), None)
    if zip_key:
        bio = _io.BytesIO(files[zip_key]); bio.name = zip_key
        await context.bot.send_document(
            chat_id, document=bio, filename=zip_key,
            caption=(
                f"📦 *Complete bundle* — all {len(sent_files)} files in one ZIP\n"
                f"_Includes transcript, summaries, and subtitles_"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── 5. Done summary ─────────────────────────────────────────────────
    done_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Process another", callback_data="action:send_file"),
        InlineKeyboardButton("⚙️ Settings",        callback_data="open:settings"),
    ]])
    await context.bot.send_message(
        chat_id,
        f"✅ *All done!*\n\n"
        f"🌐 Language: *{transcript.language_name}* ({transcript.lang_probability:.0%})\n"
        f"⏱ Time: *{elapsed:.0f}s*\n"
        f"📂 {len(sent_files)} files sent + 1 ZIP\n\n"
        f"_🎙 AudioScribe — github.com/Mo-Abdalkader/AudioScribe_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=done_keyboard,
    )


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
    chat_id = update.effective_chat.id   # define early — used throughout

    try:
        # ── Download ──────────────────────────────────────────────────
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        suffix = Path(file_name).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            tg_file_obj = await context.bot.get_file(tg_file.file_id)
            await tg_file_obj.download_to_drive(str(tmp_path))
        except Exception as e:
            err_msg = str(e)
            if "File is too big" in err_msg or "too big" in err_msg.lower():
                await _edit(status_msg,
                    "⚠️ File too large for Telegram Bot API (max 20MB).\n\n"
                    "💡 *Workaround:* Upload to Google Drive/Dropbox and send the link.")
            else:
                await _edit(status_msg, f"❌ Download failed: {err_msg}")
            _processing.pop(user_id, None)
            return

        size_mb = tmp_path.stat().st_size / (1024 * 1024)
        await _edit(status_msg, f"📁 Downloaded {size_mb:.1f} MB. Starting pipeline...")

        if not _processing.get(user_id):
            return

        # ── Pipeline ──────────────────────────────────────────────────
        from core.pipeline import Pipeline, PipelineError

        prefs = _prefs(user_id)
        lang_pref    = prefs.get("lang", "auto")
        style_pref   = prefs.get("style", "md")
        mode         = prefs.get("mode", "full")
        summary_style = prefs.get("summary_style", "detailed")
        summary_tone  = prefs.get("summary_tone", "professional")
        user_key      = prefs.get("groq_key")
        subtitle_lang = prefs.get("subtitle_lang", "none")
        subtitle_langs = [] if subtitle_lang == "none" else [subtitle_lang]

        # Resolve target languages for summary
        # "auto" means: transcribe first, then summarise in detected language
        if lang_pref == "auto":
            target_langs = ["en"]   # default; will be overridden after detection
        elif lang_pref == "both":
            target_langs = ["en", "ar"]
        else:
            target_langs = [lang_pref]

        limits = config.get_user_limits(_has_own_key(user_id))

        _loop = asyncio.get_event_loop()

        async def progress(msg: str):
            await _edit(status_msg, f"⚙️ {msg}")

        def sync_progress(msg: str):
            _loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(progress(msg), loop=_loop)
            )

        fast_mode = bool(user_key and prefs.get("fast_mode"))
        pipeline = Pipeline(fast_mode=fast_mode)
        transcript, summary, files = await asyncio.to_thread(
            pipeline.run_in_memory,
            tmp_path,
            target_langs,
            Path(file_name).stem[:30],
            job_id,
            sync_progress,
            groq_key=user_key,
            cohere_key=None,
            summary_style=summary_style,
            summary_tone=summary_tone,
            mode=mode,
            subtitle_langs=subtitle_langs,
            max_size_mb=limits["max_file_mb"],
            max_duration_min=limits["max_duration_min"],
        )

        if not _processing.get(user_id):
            return

        # ── Language detection card ────────────────────────────────────
        conf = transcript.lang_probability
        conf_emoji = "🟢" if conf >= 0.90 else ("🟡" if conf >= 0.70 else "🔴")
        word_count = len(transcript.full_text.split())
        preview = transcript.full_text[:250].strip()
        if len(transcript.full_text) > 250:
            preview = preview.rsplit(" ", 1)[0] + "…"

        await context.bot.send_message(
            chat_id,
            f"🎙 *Transcription complete*\n\n"
            f"{conf_emoji} Language: *{transcript.language_name}* ({conf:.0%} confidence)\n"
            f"📄 *{word_count:,}* words transcribed\n\n"
            f"_{preview}_",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Send results ───────────────────────────────────────────────
        elapsed = time.monotonic() - start_time
        await _edit(status_msg, f"✅ Done in {elapsed:.0f}s! Sending files...")
        await _send_files_to_chat(context, chat_id, files, transcript, elapsed, style_pref)

    except Exception as e:
        logger.error("Pipeline error for user %s: %s", user_id, traceback.format_exc())
        err_str = str(e)
        if "RATE_LIMIT:" in err_str:
            try:
                wait_min = int(err_str.split("RATE_LIMIT:")[1].strip())
            except Exception:
                wait_min = 30
            has_key = bool(_prefs(user_id).get("groq_key"))
            if has_key:
                msg = f"⏳ *Rate limit hit.* Your key reached its daily limit.\nWait ~{wait_min} min and try again."
            else:
                msg = (
                    f"⏳ *Summarization rate limit hit.*\n\n"
                    f"The shared API key reached its daily limit.\n\n"
                    f"*Option 1:* Wait ~{wait_min} minutes.\n\n"
                    f"*Option 2:* Add your own free key:\n"
                    f"1️⃣ Visit https://console.groq.com\n"
                    f"2️⃣ Sign up → API Keys → Create key\n"
                    f"3️⃣ Send: `/key gsk_your_key_here`"
                )
            await _edit(status_msg, msg)
        else:
            await _edit(status_msg, f"❌ Error: {err_str[:200]}\n\nPlease try again or use /help.")
    finally:
        _processing[user_id] = False
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ── Text message handler (URLs) ──────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle plain text messages. If the message contains a supported URL
    (Google Drive, Dropbox, or direct media link), start processing it.
    Otherwise, prompt the user to send a file or URL.
    """
    text = update.effective_message.text or ""
    url, url_type = _extract_url(text)

    if url:
        await _handle_url_upload(update, context, url, url_type)
    else:
        await _reply(update,
            "🎙 Send me an audio or video file, or a link to one:\n\n"
            "• Google Drive (set to *Anyone with link*)\n"
            "• Dropbox (shared link)\n"
            "• Direct media URL (.mp3, .mp4, ...)\n\n"
            "Use /help for full instructions."
        )
