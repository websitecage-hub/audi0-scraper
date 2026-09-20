FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=8000 \
    HOST=0.0.0.0 \
    TL_LIBRARY=/data

# ffmpeg (audio extraction) + chromium runtime libs (Scrapling stealth browser)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        fonts-liberation \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# deps first (better layer caching)
COPY requirements.txt ./
RUN pip install -r requirements.txt

# chromium for patchright (used by Scrapling's StealthyFetcher)
RUN python -m patchright install chromium

# app code
COPY . .

EXPOSE 8000
# gunicorn: 1 worker (capped RAM for free tier), threads for concurrency,
# long timeout (600s) so yt-dlp/ffmpeg downloads aren't killed mid-request.
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "600", "--graceful-timeout", "30", "--worker-class", "gthread", "-b", "0.0.0.0:8000", "server:app"]
