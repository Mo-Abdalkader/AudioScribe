# Hugging Face Spaces Deployment Guide (Free, No Credit Card)

## Step 1 — Create a Hugging Face Account

1. Go to https://huggingface.co
2. Click **Sign Up** — email + password, no credit card
3. Confirm your email

---

## Step 2 — Create a Space

1. Click your profile → **New Space**
2. Fill in:

| Field | Value |
|---|---|
| **Space Name** | `audioscribe` |
| **License** | `MIT` |
| **Space SDK** | `Docker` |
| **Hardware** | **CPU free** (2 vCPU, 16 GB RAM) |

3. Click **Create Space**

---

## Step 3 — Connect Your Repo

You have two options:

### Option A: GitHub Sync (Recommended)

In your Space settings → **Settings** → **Repository** → link your GitHub repo.

HF auto-deploys whenever you push to `main`.

### Option B: Upload Directly

```bash
git clone https://huggingface.co/spaces/YOUR_USERNAME/audioscribe
cd audioscribe
# Copy all your project files here
cp -r /path/to/AudioScribe/* .
git add .
git commit -m "Initial deploy"
git push
```

---

## Step 4 — Set Environment Variables

In your Space **Settings** → **Repository secrets** → add:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `GROQ_API_KEYS` | Your Groq API key |
| `WEB_SECRET_KEY` | Any random string |

No need to set `PORT` — it's already 7860 in the Dockerfile.

---

## Step 5 — Deploy

Push to your HF Space repo (or push to GitHub if you synced).

First build takes ~5-10 minutes (installing ffmpeg + Python packages).

Once done, your app is live at:

```
https://YOUR_USERNAME-audioscribe.hf.space
```

---

## Step 6 — Keep It Alive (Free)

HF Spaces sleep after **48 hours** of inactivity. Keep it awake with a free ping:

1. Go to https://cron-job.org (free, no credit card)
2. Click **Create Cronjob**
3. Set:
   - URL: `https://YOUR_USERNAME-audioscribe.hf.space/health`
   - Schedule: **Every 5 minutes** (or every hour — your choice)
4. Click **Create**

This pings your app regularly — it never sleeps.

---

## Important Notes

- **First request** after sleep takes ~30 seconds (cold start)
- **Bot runs in polling mode** — no webhook needed
- **All data is temporary** — files are deleted after processing
- **Dockerfile** is already in the repo — builds automatically
