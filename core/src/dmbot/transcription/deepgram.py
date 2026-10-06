"""Deepgram Nova-3 speech-to-text (TRANSCRIBER=deepgram, #170).

Each finished piece of speech is one request to Deepgram's pre-recorded API, the same
way DMbot hands clips to local Whisper. Names DMbot expects (players today; campaign
names later, #126) go along as keyterms, which is what lifted Deepgram to 23/24 D&D
terms in the quick comparison (docs/testing-history.log, 2026-10-05).

Audio is sent over HTTPS only, with ``mip_opt_out=true`` so Deepgram doesn't keep it to
train its models. The key goes in a header and is never logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriptionProblem, clean_text, to_wav
from dmbot.transcription.config import TranscriptionSettings

log = logging.getLogger(__name__)

# Two tries (4 s + 1 s pause + 4 s) fit inside the pipeline's 10 s minimum budget per
# clip (#155), so a hung Deepgram shows up as a failure ("isn't working"), not as
# "couldn't keep up". Replies took 0.12-0.6 s in testing.
REQUEST_TIMEOUT_S = 4
CONNECT_TIMEOUT_S = 2
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAY_S = 1.0
# Plain reasons the DM screen can show (the alert adds what to do next).
STATUS_REASONS = {
    400: "Deepgram couldn't use the request",
    401: "Deepgram didn't accept DEEPGRAM_API_KEY",
    402: "the Deepgram account is out of credit",
    403: "Deepgram didn't accept DEEPGRAM_API_KEY",
}
# The same for the DM screen: no settings names (#99).
DM_REASONS = {401: "Deepgram didn't accept DMbot's key", 403: "Deepgram didn't accept DMbot's key"}
# Statuses the host can fix in .env or their Deepgram account (not an outage).
HOST_FIXABLE = frozenset({400, 401, 402, 403})
# Deepgram rejects keyterm lists over about 500 tokens. Names are short, so a cap on the
# count and total length keeps well inside it.
MAX_KEYTERMS = 50
MAX_KEYTERM_CHARS = 1000


class DeepgramError(TranscriptionProblem):
    """Safe to show and log: never contains the key, players' names or the reply body."""


def keyterms(hints: list[str]) -> list[str]:
    """Hints as keyterms: tidied, de-duplicated (ignoring case), capped."""
    seen: set[str] = set()
    terms: list[str] = []
    length = 0
    for raw in hints:
        term = " ".join(raw.split())
        key = term.casefold()
        if not term or key in seen:
            continue
        if len(terms) >= MAX_KEYTERMS or length + len(term) > MAX_KEYTERM_CHARS:
            break
        seen.add(key)
        terms.append(term)
        length += len(term)
    return terms


def request_params(settings: TranscriptionSettings, hints: list[str]) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),  # punctuation and tidy numbers
        ("mip_opt_out", "true"),  # don't keep players' voices for model training
    ]
    if settings.language:
        params.append(("language", settings.language))
    else:
        params.append(("detect_language", "true"))
    params += [("keyterm", term) for term in keyterms(hints)]
    return params


def transcript(payload: Any) -> str | None:
    """The text from Deepgram's reply, or None if it has none."""
    try:
        text = payload["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        return None
    return clean_text(text) if isinstance(text, str) else None


class DeepgramTranscriber:
    def __init__(
        self, settings: TranscriptionSettings, session: aiohttp.ClientSession | None = None
    ) -> None:
        self._settings = settings
        self._session = session
        self._owns_session = session is None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            )
            self._owns_session = True
        return self._session

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        s = self._settings
        headers = {"Authorization": f"Token {s.deepgram_api_key}", "Content-Type": "audio/wav"}
        body = to_wav(utterance.pcm)
        params = request_params(s, hints)
        for attempt in (1, 2):
            try:
                async with self._get_session().post(
                    s.deepgram_url, params=params, data=body, headers=headers
                ) as resp:
                    if resp.status == 200:
                        return transcript(await resp.json(content_type=None))
                    if attempt == 1 and resp.status in RETRY_STATUSES:
                        await asyncio.sleep(RETRY_DELAY_S)
                        continue
                    # Status only: error bodies can echo request details.
                    reason = STATUS_REASONS.get(resp.status, "Deepgram had a problem")
                    raise DeepgramError(
                        f"{reason} (HTTP {resp.status})",
                        host_can_fix=resp.status in HOST_FIXABLE,
                        for_dm=DM_REASONS.get(resp.status, reason),
                    )
            except aiohttp.ClientConnectionError as exc:
                # A dropped or stale connection, or a slow connect: worth one more try.
                if attempt == 1:
                    continue
                raise DeepgramError(
                    f"couldn't reach Deepgram ({type(exc).__name__})",
                    for_dm="DMbot couldn't reach Deepgram",
                ) from None
            except TimeoutError:
                # The whole request took too long: not retried (a second wait would run
                # past the clip budget). The pipeline's own budget cancels the task
                # instead (CancelledError), which isn't caught here.
                raise DeepgramError("Deepgram took too long to answer") from None
            except aiohttp.ClientError as exc:
                # aiohttp's own messages can include the request URL, whose query holds
                # players' names (keyterms): keep only the error's type.
                raise DeepgramError(
                    f"Deepgram request failed ({type(exc).__name__})",
                    for_dm="DMbot's request to Deepgram failed",
                ) from None
            except ValueError:
                raise DeepgramError("Deepgram sent a reply DMbot couldn't read") from None
        return None  # pragma: no cover  # loop always returns or raises

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
