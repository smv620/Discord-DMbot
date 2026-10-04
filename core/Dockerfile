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

RUN useradd --create-home --uid 1000 dmbot && mkdir -p /data /models \
    && chown dmbot:dmbot /data /models
USER dmbot
VOLUME ["/data", "/models"]
CMD ["python", "-m", "dmbot"]
