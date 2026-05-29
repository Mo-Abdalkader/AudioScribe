"""
bot/main.py — Telegram bot setup and startup.
Compatible with python-telegram-bot v20.x and v21.x + Python 3.13.
"""
from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
)

import config
from bot.handlers import (
    cmd_start, cmd_help, cmd_info, cmd_settings, cmd_mode, cmd_subtitle_lang,
    cmd_lang, cmd_style, cmd_summary_style, cmd_summary_tone,
    cmd_key, cmd_fastmode, cmd_cancel,
    handle_file, handle_message, handle_callback,
)

logger = logging.getLogger(__name__)


async def _set_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start",          "Welcome & intro"),
        BotCommand("help",           "Full help"),
        BotCommand("info",           "Project & developer info"),
        BotCommand("settings",       "⚙️ Open settings panel"),
        BotCommand("mode",           "Set processing mode (full/transcript/subtitles/summary)"),
        BotCommand("lang",           "Set output language"),
        BotCommand("subtitle_lang",  "Set translated subtitle language"),
        BotCommand("style",          "Set summary format"),
        BotCommand("summary_style",  "Set detail level (brief/detailed)"),
        BotCommand("summary_tone",   "Set tone (professional/casual/technical)"),
        BotCommand("key",            "Set your Groq API key"),
        BotCommand("cancel",         "Cancel current processing"),
    ])


def build_app() -> Application:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    builder = Application.builder().token(token)

    # post_init only exists in PTB v21+; skip gracefully on v20
    try:
        import telegram
        if int(telegram.__version__.split(".")[0]) >= 21:
            builder = builder.post_init(_set_commands)
    except Exception:
        pass

    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("settings",      cmd_settings))
    app.add_handler(CommandHandler("mode",          cmd_mode))
    app.add_handler(CommandHandler("subtitle_lang", cmd_subtitle_lang))
    app.add_handler(CommandHandler("lang",          cmd_lang))
    app.add_handler(CommandHandler("style", cmd_style))
    app.add_handler(CommandHandler("summary_style", cmd_summary_style))
    app.add_handler(CommandHandler("summary_tone", cmd_summary_tone))
    app.add_handler(CommandHandler("key", cmd_key))
    app.add_handler(CommandHandler("fastmode", cmd_fastmode))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Inline keyboard button callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Only accept media files - text messages are ignored (no handler registered)
    media_filter = (
        filters.AUDIO | filters.VOICE | filters.VIDEO
        | filters.VIDEO_NOTE | filters.Document.ALL
    )
    app.add_handler(MessageHandler(media_filter, handle_file))

    # Handle text messages — detect URLs (Google Drive, Dropbox, direct links)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def run():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.WARNING,  # Only show warnings and errors in console
    )
    # Enable file logging for detailed debug
    file_handler = logging.FileHandler("audioscribe.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)
    
    issues = config.validate()
    if issues:
        for issue in issues:
            logger.error("Config issue: %s", issue)
        raise SystemExit("Fix configuration before starting.")

    logger.info("Starting AudioScribe Telegram Bot...")
    app = build_app()
    app.run_polling(drop_pending_updates=True)