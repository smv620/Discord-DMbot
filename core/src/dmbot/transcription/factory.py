"""Pick the transcription engine from settings. The only place that knows the engines."""

from __future__ import annotations

from dmbot.transcription.base import PlaceholderTranscriber, Transcriber
from dmbot.transcription.config import TranscriptionSettings


def build_transcriber(settings: TranscriptionSettings) -> Transcriber:
    if settings.engine == "whisper-local":
        from dmbot.transcription.whisper_local import LocalWhisperTranscriber

        return LocalWhisperTranscriber(settings)
    if settings.engine == "cloud":
        from dmbot.transcription.cloud import CloudTranscriber

        return CloudTranscriber(settings)
    if settings.engine == "deepgram":
        from dmbot.transcription.deepgram import DeepgramTranscriber

        return DeepgramTranscriber(settings)
    return PlaceholderTranscriber()
