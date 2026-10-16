FROM python:3.12-slim

# Set to "dev" for a CPU-only image without local Whisper (TRANSCRIBER=cloud or none).
ARG EXTRAS=whisper
# The commit this image is built from; DMbot's links to its own documents point at it (#1195).
# Compose passes it from the host (GIT_COMMIT=$(git rev-parse HEAD) docker compose up --build).
ARG GIT_COMMIT=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GIT_COMMIT=$GIT_COMMIT \
    DMBOT_DATA_DIR=/data \
    HF_HOME=/models

# ffmpeg mixes the recap's narration, music and ambience (#1383): version 4.4 or newer.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[${EXTRAS}]"

# /data/recap-audio is its own volume in compose (#1418): made here and owned by dmbot, so a new
# volume mounted over it starts out writable by the bot.
RUN useradd --create-home --uid 1000 dmbot \
    && mkdir -p /data/recap-audio /models /var/lib/dmbot/test-recordings \
    && chown dmbot:dmbot /data /data/recap-audio /models /var/lib/dmbot/test-recordings \
    && chmod 700 /var/lib/dmbot/test-recordings
USER dmbot
VOLUME ["/data", "/models", "/var/lib/dmbot/test-recordings"]
CMD ["python", "-m", "dmbot"]
