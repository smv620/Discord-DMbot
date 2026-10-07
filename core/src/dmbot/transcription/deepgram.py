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
import math
from typing import Any

import aiohttp

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriptionProblem, clean_text, to_wav
from dmbot.transcription.config import TranscriptionSettings

log = logging.getLogger(__name__)

# Two tries (4 s + a pause of at most 2 s + 4 s) fit inside the pipeline's 10 s minimum
# budget per clip (#155), so a hung Deepgram shows up as a failure ("isn't working"), not
# as "couldn't keep up". Replies took 0.12-0.6 s in testing. A refused keyterm list (#209)
# adds one or two more requests; refusals come back fast, and the budget still cuts off
# the rare clip that is refused slowly.
REQUEST_TIMEOUT_S = 4
CONNECT_TIMEOUT_S = 2
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAY_S = 1.0  # when Deepgram doesn't say how long to wait (Retry-After)
# The longest wait honoured (#173). One worker writes for every table, so a longer wait
# would hold all of them up: the clip fails as "busy" instead. 2 s keeps two tries and the
# wait inside the clip budget (4 + 2 + 4 = 10 s).
MAX_RETRY_WAIT_S = 2.0
# The longest a Retry-After is remembered for every clip: after this, a clip asks again,
# so no header (a huge number, or an hour from a proxy) can silence the table for long.
MAX_BUSY_WINDOW_S = 60.0
BUSY = "Deepgram is busy"
# Reasons for the log (with settings names); DM_REASONS has the DM screen's words.
STATUS_REASONS = {
    400: "Deepgram couldn't use the request",
    429: BUSY,
    503: BUSY,
    401: "Deepgram didn't accept DEEPGRAM_API_KEY",
    402: "the Deepgram account is out of credit",
    403: "Deepgram didn't accept DEEPGRAM_API_KEY",
}
# The same for the DM screen: no settings names (#99).
DM_REASONS = {401: "Deepgram didn't accept DMbot's key", 403: "Deepgram didn't accept DMbot's key"}
# Statuses the host can fix in .env or their Deepgram account (not an outage).
HOST_FIXABLE = frozenset({400, 401, 402, 403})
_sleep = asyncio.sleep  # replaced in tests


def _clock() -> float:  # replaced in tests
    return asyncio.get_running_loop().time()


# Deepgram rejects keyterm lists over about 500 tokens with a 400. Invented names split
# into many tokens, so the length cap is cautious, and a 400 with keyterms is retried
# with half of them, then none (#209).
MAX_KEYTERMS = 50
MAX_KEYTERM_CHARS = 600
MIN_KEYTERM_CHARS = 100  # the lowest a refusal can lower the length cap to


class DeepgramError(TranscriptionProblem):
    """Safe to show and log: never contains the key, players' names or the reply body."""


class _KeytermsRefused(Exception):
    """Deepgram answered 400 to a request that carried keyterms."""


def keyterms(hints: list[str], max_chars: int = MAX_KEYTERM_CHARS) -> list[str]:
    """Hints as keyterms: tidied, de-duplicated (ignoring case), capped."""
    seen: set[str] = set()
    terms: list[str] = []
    length = 0
    for raw in hints:
        term = " ".join(raw.split())
        key = term.casefold()
        if not term or key in seen:
            continue
        if len(terms) >= MAX_KEYTERMS:
            break
        if length + len(term) > max_chars:
            continue  # one long name shouldn't push out the shorter ones after it
        seen.add(key)
        terms.append(term)
        length += len(term)
    return terms


def fallbacks(terms: list[str]) -> list[list[str]]:
    """The keyterm lists to try in turn when Deepgram refuses them: all, the most
    relevant half (hints come most relevant first), then none."""
    lists = [terms]
    if len(terms) > 1:
        lists.append(terms[: len(terms) // 2])
    if terms:
        lists.append([])
    return lists


def retry_after_s(value: str | None) -> float | None:
    """Seconds from a Retry-After header, or None if it's missing, a date, negative or not
    a finite number: then the usual pause applies (#173)."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None  # an HTTP date: rare from an API, so use the usual pause
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def request_params(settings: TranscriptionSettings, terms: list[str]) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),  # punctuation and tidy numbers
        ("mip_opt_out", "true"),  # don't keep players' voices for model training
    ]
    if settings.language:
        params.append(("language", settings.language))
    else:
        params.append(("detect_language", "true"))
    params += [("keyterm", term) for term in terms]
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
        # Keyterm length that Deepgram accepted after a refusal, per listening session
        # (one campaign in one server), so one campaign's names never limit another's.
        # An entry is added only after a refusal, which is rare.
        self._max_keyterm_chars: dict[tuple[int, int], int] = {}
        # Set when a 400 came back even without keyterms: a settings problem, so later
        # 400s raise at once instead of paying for the fallbacks on every clip. Any
        # answer clears it (one clip Deepgram couldn't read isn't a settings problem).
        self._400_is_settings = False
        # Loop time until which Deepgram asked DMbot to wait (Retry-After, at most
        # MAX_BUSY_WINDOW_S). It applies to the whole account, so every clip waits for it
        # instead of asking again first. A 200 to a request sent after it was set clears
        # it; otherwise it expires on its own.
        self._busy_until = 0.0
        self._busy_set_at = 0.0

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
        body = to_wav(utterance.pcm)
        key = (utterance.guild_id, utterance.session)
        terms = keyterms(hints, self._max_keyterm_chars.get(key, MAX_KEYTERM_CHARS))
        tries = fallbacks(terms) if not self._400_is_settings else [terms]
        for n, sent in enumerate(tries):
            try:
                text = await self._request(body, sent, fallback=n < len(tries) - 1)
            except _KeytermsRefused:
                # Counts only: the keyterms are players' and characters' names.
                log.warning(
                    "Deepgram refused %d keyterms (%d characters); trying %d",
                    len(sent),
                    sum(map(len, sent)),
                    len(tries[n + 1]),
                )
                continue
            if len(sent) < len(terms):
                # Remember a length that worked, so later clips in this session don't pay
                # for a refusal first. It only goes down, and never so low that hints
                # stop altogether.
                worked = max(MIN_KEYTERM_CHARS, sum(map(len, sent)))
                self._max_keyterm_chars[key] = min(
                    worked, self._max_keyterm_chars.get(key, MAX_KEYTERM_CHARS)
                )
            return text
        return None  # pragma: no cover  # the last try is never refused, it raises

    async def _wait_if_busy(self) -> None:
        """Wait out a Retry-After that's still running, or fail at once if it's too long."""
        left = self._busy_until - _clock()
        if left > MAX_RETRY_WAIT_S:
            raise DeepgramError(f"{BUSY} (asked to wait {left:.0f} s more)", for_dm=BUSY)
        if left > 0:
            await _sleep(left)

    async def _request(self, body: bytes, terms: list[str], *, fallback: bool) -> str | None:
        """One request, retried once if Deepgram is busy: after the wait it asks for
        (Retry-After, up to MAX_RETRY_WAIT_S), or RETRY_DELAY_S. With `fallback`, a 400
        raises _KeytermsRefused so the caller can try fewer keyterms."""
        s = self._settings
        headers = {"Authorization": f"Token {s.deepgram_api_key}", "Content-Type": "audio/wav"}
        params = request_params(s, terms)
        for attempt in (1, 2):
            await self._wait_if_busy()
            sent_at = _clock()
            pause = 0.0  # before the second try, when Deepgram gave no Retry-After
            try:
                async with self._get_session().post(
                    s.deepgram_url, params=params, data=body, headers=headers
                ) as resp:
                    if resp.status == 200:
                        self._400_is_settings = False
                        if sent_at >= self._busy_set_at:
                            # Sent after the last "wait": it really is answering again.
                            # (An older request's 200 must not clear a newer window.)
                            self._busy_until = 0.0
                        return transcript(await resp.json(content_type=None))
                    asked = retry_after_s(resp.headers.get("Retry-After"))
                    if asked is not None and resp.status in RETRY_STATUSES:
                        now = _clock()
                        self._busy_until = now + min(asked, MAX_BUSY_WINDOW_S)
                        self._busy_set_at = now
                    if attempt == 1 and resp.status in RETRY_STATUSES:
                        if asked is not None and asked > MAX_RETRY_WAIT_S:
                            # Too long to hold every table up: say so now.
                            raise DeepgramError(f"{BUSY} (HTTP {resp.status})", for_dm=BUSY)
                        # The wait itself happens after the reply is closed: the
                        # Retry-After at the top of the loop, or this pause.
                        pause = RETRY_DELAY_S if asked is None else 0.0
                    else:
                        if resp.status == 400 and fallback:
                            # Most likely too many keyterm tokens, not the host's settings.
                            raise _KeytermsRefused
                        if resp.status == 400 and not terms:
                            self._400_is_settings = True
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
                raise DeepgramError(
                    "Deepgram took too long to answer", for_dm="Deepgram took too long to answer"
                ) from None
            except aiohttp.ClientError as exc:
                # aiohttp's own messages can include the request URL, whose query holds
                # players' names (keyterms): keep only the error's type.
                raise DeepgramError(
                    f"Deepgram request failed ({type(exc).__name__})",
                    for_dm="DMbot's request to Deepgram failed",
                ) from None
            except ValueError:
                raise DeepgramError(
                    "Deepgram sent a reply DMbot couldn't read",
                    for_dm="Deepgram sent a reply DMbot couldn't read",
                ) from None
            if pause:
                await _sleep(pause)
        return None  # pragma: no cover  # loop always returns or raises

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
