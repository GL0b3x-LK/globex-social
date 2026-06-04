# Globex SM Automation — Railway production image.
#
# Based on the official Playwright Python image (Chromium + all system deps
# preinstalled), pinned to match playwright==1.60.0 in requirements.txt. Adds
# ffmpeg for the VHS video pipeline. Secrets are injected by Railway as env vars —
# never baked into the image (.env is excluded via .dockerignore).
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && python -m playwright install chromium chromium-headless-shell

COPY . .

# Railway sets $PORT at runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
