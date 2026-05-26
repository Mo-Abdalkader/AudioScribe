# Render Deployment Guide (No Credit Card Needed)

## Step 1 — Sign Up

1. Go to https://render.com
2. Click **Get Started** → sign up with **GitHub**
3. **No credit card required**

---

## Step 2 — Create a Web Service

1. In the Render dashboard, click **New +** → **Web Service**
2. Connect your GitHub account and select `Mo-Abdalkader/AudioScribe`
3. Fill in:

| Field | Value |
|---|---|
| **Name** | `audioscribe` |
| **Region** | Frankfurt (closest to Egypt) or Oregon |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Instance Type** | **Free** |

---

## Step 3 — Set Environment Variables

Click **Environment** tab and add:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `GROQ_API_KEYS` | Your Groq API key from console.groq.com |
| `PUBLIC_DOMAIN` | Leave **empty** (bot uses polling mode) |
| `WEB_SECRET_KEY` | Generate a random string (any 16+ chars) |

---

## Step 4 — Deploy

Click **Create Web Service**.

Render will build and deploy. Watch the logs — first deploy takes ~5 minutes.

Once done, your app is live at: `https://audioscribe.onrender.com`

---

## Step 5 — Keep It Alive (Free, No Card)

Render free tier **sleeps after 15 min** of no traffic. The Telegram bot needs to stay awake, so:

1. Go to https://uptimerobot.com — sign up (free, no card)
2. Click **Add New Monitor**
3. Set:
   - Monitor Type: **HTTP(s)**
   - URL: `https://audioscribe.onrender.com/health`
   - Interval: **5 minutes**
4. Click **Create Monitor**

UptimeRobot pings your app every 5 min — Render never sleeps. Free forever.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails — ffmpeg not found | Check build command has `apt-get install -y ffmpeg` |
| Bot not responding | Bot uses polling mode (no PUBLIC_DOMAIN). Check logs: `apt-get install -y ffmpeg && pip install -r requirements.txt` |
| App crashes at startup | Check `.env` variables are set correctly in Render dashboard |
| 502 Bad Gateway | App needs more time to start. Render automatically retries |
