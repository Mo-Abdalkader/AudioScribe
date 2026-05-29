FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -U yt-dlp

COPY . .

RUN mkdir -p /tmp/audioscribe

# Railway provides PORT automatically
ENV WEB_ONLY=1

EXPOSE 8080

CMD ["python", "main.py"]
