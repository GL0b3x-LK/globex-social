# Globex SM Automation — Railway production image.
#
# Python 3.12 is REQUIRED (the code uses StrEnum [3.11+] and PEP 695 generics
# [3.12+]). The Playwright base images track the distro's system Python (jammy =
# 3.10), which breaks those — so we start from python:3.12 and install the
# Chromium browser + its OS deps + ffmpeg (VHS video) ourselves.
# Secrets are injected by Railway as env vars; .env is excluded via .dockerignore.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && python -m playwright install-deps chromium \
    && python -m playwright install chromium chromium-headless-shell

COPY . .

# Railway sets $PORT at runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
