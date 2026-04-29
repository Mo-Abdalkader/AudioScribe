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

# ── Job store (in-memory; resets on restart) ────────────────────────────────
_jobs: dict[str, dict] = {}   # job_id → {status, progress, files, error}


def _job(job_id: str) -> dict:
    if job_id not in _jobs:
        _jobs[job_id] = {"status": "pending", "progress": [], "files": {}, "error": None}
    return _jobs[job_id]


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

    from bot.main import build_app, _set_commands
    _tg_app = build_app()
    await _tg_app.initialize()

    # Set bot commands
    try:
        await _set_commands(_tg_app)
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)

    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("PUBLIC_DOMAIN", "")
    if domain:
        # Webhook mode (Railway / production)
        webhook_url = f"https://{domain}/webhook"
        await _tg_app.bot.set_webhook(webhook_url)
        await _tg_app.start()
        logger.info("Telegram webhook mode: %s", webhook_url)
    else:
        # Polling mode (local dev)
        await _tg_app.bot.delete_webhook(drop_pending_updates=True)
        await _tg_app.start()
        await _tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling mode (local)")


@app.on_event("startup")
async def startup():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    config.OUTPUT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
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
    """Receive Telegram updates via webhook."""
    if not _tg_app:
        raise HTTPException(503, "Telegram bot not initialized")
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AudioScribe"}


# ── Processing API ─────────────────────────────────────────────────────────────

@app.post("/api/process")
async def process_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    langs: str = Form(default="en"),
    source_lang: str = Form(default=""),
    style: str = Form(default="both"),
    summary_style: str = Form(default="detailed"),
    summary_tone: str = Form(default="professional"),
    groq_key: str = Form(default=""),
    cohere_key: str = Form(default=""),
):
    """
    Upload an audio/video file for processing.
    Returns a job_id to poll for status.
    """
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
):
    """Run the pipeline in background and store results in _jobs."""
    job = _job(job_id)

    def progress(msg: str):
        job["progress"].append(msg)
        logger.info("[%s] %s", job_id, msg)

    try:
        from core.pipeline import Pipeline, PipelineError

        pipeline = Pipeline(source_lang=source_lang or None)
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
        )

        # Filter files by style preference
        filtered = {}
        for fname, data in files.items():
            if fname.endswith(".zip"):
                filtered[fname] = data
                continue
            if fname.endswith("_transcript.txt"):
                filtered[fname] = data
                continue
            if "_summary_" in fname:
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
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        if tmp_path.exists():
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