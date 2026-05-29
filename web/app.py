"""
web/app.py — FastAPI web server.
Serves the web interface and REST API for audio processing.
Also registers the Telegram webhook so both bot + web run from one process.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
)
from fastapi.staticfiles import StaticFiles
from telegram import Update
from telegram.ext import Application

import config

logger = logging.getLogger(__name__)

# ── Simple in-memory rate limiter ────────────────────────────────────────────
# Tracks request timestamps per IP. No external library needed.
from collections import defaultdict

_rate_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = 10        # max requests per window
RATE_LIMIT_WINDOW   = 3600      # 1 hour window in seconds


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds).
    Slides the window: drops timestamps older than the window, then checks count.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    # Keep only timestamps within current window
    _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]

    if len(_rate_store[ip]) >= RATE_LIMIT_REQUESTS:
        # Tell client how long until the oldest request expires
        retry_after = int(_rate_store[ip][0] + RATE_LIMIT_WINDOW - now) + 1
        return False, retry_after

    _rate_store[ip].append(now)
    return True, 0


def _get_client_ip(request: Request) -> str:
    """Get real client IP, respecting X-Forwarded-For from Railway/proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


import ipaddress as _ipaddress
import socket as _socket

# Allowed URL schemes and blocked private/internal IP ranges
_BLOCKED_HOSTS = {
    "localhost", "0.0.0.0",
    "metadata.google.internal",          # GCP metadata
    "169.254.169.254",                   # AWS/Azure metadata
}
_PRIVATE_NETWORKS = [
    _ipaddress.ip_network("10.0.0.0/8"),
    _ipaddress.ip_network("172.16.0.0/12"),
    _ipaddress.ip_network("192.168.0.0/16"),
    _ipaddress.ip_network("127.0.0.0/8"),
    _ipaddress.ip_network("169.254.0.0/16"),
    _ipaddress.ip_network("::1/128"),
    _ipaddress.ip_network("fc00::/7"),
]


def _validate_url_safe(url: str) -> tuple[bool, str]:
    """
    SSRF protection: block internal IPs, metadata endpoints, and non-http(s) schemes.
    Returns (is_safe, reason).
    """
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format."

    if parsed.scheme not in ("http", "https"):
        return False, f"URL scheme '{parsed.scheme}' is not allowed. Use http or https."

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "URL has no hostname."

    if hostname.lower() in _BLOCKED_HOSTS:
        return False, "URL points to a blocked host."

    # Resolve hostname to IP and check if it's private
    try:
        ip_str = _socket.gethostbyname(hostname)
        ip = _ipaddress.ip_address(ip_str)
        for network in _PRIVATE_NETWORKS:
            if ip in network:
                return False, "URL points to a private/internal network address."
    except _socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"
    except Exception:
        pass   # if we can't resolve, let httpx handle it

    return True, ""

# ── Job store (in-memory; resets on restart) ────────────────────────────────
JOB_TTL_SECONDS = 7200   # jobs expire after 2 hours

_jobs: dict[str, dict] = {}   # job_id → {status, progress, files, error, created_at}


def _job(job_id: str) -> dict:
    if job_id not in _jobs:
        _jobs[job_id] = {
            "status": "pending",
            "progress": [],
            "files": {},
            "error": None,
            "created_at": time.time(),   # used for TTL cleanup
        }
    return _jobs[job_id]


async def _cleanup_expired_jobs():
    """Background task: remove jobs older than JOB_TTL_SECONDS every 30 minutes."""
    while True:
        await asyncio.sleep(1800)   # run every 30 minutes
        cutoff = time.time() - JOB_TTL_SECONDS
        expired = [jid for jid, j in _jobs.items() if j.get("created_at", 0) < cutoff]
        for jid in expired:
            del _jobs[jid]
        if expired:
            logger.info("Cleaned up %d expired jobs", len(expired))


# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="AudioScribe API", version="1.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Telegram webhook integration ─────────────────────────────────────────────

_tg_app: Optional[Application] = None


async def _init_telegram():
    global _tg_app
    if not config.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return

    # Skip in web-only mode (WEB_ONLY=1 env var)
    if os.environ.get("WEB_ONLY", "").lower() == "1":
        logger.info("Web-only mode: Telegram disabled")
        return

    from bot.main import build_app, _set_commands
    _tg_app = build_app()
    await _tg_app.initialize()

    try:
        await _set_commands(_tg_app)
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)

    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("PUBLIC_DOMAIN", "")
    if domain:
        # ── Webhook mode (production) ──────────────────────────────────
        webhook_url = f"https://{domain}/webhook"
        # Use a secret token so only Telegram can hit our webhook endpoint
        secret = config.WEB_SECRET_KEY[:32] if config.WEB_SECRET_KEY else None
        try:
            await _tg_app.bot.set_webhook(
                webhook_url,
                secret_token=secret,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],   # only what we handle
            )
            await _tg_app.start()
            logger.info("✅ Telegram WEBHOOK mode: %s", webhook_url)
        except Exception as e:
            logger.error("Failed to set Telegram webhook: %s", e)
    else:
        # ── Polling mode (local dev) ───────────────────────────────────
        try:
            await _tg_app.bot.delete_webhook(drop_pending_updates=True)
            await _tg_app.start()
            await _tg_app.updater.start_polling(drop_pending_updates=True)
            logger.info("✅ Telegram POLLING mode (local dev)")
        except Exception as e:
            logger.error("Failed to start Telegram polling: %s", e)


@app.on_event("startup")
async def startup():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    config.OUTPUT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(_cleanup_expired_jobs())   # start background cleanup loop
    await _init_telegram()


@app.on_event("shutdown")
async def shutdown():
    if _tg_app:
        try:
            await _tg_app.updater.stop()
        except Exception:
            pass
        await _tg_app.stop()
        await _tg_app.shutdown()


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook. Validates secret token in production."""
    if not _tg_app:
        raise HTTPException(503, "Telegram bot not initialized")

    # Validate secret token (set during set_webhook) — only in production
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("PUBLIC_DOMAIN", "")
    if domain and config.WEB_SECRET_KEY:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        expected_token = config.WEB_SECRET_KEY[:32]
        if incoming_token != expected_token:
            logger.warning("Webhook received with invalid secret token — rejected")
            raise HTTPException(403, "Forbidden")

    data = await request.json()
    update = Update.de_json(data, _tg_app.bot)
    await _tg_app.process_update(update)
    return {"ok": True}


# ── Web UI routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>AudioScribe</h1><p>Web interface loading...</p>")


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon from static/images directory."""
    favicon_path = Path(__file__).parent / "static" / "images" / "favicon.ico"
    if favicon_path.exists():
        return Response(
            content=favicon_path.read_bytes(),
            media_type="image/x-icon",
        )
    # Fallback: try logo.png
    logo_path = Path(__file__).parent / "static" / "images" / "logo.png"
    if logo_path.exists():
        return Response(content=logo_path.read_bytes(), media_type="image/png")
    return Response(status_code=404)


@app.get("/manifest.json")
async def manifest():
    """PWA manifest."""
    manifest_path = Path(__file__).parent / "static" / "manifest.json"
    if manifest_path.exists():
        return Response(
            content=manifest_path.read_text(encoding="utf-8"),
            media_type="application/manifest+json",
        )
    return Response(status_code=404)


@app.get("/sw.js")
async def service_worker():
    """PWA service worker — must be served from root scope."""
    sw_path = Path(__file__).parent / "static" / "sw.js"
    if sw_path.exists():
        return Response(
            content=sw_path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )
    return Response(status_code=404)


@app.get("/offline.html", response_class=HTMLResponse)
async def offline():
    """Offline fallback page for PWA."""
    offline_path = Path(__file__).parent / "templates" / "offline.html"
    if offline_path.exists():
        return HTMLResponse(offline_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>You're offline</h1>")


@app.get("/health")
async def health():
    """
    Smart health check — verifies all critical dependencies are available.
    Returns 200 if healthy, 503 if any critical component is missing.
    """
    import shutil, subprocess

    checks = {}
    healthy = True

    # ffmpeg — critical for audio processing
    if shutil.which("ffmpeg"):
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            checks["ffmpeg"] = "ok"
        except Exception:
            checks["ffmpeg"] = "error"
            healthy = False
    else:
        checks["ffmpeg"] = "missing"
        healthy = False

    # yt-dlp — optional but needed for YouTube
    if shutil.which("yt-dlp"):
        checks["yt_dlp"] = "ok"
    else:
        checks["yt_dlp"] = "missing (YouTube disabled)"

    # Groq API key configured
    if config.GROQ_API_KEYS:
        checks["groq_keys"] = f"{len(config.GROQ_API_KEYS)} key(s) configured"
    else:
        checks["groq_keys"] = "missing"
        healthy = False

    # Telegram bot token
    checks["telegram"] = "configured" if config.TELEGRAM_BOT_TOKEN else "missing"

    # Job store stats
    checks["active_jobs"] = len(_jobs)

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks,
        }
    )


@app.get("/api/limits")
async def get_limits(request: Request):
    """Return remaining requests for the current IP in this window."""
    ip = _get_client_ip(request)
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    used = len([t for t in _rate_store.get(ip, []) if t > window_start])
    remaining = max(0, RATE_LIMIT_REQUESTS - used)
    reset_in = 0
    if _rate_store.get(ip):
        oldest = min(t for t in _rate_store[ip] if t > window_start) if any(t > window_start for t in _rate_store[ip]) else now
        reset_in = max(0, int(oldest + RATE_LIMIT_WINDOW - now))
    return {
        "limit": RATE_LIMIT_REQUESTS,
        "used": used,
        "remaining": remaining,
        "reset_in_seconds": reset_in,
        "window_hours": RATE_LIMIT_WINDOW // 3600,
    }


# ── Processing API ─────────────────────────────────────────────────────────────

@app.post("/api/process")
async def process_audio(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    langs: str = Form(default="en"),
    source_lang: str = Form(default=""),
    style: str = Form(default="both"),
    summary_style: str = Form(default="detailed"),
    summary_tone: str = Form(default="professional"),
    groq_key: str = Form(default=""),
    cohere_key: str = Form(default=""),
    mode: str = Form(default="full"),
    subtitle_langs: str = Form(default=""),
    fast_mode: bool = Form(default=False),
):
    """
    Upload an audio/video file for processing.
    Users with their own Groq key bypass the shared rate limit entirely.
    Returns a job_id to poll for status.
    """
    # Rate limit — own-key users are exempt (they use their own quota)
    if not groq_key:
        ip = _get_client_ip(request)
        allowed, retry_after = _check_rate_limit(ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit reached ({RATE_LIMIT_REQUESTS} requests/hour). "
                       f"Try again in {retry_after // 60 + 1} minutes, "
                       f"or add your own free Groq API key."
            )
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALL_SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format: {ext}")

    # Check size
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > config.MAX_UPLOAD_SIZE_MB:
        raise HTTPException(413, f"File too large: {size_mb:.1f} MB. Max: {config.MAX_UPLOAD_SIZE_MB} MB")

    job_id = str(uuid.uuid4())
    target_langs = [l.strip() for l in langs.split(",") if l.strip()]
    if not target_langs:
        target_langs = ["en"]

    _job(job_id)["status"] = "processing"
    _job(job_id)["filename"] = file.filename

    # Write to temp file
    suffix = ext or ".tmp"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    background_tasks.add_task(
        _run_pipeline_background,
        job_id=job_id,
        tmp_path=tmp_path,
        filename=file.filename or "audio",
        target_langs=target_langs,
        source_lang=source_lang,
        style=style,
        summary_style=summary_style,
        summary_tone=summary_tone,
        groq_key=groq_key or None,
        cohere_key=cohere_key or None,
        mode=mode,
        subtitle_langs=[l.strip() for l in subtitle_langs.split(",") if l.strip()],
        fast_mode=fast_mode,
    )

    return {"job_id": job_id, "status": "processing"}


@app.post("/api/process-url")
async def process_audio_url(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    langs: str = Form(default="en"),
    source_lang: str = Form(default=""),
    style: str = Form(default="both"),
    summary_style: str = Form(default="detailed"),
    summary_tone: str = Form(default="professional"),
    groq_key: str = Form(default=""),
    cohere_key: str = Form(default=""),
    mode: str = Form(default="full"),
    subtitle_langs: str = Form(default=""),
    fast_mode: bool = Form(default=False),
):
    ip = _get_client_ip(request)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed and not groq_key:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached ({RATE_LIMIT_REQUESTS} requests/hour). "
                   f"Try again in {retry_after // 60 + 1} minutes, or add your own Groq API key."
        )
    """
    Accept a URL (Google Drive, Dropbox, or direct media link),
    download it server-side, then run the same pipeline as /api/process.
    """
    import httpx as _httpx
    import re as _re

    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Invalid URL: must start with http:// or https://")

    # SSRF protection — block internal IPs and metadata endpoints
    is_safe, reason = _validate_url_safe(url)
    if not is_safe:
        raise HTTPException(400, f"URL not allowed: {reason}")

    # ── Detect URL type and normalize ───────────────────────────────────────
    _GDRIVE_RE = _re.compile(
        r'https?://(?:drive\.google\.com/(?:file/d/|uc\?(?:export=\w+&)?id=)|docs\.google\.com/uc\?(?:export=\w+&)?id=)'
        r'([a-zA-Z0-9_-]+)',
        _re.IGNORECASE
    )
    _DROPBOX_RE = _re.compile(r'https?://(?:www\.)?dropbox\.com/s/[^\s<>"]+', _re.IGNORECASE)
    _YOUTUBE_RE = _re.compile(
        r'https?://(?:(?:www\.)?youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})',
        _re.IGNORECASE
    )

    gdrive_match = _GDRIVE_RE.search(url)
    dropbox_match = _DROPBOX_RE.search(url)
    youtube_match = _YOUTUBE_RE.search(url)

    if youtube_match:
        download_url = f"https://www.youtube.com/watch?v={youtube_match.group(1)}"
        url_type = "youtube"
    elif gdrive_match:
        file_id = gdrive_match.group(1)
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        url_type = "gdrive"
    elif dropbox_match:
        download_url = _re.sub(r'[?&]dl=\d', '', url)
        download_url += ('&dl=1' if '?' in download_url else '?dl=1')
        url_type = "dropbox"
    else:
        download_url = url
        url_type = "direct"

    job_id = str(uuid.uuid4())
    target_langs = [l.strip() for l in langs.split(",") if l.strip()] or ["en"]

    _job(job_id)["status"] = "processing"
    _job(job_id)["filename"] = url

    background_tasks.add_task(
        _run_url_pipeline_background,
        job_id=job_id,
        download_url=download_url,
        url_type=url_type,
        target_langs=target_langs,
        source_lang=source_lang,
        style=style,
        summary_style=summary_style,
        summary_tone=summary_tone,
        groq_key=groq_key or None,
        cohere_key=cohere_key or None,
        mode=mode,
        subtitle_langs=[l.strip() for l in subtitle_langs.split(",") if l.strip()],
        fast_mode=fast_mode,
    )
    return {"job_id": job_id, "status": "processing"}


async def _run_pipeline_background(
    job_id: str,
    tmp_path: Path,
    filename: str,
    target_langs: list[str],
    source_lang: str = "",
    style: str = "both",
    summary_style: str = "detailed",
    summary_tone: str = "professional",
    groq_key: Optional[str] = None,
    cohere_key: Optional[str] = None,
    mode: str = "full",
    subtitle_langs: list[str] = None,
    fast_mode: bool = False,
):
    """Run the pipeline in background and store results in _jobs."""
    job = _job(job_id)
    if subtitle_langs is None:
        subtitle_langs = []

    def progress(msg: str):
        job["progress"].append(msg)
        logger.info("[%s] %s", job_id, msg)

    try:
        from core.pipeline import Pipeline, PipelineError

        pipeline = Pipeline(source_lang=source_lang or None, fast_mode=fast_mode)
        transcript, summary, files = await asyncio.to_thread(
            pipeline.run_in_memory,
            tmp_path,
            target_langs,
            Path(filename).stem[:30],
            job_id,
            progress,
            groq_key=groq_key,
            cohere_key=cohere_key,
            summary_style=summary_style,
            summary_tone=summary_tone,
            mode=mode,
            subtitle_langs=subtitle_langs,
        )

        # Filter files by style preference
        filtered = {}
        for fname, data in files.items():
            if fname.endswith(".zip"):
                filtered[fname] = data; continue
            # transcript and subtitle files always included
            if fname.endswith(".txt") and ("transcript" in fname or "script" in fname):
                filtered[fname] = data; continue
            if fname.endswith(".srt") or fname.endswith(".vtt"):
                filtered[fname] = data; continue
            # summary files filtered by style preference
            if "summary" in fname:
                if fname.endswith(".txt") and style in ("plain", "both"):
                    filtered[fname] = data
                elif fname.endswith(".md") and style in ("md", "both"):
                    filtered[fname] = data

        job["status"] = "done"
        job["files"] = filtered
        job["detected_lang"] = transcript.detected_language
        job["lang_name"] = transcript.language_name
        job["transcript_preview"] = transcript.full_text[:500]

    except Exception as e:
        logger.exception("Background pipeline failed for job %s", job_id)
        err_str = str(e)
        # Surface rate limit with wait time extracted from summarizer
        if "RATE_LIMIT:" in err_str:
            try:
                wait_min = int(err_str.split("RATE_LIMIT:")[1].strip())
            except Exception:
                wait_min = 30
            job["status"] = "error"
            job["error"] = (
                f"Summarization rate limit reached. "
                f"Please try again in ~{wait_min} minutes, "
                f"or add your own Groq API key in the field below."
            )
        else:
            job["status"] = "error"
            job["error"] = err_str
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


async def _run_url_pipeline_background(
    job_id: str,
    download_url: str,
    url_type: str,
    target_langs: list[str],
    source_lang: str = "",
    style: str = "both",
    summary_style: str = "detailed",
    summary_tone: str = "professional",
    groq_key: Optional[str] = None,
    cohere_key: Optional[str] = None,
    mode: str = "full",
    subtitle_langs: list[str] = None,
    fast_mode: bool = False,
):
    """Download from URL then run the pipeline — used by /api/process-url."""
    import httpx as _httpx

    job = _job(job_id)
    if subtitle_langs is None:
        subtitle_langs = []

    def progress(msg: str):
        job["progress"].append(msg)
        logger.info("[%s] %s", job_id, msg)

    tmp_path: Optional[Path] = None
    try:
        progress("Downloading from URL...")

        _ct_to_ext = {
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/ogg": ".ogg",
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
            "audio/aac": ".aac", "audio/webm": ".webm",
            "video/mp4": ".mp4", "video/x-matroska": ".mkv",
            "video/webm": ".webm", "video/quicktime": ".mov",
            "video/x-msvideo": ".avi", "application/octet-stream": ".mp4",
        }

        # ── YouTube: use yt-dlp ──────────────────────────────────────────
        if url_type == "youtube":
            import subprocess, glob, shutil
            if not shutil.which("yt-dlp"):
                job["status"] = "error"
                job["error"] = "yt-dlp not installed. Run: pip install yt-dlp"
                return

            progress("Fetching YouTube audio with yt-dlp...")
            tmp_dir = tempfile.mkdtemp()
            output_template = f"{tmp_dir}/audio.%(ext)s"
            max_mb = config.MAX_UPLOAD_SIZE_MB

            dl_result = subprocess.run(
                [
                    "yt-dlp", "--no-playlist",
                    "--age-limit", "99",
                    "--geo-bypass",
                    "--extractor-args", "youtube:player_client=android,web",
                    "--extractor-retries", "3",
                    "--retries", "3",
                    "-f", "bestaudio/best",
                    "--extract-audio", "--audio-format", "m4a",
                    "--audio-quality", "0",
                    "--max-filesize", f"{max_mb}M",
                    "-o", output_template,
                    download_url,
                ],
                capture_output=True, text=True, timeout=300
            )
            if dl_result.returncode != 0:
                stderr = dl_result.stderr.lower()
                # Provide full error for debugging
                job["status"] = "error"
                job["error"] = f"yt-dlp failed: {dl_result.stderr[:400]}"
                return

            matches = glob.glob(f"{tmp_dir}/audio.*")
            if not matches:
                job["status"] = "error"
                job["error"] = "YouTube download produced no output."
                return
            tmp_path = Path(matches[0])

        # ── Google Drive / Dropbox / Direct: httpx streaming ────────────
        else:
            async with _httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                async with client.stream("GET", download_url) as resp:
                    if resp.status_code != 200:
                        job["status"] = "error"
                        job["error"] = f"Download failed: HTTP {resp.status_code}"
                        return

                    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

                    # Google Drive returns HTML for private/invalid files
                    if url_type == "gdrive" and content_type in ("text/html", "application/xhtml+xml"):
                        job["status"] = "error"
                        job["error"] = (
                            "Google Drive file is private or link is invalid. "
                            "Open Drive → Share → change to 'Anyone with the link'."
                        )
                        return

                    suffix = _ct_to_ext.get(content_type, ".mp4")
                    max_bytes = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024

                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                        downloaded = 0
                        async for chunk in resp.aiter_bytes(1024 * 256):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                tmp_path.unlink(missing_ok=True)
                                job["status"] = "error"
                                job["error"] = f"File too large (>{config.MAX_UPLOAD_SIZE_MB} MB)"
                                return
                            tmp.write(chunk)

            size_mb = downloaded / (1024 * 1024)
            progress(f"Downloaded {size_mb:.1f} MB. Processing...")

        from core.pipeline import Pipeline, PipelineError
        pipeline = Pipeline(source_lang=source_lang or None, fast_mode=fast_mode)
        transcript, summary, files = await asyncio.to_thread(
            pipeline.run_in_memory,
            tmp_path,
            target_langs,
            f"url_file_{job_id[:8]}",
            job_id,
            progress,
            groq_key=groq_key,
            cohere_key=cohere_key,
            summary_style=summary_style,
            summary_tone=summary_tone,
            mode=mode,
            subtitle_langs=subtitle_langs,
        )

        # Filter by style
        filtered = {}
        for fname, data in files.items():
            if fname.endswith(".zip"):
                filtered[fname] = data; continue
            # transcript and subtitle files always included
            if fname.endswith(".txt") and ("transcript" in fname or "script" in fname):
                filtered[fname] = data; continue
            if fname.endswith(".srt") or fname.endswith(".vtt"):
                filtered[fname] = data; continue
            # summary files filtered by style preference
            if "summary" in fname:
                if fname.endswith(".txt") and style in ("plain", "both"):
                    filtered[fname] = data
                elif fname.endswith(".md") and style in ("md", "both"):
                    filtered[fname] = data

        job["status"] = "done"
        job["files"] = filtered
        job["detected_lang"] = transcript.detected_language
        job["lang_name"] = transcript.language_name
        job["transcript_preview"] = transcript.full_text[:500]

    except Exception as e:
        logger.exception("URL pipeline failed for job %s", job_id)
        err_str = str(e)
        if "RATE_LIMIT:" in err_str:
            try:
                wait_min = int(err_str.split("RATE_LIMIT:")[1].strip())
            except Exception:
                wait_min = 30
            job["status"] = "error"
            job["error"] = (
                f"Summarization rate limit reached. "
                f"Please try again in ~{wait_min} minutes, "
                f"or add your own Groq API key in the field below."
            )
        else:
            job["status"] = "error"
            job["error"] = err_str
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    """Poll for job status and progress."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    j = _jobs[job_id]
    return {
        "job_id": job_id,
        "status": j["status"],
        "progress": j.get("progress", []),
        "error": j.get("error"),
        "detected_lang": j.get("detected_lang"),
        "lang_name": j.get("lang_name"),
        "transcript_preview": j.get("transcript_preview"),
        "files": list(j.get("files", {}).keys()),
    }


@app.get("/api/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a specific output file."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    j = _jobs[job_id]
    if j["status"] != "done":
        raise HTTPException(400, "Job not complete")

    files = j.get("files", {})
    if filename not in files:
        raise HTTPException(404, f"File not found: {filename}")

    data = files[filename]
    media_type = "application/zip" if filename.endswith(".zip") else "text/plain; charset=utf-8"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/job/{job_id}")
async def delete_job(job_id: str):
    """Clean up a completed job from memory."""
    if job_id in _jobs:
        del _jobs[job_id]
    return {"deleted": job_id}


# ── Entry point ─────────────────────────────────────────────────────────────

def run():
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=config.WEB_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    run()