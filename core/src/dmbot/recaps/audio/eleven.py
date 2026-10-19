"""The voice company's four calls for a recap (#1383): speech with timestamps, music, a looping
ambience bed and a pronunciation dictionary.

Everything goes through one injectable `Transport` (like the Stripe provider's), so tests use a
fake and nothing here opens a connection of its own. The key comes from the environment only, is
held in the real transport, and is never logged, put in an error message or shown. Errors carry a
kind and a status, never a body: a body could hold the text that was sent.

The request shapes are the ones the company's documentation gave when the design was made
(#1381). They live in this one module so a change on their side is a change here; dev1's live
run on the server is the proof they still hold.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

import aiohttp

from dmbot.recaps.audio.names import Rule

log = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

API_BASE = "https://api.elevenlabs.io"
# The newest model that understands bracketed delivery tags; recorded here, in one place.
SPEECH_MODEL = "eleven_v3"
MUSIC_MODEL = "music_v1"
SAMPLE_RATE = 44100  # the mix and the deliverables
# 24 kHz 16-bit mono PCM: the highest PCM rate every plan may use (44.1 kHz is for the top tiers),
# plenty for a narrator, and the mix brings it up to SAMPLE_RATE.
SPEECH_RATE = 24000
SPEECH_FORMAT = f"pcm_{SPEECH_RATE}"
# The models that take the neighbouring paragraphs' words (request stitching). The tag model (v3)
# does not, so those fields are left out for it. Pronunciation dictionaries (alias rules) work
# with every model, v3 included.
CONTEXT_MODELS = frozenset({"eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5"})
SOUND_MODEL = "eleven_text_to_sound_v2"  # the one that can make a seamless loop
MUSIC_FORMAT = "mp3_44100_128"
VOICE_SETTINGS_VERSION = 1  # bump with the numbers below: stored recaps say which they used
VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}
CREDITS_PER_CHAR = 1
CREDITS_PER_MUSIC_MINUTE = 900
CREDITS_PER_SOUND = 200
SPEECH_TIMEOUT_S = 180
MUSIC_TIMEOUT_S = 420
MAX_AUDIO_BYTES = 60_000_000  # a reply bigger than this is refused (10 minutes of 44.1 kHz PCM)

# (method, path, json body) -> (status, body bytes)
Transport = Callable[[str, str, "Mapping[str, Any] | None"], Awaitable[tuple[int, bytes]]]


STEPS = ("speech", "music", "ambience", "pronunciation dictionary")
# What to check for each kind, in plain words, for the test tool's report.
CHECKS: dict[str, str] = {
    "auth": "check that ELEVENLABS_API_KEY is right and has not expired.",
    "plan": (
        "the account is out of credits, or its plan does not include this "
        "(music needs a paid plan)."
    ),
    "refused": "the request was turned down.",
    "busy": "the service was busy or down; try again in a few minutes.",
    "network": "the connection failed or timed out; check the server's network.",
    "reply": (
        "the answer was not audio DMbot could read; try again, and say so if it keeps happening."
    ),
}


def check_line(exc: ProviderError) -> str:
    """What to check, in plain words. A refused speech call is most often the voice id; the other
    calls do not use one, so only speech is told to look there."""
    line = CHECKS.get(exc.kind, "an unknown kind of failure; look at the server log.")
    if exc.kind == "refused" and exc.step == "speech":
        line += " Check that the voice id (ELEVENLABS_VOICE_ID) is one this account may use."
    return line


def step(name: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Name the call in any `ProviderError` it raises, so a failure says which call it was."""

    def wrap(call: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(call)
        async def named(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await call(*args, **kwargs)
            except ProviderError as exc:
                if not exc.step:
                    exc.step = name
                raise

        return named

    return wrap


class ProviderError(Exception):
    """A call failed. `kind` says how, in a fixed word; never the reply's body."""

    def __init__(self, kind: str, status: int = 0, step: str = "") -> None:
        super().__init__(f"{kind} ({status})" if status else kind)
        self.kind = kind
        self.status = status
        self.step = step  # which call failed (`STEPS`); a fixed word, never what was sent

    def describe(self) -> str:
        """`auth, HTTP 401, during speech`: for the log and the test tool. Fixed words and a
        number only: never the reply's body, the text sent or the key."""
        status = f"HTTP {self.status}" if self.status else "no HTTP reply"
        return f"{self.kind}, {status}, during {self.step or 'an unnamed call'}"

    @property
    def permanent(self) -> bool:
        """Whether trying again would fail the same way (a bad key, a refused request)."""
        return self.kind in ("auth", "refused", "plan")


@dataclass(frozen=True, slots=True)
class Locator:
    """A pronunciation dictionary at the company (its id and the version in use)."""

    dictionary_id: str
    version_id: str


@dataclass(frozen=True, slots=True)
class Speech:
    pcm: bytes  # 16-bit mono at SAMPLE_RATE
    starts: tuple[float, ...]  # seconds from the start of this piece, for each sent character
    ends: tuple[float, ...]

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (2 * SPEECH_RATE)


class HttpTransport:
    """The real calls. The key is held here and nowhere else."""

    def __init__(self, api_key: str, base: str = API_BASE) -> None:
        self._headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> tuple[int, bytes]:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        # Music can take minutes, a paragraph of speech should not: each call has its own limit
        # (making the connection is quick either way).
        slow = "/v1/music" in path or "sound-generation" in path
        timeout = aiohttp.ClientTimeout(
            total=MUSIC_TIMEOUT_S if slow else SPEECH_TIMEOUT_S, connect=10
        )
        try:
            async with self._http.request(
                method,
                self._base + path,
                data=None if body is None else json.dumps(body),
                headers=self._headers,
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    return response.status, b""  # the body is never kept: it could echo the text
                # Read to the end in pieces (a single read() returns only what has arrived so
                # far), and stop at the limit instead of filling memory.
                data = bytearray()
                async for chunk in response.content.iter_chunked(1 << 16):
                    data += chunk
                    if len(data) > MAX_AUDIO_BYTES:
                        raise ProviderError("reply")
                return response.status, bytes(data)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ProviderError("network") from exc


def _fail(status: int) -> ProviderError:
    if status in (401, 403):
        return ProviderError("auth", status)
    if status == 402:
        return ProviderError("plan", status)  # out of credits, or a plan without the feature
    if status == 429 or status >= 500:
        return ProviderError("busy", status)
    return ProviderError("refused", status)


class ElevenLabs:
    """The calls. `credits` counts what was asked for, by the company's own price list, so the
    cost log and the cap use the same figure."""

    def __init__(
        self,
        transport: Transport,
        *,
        voice_id: str,
        speech_model: str = SPEECH_MODEL,
        music_model: str = MUSIC_MODEL,
    ) -> None:
        self._http = transport
        self._voice = voice_id
        self._speech_model = speech_model
        self._music_model = music_model
        self.credits = 0

    @step("speech")
    async def speech(
        self,
        text: str,
        *,
        previous_text: str = "",
        next_text: str = "",
        seed: int,
        dictionary: Locator | None = None,
    ) -> Speech:
        """One paragraph read aloud, with the time each character begins and ends."""
        body: dict[str, Any] = {
            "text": text,
            "model_id": self._speech_model,
            "voice_settings": VOICE_SETTINGS,
            "seed": seed % (2**32),
            "apply_text_normalization": "auto",
        }
        takes_context = self._speech_model in CONTEXT_MODELS
        if previous_text and takes_context:
            body["previous_text"] = previous_text
        if next_text and takes_context:
            body["next_text"] = next_text
        if dictionary is not None:
            body["pronunciation_dictionary_locators"] = [
                {
                    "pronunciation_dictionary_id": dictionary.dictionary_id,
                    "version_id": dictionary.version_id,
                }
            ]
        status, raw = await self._http(
            "POST",
            f"/v1/text-to-speech/{self._voice}/with-timestamps?output_format={SPEECH_FORMAT}",
            body,
        )
        if status != 200:
            raise _fail(status)
        self.credits += CREDITS_PER_CHAR * len(text)
        if len(raw) > MAX_AUDIO_BYTES:
            raise ProviderError("reply")
        try:
            data = json.loads(raw)
            pcm = base64.b64decode(data["audio_base64"], validate=True)
            align = data["alignment"]
            starts = tuple(float(x) for x in align["character_start_times_seconds"])
            ends = tuple(float(x) for x in align["character_end_times_seconds"])
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise ProviderError("reply") from exc
        if not pcm or len(starts) != len(ends):
            raise ProviderError("reply")
        return Speech(pcm, starts, ends)

    @step("music")
    async def music(self, request: Mapping[str, Any], *, seconds: float) -> bytes:
        """The piece for a composition plan (`music.composition_request`), as an mp3."""
        body = {**request, "model_id": self._music_model}
        if "composition_plan" in body:
            # The company takes `force_instrumental` only with a prompt, not with a plan; a plan
            # whose sections have no lyric lines (ours never has any) is instrumental already.
            body.pop("force_instrumental", None)
        status, raw = await self._http("POST", f"/v1/music?output_format={MUSIC_FORMAT}", body)
        if status != 200:
            raise _fail(status)
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            raise ProviderError("reply")
        self.credits += round(CREDITS_PER_MUSIC_MINUTE * seconds / 60)
        return raw

    @step("ambience")
    async def ambience(self, request: Mapping[str, Any]) -> bytes:
        """One looping sound bed (`music.Ambience.as_request`), as an mp3."""
        status, raw = await self._http(
            "POST",
            f"/v1/sound-generation?output_format={MUSIC_FORMAT}",
            {**request, "model_id": SOUND_MODEL},
        )
        if status != 200:
            raise _fail(status)
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            raise ProviderError("reply")
        self.credits += CREDITS_PER_SOUND
        return raw

    async def forget_dictionary(self, dictionary_id: str) -> None:
        """Delete an older dictionary at the company (best effort: a failure is only a leftover)."""
        try:
            await self._http("DELETE", f"/v1/pronunciation-dictionaries/{dictionary_id}", None)
        except ProviderError:
            log.info("Recap audio: an old dictionary was left at the provider")

    @step("pronunciation dictionary")
    async def dictionary(self, name: str, rules: Sequence[Rule]) -> Locator:
        """A pronunciation dictionary made from alias rules (text to text)."""
        body = {"name": name, "rules": [r.as_request() for r in rules]}
        status, raw = await self._http(
            "POST", "/v1/pronunciation-dictionaries/add-from-rules", body
        )
        if status != 200:
            raise _fail(status)
        try:
            data = json.loads(raw)
            return Locator(str(data["id"]), str(data["version_id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("reply") from exc
