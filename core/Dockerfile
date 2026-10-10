FROM python:3.12-slim

# Set to "dev" for a CPU-only image without local Whisper (TRANSCRIBER=cloud or none).
ARG EXTRAS=whisper

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DMBOT_DATA_DIR=/data \
    HF_HOME=/models

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[${EXTRAS}]"

RUN useradd --create-home --uid 1000 dmbot && mkdir -p /data /models /var/lib/dmbot/test-recordings \
    && chown dmbot:dmbot /data /models /var/lib/dmbot/test-recordings \
    && chmod 700 /var/lib/dmbot/test-recordings
USER dmbot
VOLUME ["/data", "/models", "/var/lib/dmbot/test-recordings"]
CMD ["python", "-m", "dmbot"]
