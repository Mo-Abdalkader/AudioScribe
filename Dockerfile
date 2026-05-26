FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg yt-dlp && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/audioscribe

EXPOSE 7860

ENV PORT=7860
ENV WEB_ONLY=

CMD ["python", "main.py"]
