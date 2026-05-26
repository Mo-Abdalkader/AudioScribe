# Oracle Cloud Always Free — AudioScribe Deployment Guide

## 1. Sign Up & Create Instance

1. Go to https://cloud.oracle.com and sign up for **Always Free** tier
2. Once logged in, navigate to **Compute → Instances**
3. Click **Create Instance**
4. Name: `audioscribe` (or whatever you like)
5. **Image**: Canonical Ubuntu 22.04 (or 24.04)
6. **Shape**: Select `VM.Standard.A1.Flex` (ARM, Ampere)
   - Set **OCPUs** to **1** (up to 4 free)
   - **Memory** will auto-set to 6 GB (or adjust as needed)
7. **Add SSH key**: Save the private key (`.pem`) — you need this to connect
8. Click **Create**

Wait 2-3 minutes for the instance to provision. Note the **Public IP Address**.

---

## 2. SSH Into the VM

From your terminal (PowerShell on Windows, Terminal on Mac/Linux):

```powershell
ssh -i path\to\your-key.pem ubuntu@<PUBLIC_IP>
```

Or on Windows using PowerShell:

```powershell
ssh -i "C:\Users\You\Downloads\your-key.pem" ubuntu@<PUBLIC_IP>
```

---

## 3. Install Dependencies (one-time)

```bash
sudo apt update && sudo apt upgrade -y

# Python + pip
sudo apt install -y python3 python3-pip python3-venv

# FFmpeg (required for audio processing)
sudo apt install -y ffmpeg

# yt-dlp (for YouTube downloads)
sudo apt install -y yt-dlp

# nltk data (required by the summarizer)
python3 -m nltk.downloader punkt_tab
```

---

## 4. Upload Your Project

On your **local machine**, push to GitHub first:

```bash
git init
git add .
git commit -m "v14 ready for Oracle Cloud"
```

Then on the **Oracle VM**, clone it:

```bash
git clone https://github.com/YOUR_USERNAME/AudioScribe.git
cd AudioScribe
```

Or if you prefer to use `scp` directly:

```powershell
# From your local PowerShell:
scp -r C:\path\to\AudioScribe_v14 ubuntu@<PUBLIC_IP>:~/AudioScribe
```

---

## 5. Set Up Python Environment

```bash
cd ~/AudioScribe
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 6. Create .env File

```bash
nano .env
```

Paste in:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
GROQ_API_KEYS=your_groq_api_key_here
# Optional but recommended:
COHERE_API_KEY=
WEB_SECRET_KEY=generate-a-random-string-here
```

Save: `Ctrl+X` → `Y` → `Enter`

---

## 7. Open Firewall Port

Oracle blocks port 8000 by default. You need to open it:

In Oracle Cloud Console:
1. Go to **Networking → Virtual Cloud Networks**
2. Click your VCN → **Security Lists** → **Default Security List**
3. Click **Add Ingress Rules**
4. Enter:
   - Source Type: CIDR
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: TCP
   - Source Port Range: (leave blank)
   - Destination Port Range: `8000`
   - Description: `AudioScribe web UI`
5. Click **Add Ingress Rules**

---

## 8. Run the App (first test)

```bash
cd ~/AudioScribe
source venv/bin/activate
python main.py
```

You should see logs. Open your browser to: `http://<PUBLIC_IP>:8000`

Your Telegram bot should also start responding (polling mode).

Press `Ctrl+C` to stop.

---

## 9. Run Permanently with systemd

Create a service file:

```bash
sudo nano /etc/systemd/system/audioscribe.service
```

Paste:

```ini
[Unit]
Description=AudioScribe
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/AudioScribe
ExecStart=/home/ubuntu/AudioScribe/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Save (`Ctrl+X` → `Y` → `Enter`).

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable audioscribe
sudo systemctl start audioscribe
```

Check status:

```bash
sudo systemctl status audioscribe
```

View logs:

```bash
sudo journalctl -u audioscribe -f
```

---

## 10. Keep the Bot Alive

That's it — systemd keeps it running forever:
- Auto-restarts on crash
- Starts on boot
- No sleep (unlike Render/Railway free tiers)
- 6 GB RAM + 1-4 CPU cores — plenty for ffmpeg

---

## Optional: Add a Domain + HTTPS

If you want a real domain (or a free `nip.io` domain):

1. Stop the service: `sudo systemctl stop audioscribe`
2. Get a free domain or use `http://<PUBLIC_IP>.nip.io:8000`
3. Set `PUBLIC_DOMAIN` in `.env` (for webhook mode instead of polling)
4. For HTTPS, install Caddy:

```bash
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

```
audioscribe.yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
sudo systemctl enable caddy
sudo systemctl start caddy
```

Then set `PUBLIC_DOMAIN=audioscribe.yourdomain.com` in `.env` and restart.

---

## Useful Commands

| What | Command |
|---|---|
| Check status | `sudo systemctl status audioscribe` |
| View live logs | `sudo journalctl -u audioscribe -f` |
| Restart | `sudo systemctl restart audioscribe` |
| Stop | `sudo systemctl stop audioscribe` |
| Update code | `cd ~/AudioScribe && git pull && sudo systemctl restart audioscribe` |
| Update deps | `source venv/bin/activate && pip install -r requirements.txt --upgrade` |
