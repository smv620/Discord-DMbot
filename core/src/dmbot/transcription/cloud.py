"""Cloud speech-to-text through any OpenAI-compatible transcription API.

Pay as you go, no GPU needed. Works with OpenAI and other providers that expose the
same ``/audio/transcriptions`` endpoint — set CLOUD_STT_URL, CLOUD_STT_API_KEY and
CLOUD_STT_MODEL. Audio is sent over HTTPS only.
"""

from __future__ import annotations

import logging

import aiohttp

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import build_prompt, clean_text, to_wav
from dmbot.transcription.config import TranscriptionSettings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30


class CloudTranscriptionError(RuntimeError):
    pass


class CloudTranscriber:
    def __init__(
        self, settings: TranscriptionSettings, session: aiohttp.ClientSession | None = None
    ) -> None:
        self._settings = settings
        self._session = session
        self._owns_session = session is None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
            )
            self._owns_session = True
        return self._session

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        s = self._settings
        form = aiohttp.FormData()
        form.add_field(
            "file", to_wav(utterance.pcm), filename="speech.wav", content_type="audio/wav"
        )
        form.add_field("model", s.cloud_model)
        form.add_field("response_format", "json")
        if s.language:
            form.add_field("language", s.language)
        prompt = build_prompt(hints)
        if prompt:
            form.add_field("prompt", prompt)

        headers = {"Authorization": f"Bearer {s.cloud_api_key}"}
        async with self._get_session().post(s.cloud_url, data=form, headers=headers) as resp:
            if resp.status != 200:
                # Never include the key or audio in errors; status + short body only.
                body = (await resp.text())[:200]
                raise CloudTranscriptionError(
                    f"Speech-to-text service returned {resp.status}: {body}"
                )
            payload = await resp.json(content_type=None)
        text = payload.get("text") if isinstance(payload, dict) else None
        return clean_text(text) if isinstance(text, str) else None

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
