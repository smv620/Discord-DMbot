"""One small client per speech-to-text service, all with the same shape.

Audio is streamed in 20 ms chunks at real-time pace (or all at once for re-listen),
then the service is told the utterance has ended. Timings, the same for every service:
- `connect_s`: opening the connection until it's ready for audio (for Speechmatics,
  that includes loading the custom dictionary);
- `final_s`: from the last audio chunk until the last final text for this utterance
  arrived (0 if it was already final by then);
- `total_s`: from opening the connection until that final text (the re-listen cost).

API keys come from the environment and are never printed or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus

from dmbot.devtools.stt_bakeoff.data import VocabEntry

RATE = 16_000
CHUNK_BYTES = RATE * 2 * 20 // 1000  # 20 ms of 16-bit mono
TIMEOUT_S = 30.0

Word = tuple[str, float | None]


@dataclass(slots=True)
class Result:
    text: str = ""
    words: list[Word] = field(default_factory=list)
    connect_s: float = 0.0
    final_s: float = 0.0
    total_s: float = 0.0
    close_s: float = 0.0  # Speechmatics: last chunk until the session's EndOfTranscript
    audio_s: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        """Keep the first error: later ones are usually just the closed connection."""
        if self.error is None:
            self.error = message

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


class Provider(Protocol):
    name: str

    async def transcribe(
        self, pcm: bytes, vocab: list[VocabEntry], *, realtime: bool = True
    ) -> Result: ...


class MissingKey(RuntimeError):
    pass


def api_key(var: str) -> str:
    key = os.environ.get(var, "").strip()
    if not key:
        raise MissingKey(f"{var} isn't set. Add it to the server's .env (never to the repo).")
    return key


async def stream_audio(
    send: Callable[[bytes], Awaitable[None]],
    pcm: bytes,
    *,
    realtime: bool,
    stop: Callable[[], bool] = lambda: False,
) -> tuple[int, float]:
    """Send `pcm` in 20 ms chunks, stopping early if `stop()` turns true.

    Returns (chunks sent, time the last one went).
    """
    start = time.perf_counter()
    count = 0
    for i in range(0, len(pcm), CHUNK_BYTES):
        if stop():
            break
        if realtime:
            delay = start + count * 0.020 - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        await send(pcm[i : i + CHUNK_BYTES])
        count += 1
    return count, time.perf_counter()


async def _finish(reader: asyncio.Task[None], grace_s: float = 1.0) -> None:
    """Let the reader take in what's already arrived (an Error message explains a
    closed connection better than the failed send does), then stop it."""
    if not reader.done():
        with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(reader), grace_s)
    if not reader.done():
        reader.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await reader


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, InvalidStatus):
        return f"HTTP {exc.response.status_code} when connecting"
    return f"{type(exc).__name__}: {exc}"


# ---- Speechmatics ------------------------------------------------------------------

SPEECHMATICS_URL = "wss://us.rt.speechmatics.com/v2"


def speechmatics_start(
    vocab: list[VocabEntry], operating_point: str, max_delay: float
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "language": "en",
        "operating_point": operating_point,
        "max_delay": max_delay,
        "enable_partials": False,
    }
    if vocab:
        config["additional_vocab"] = [
            {"content": v.content, "sounds_like": list(v.sounds_like)}
            if v.sounds_like
            else {"content": v.content}
            for v in vocab
        ]
    return {
        "message": "StartRecognition",
        "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": RATE},
        "transcription_config": config,
    }


def speechmatics_words(results: list[dict[str, Any]]) -> tuple[str, list[Word]]:
    """Text and (word, confidence) pairs from AddTranscript results."""
    text = ""
    words: list[Word] = []
    for r in results:
        alts = r.get("alternatives") or [{}]
        content = str(alts[0].get("content", ""))
        if not content:
            continue
        if r.get("type") == "punctuation":
            text += content
        else:
            text += (" " if text else "") + content
            conf = alts[0].get("confidence")
            words.append((content, float(conf) if conf is not None else None))
    return text, words


class Speechmatics:
    """Real-time API. After the audio it sends ForceEndOfUtterance (what DMbot would do
    at the end of each piece of speech), then EndOfStream."""

    def __init__(
        self,
        operating_point: str,
        *,
        url: str = SPEECHMATICS_URL,
        max_delay: float = 1.0,
        key: str | None = None,
    ) -> None:
        self.name = f"sm-{operating_point.split('-')[0]}"
        self.operating_point = operating_point
        self.url = url
        self.max_delay = max_delay
        self._key = key

    async def transcribe(
        self, pcm: bytes, vocab: list[VocabEntry], *, realtime: bool = True
    ) -> Result:
        key = self._key or api_key("SPEECHMATICS_API_KEY")
        res = Result(audio_s=len(pcm) / (2 * RATE))
        results: list[dict[str, Any]] = []
        marks: dict[str, float] = {}
        t0 = time.perf_counter()
        t_end = t0
        try:
            async with connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {key}"},
                open_timeout=TIMEOUT_S,
                max_size=None,
            ) as ws:
                start = speechmatics_start(vocab, self.operating_point, self.max_delay)
                await ws.send(json.dumps(start))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT_S))
                    if msg.get("message") == "RecognitionStarted":
                        break
                    if msg.get("message") == "Error":
                        res.fail(f"{msg.get('type')}: {msg.get('reason')}")
                        return res
                res.connect_s = time.perf_counter() - t0

                async def read() -> None:
                    while True:
                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            continue
                        m = json.loads(raw)
                        kind = m.get("message")
                        if kind == "AddTranscript":
                            if m.get("results"):
                                results.extend(m["results"])
                                marks["final"] = time.perf_counter()
                        elif kind == "EndOfTranscript":
                            marks["closed"] = time.perf_counter()
                            return
                        elif kind == "Error":
                            res.fail(f"{m.get('type')}: {m.get('reason')}")
                            return

                reader = asyncio.create_task(read())
                try:
                    count, t_end = await stream_audio(
                        ws.send, pcm, realtime=realtime, stop=reader.done
                    )
                    if not reader.done():
                        await ws.send(json.dumps({"message": "ForceEndOfUtterance"}))
                        await ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": count}))
                        await asyncio.wait_for(asyncio.shield(reader), TIMEOUT_S)
                finally:
                    await _finish(reader)
        except Exception as exc:  # report it and carry on with the next clip
            res.fail(_error_text(exc))
        if "final" in marks:
            res.final_s = max(0.0, marks["final"] - t_end)
            res.total_s = marks["final"] - t0
        if "closed" in marks:
            res.close_s = marks["closed"] - t_end
        res.text, res.words = speechmatics_words(results)
        return res


# ---- Deepgram ----------------------------------------------------------------------

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


def deepgram_url(keyterms: list[str], model: str = "nova-3", base: str = DEEPGRAM_URL) -> str:
    params: list[tuple[str, str]] = [
        ("model", model),
        ("language", "en"),
        ("encoding", "linear16"),
        ("sample_rate", str(RATE)),
        ("channels", "1"),
        ("punctuate", "true"),
        ("smart_format", "true"),
        ("interim_results", "false"),
        ("mip_opt_out", "true"),  # don't keep the test audio for model training
    ]
    params += [("keyterm", k) for k in keyterms]
    return f"{base}?{urlencode(params, quote_via=quote)}"


def deepgram_words(message: dict[str, Any]) -> tuple[str, list[Word]]:
    alts = (message.get("channel") or {}).get("alternatives") or [{}]
    words: list[Word] = []
    for w in alts[0].get("words", []):
        text = str(w.get("punctuated_word") or w.get("word") or "")
        if text:
            conf = w.get("confidence")
            words.append((text, float(conf) if conf is not None else None))
    return str(alts[0].get("transcript", "")), words


class Deepgram:
    """Nova-3 streaming. Deepgram rejects keyterm lists over ~500 tokens, so a long list
    is halved until accepted, but never below `min_terms` (the Scene list): the cut only
    removes made-up distractors, which come last. The number used is noted."""

    def __init__(
        self,
        model: str = "nova-3",
        *,
        key: str | None = None,
        base_url: str = DEEPGRAM_URL,
        min_terms: int = 0,
        finalize_timeout: float = 10.0,
    ) -> None:
        self.name = "dg-" + model.replace("-", "")
        self.model = model
        self.base_url = base_url
        self.min_terms = min_terms
        self.finalize_timeout = finalize_timeout
        self._key = key
        self._max_terms: int | None = None

    async def transcribe(
        self, pcm: bytes, vocab: list[VocabEntry], *, realtime: bool = True
    ) -> Result:
        terms = [v.content for v in vocab]
        if self._max_terms is not None and len(terms) > self._max_terms:
            terms = terms[: max(self._max_terms, self.min_terms)]
        while True:
            res = await self._once(pcm, terms, realtime=realtime)
            too_long = res.error in ("HTTP 400 when connecting", "HTTP 414 when connecting")
            if too_long and len(terms) > max(1, self.min_terms):
                terms = terms[: max(len(terms) // 2, self.min_terms)]
                self._max_terms = len(terms)
                continue
            if vocab:
                res.notes.append(f"keyterms {len(terms)}/{len(vocab)}")
            return res

    async def _once(self, pcm: bytes, terms: list[str], *, realtime: bool) -> Result:
        key = self._key or api_key("DEEPGRAM_API_KEY")
        res = Result(audio_s=len(pcm) / (2 * RATE))
        texts: list[str] = []
        marks: dict[str, float] = {}
        t0 = time.perf_counter()
        t_end = t0
        try:
            async with connect(
                deepgram_url(terms, self.model, self.base_url),
                additional_headers={"Authorization": f"Token {key}"},
                open_timeout=TIMEOUT_S,
                max_size=None,
            ) as ws:
                res.connect_s = time.perf_counter() - t0
                t_end = await self._run(ws, pcm, realtime, texts, res, marks)
        except Exception as exc:
            res.fail(_error_text(exc))
        final = marks.get("finalized", marks.get("last_final"))
        if final is not None:
            res.final_s = max(0.0, final - t_end)
            res.total_s = final - t0
        res.text = " ".join(t for t in texts if t)
        return res

    async def _run(
        self,
        ws: ClientConnection,
        pcm: bytes,
        realtime: bool,
        texts: list[str],
        res: Result,
        marks: dict[str, float],
    ) -> float:
        finalized = asyncio.Event()

        async def read() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                m = json.loads(raw)
                if m.get("type") == "Results" and m.get("is_final"):
                    text, words = deepgram_words(m)
                    texts.append(text)
                    res.words.extend(words)
                    if text:
                        marks["last_final"] = time.perf_counter()
                    if m.get("from_finalize"):
                        marks["finalized"] = time.perf_counter()
                        finalized.set()
                elif m.get("type") == "Error" or "err_code" in m:
                    res.fail(str(m.get("description") or m.get("err_msg") or "error message"))

        reader = asyncio.create_task(read())
        t_end = time.perf_counter()
        try:
            _count, t_end = await stream_audio(ws.send, pcm, realtime=realtime, stop=reader.done)
            if not reader.done():
                await ws.send(json.dumps({"type": "Finalize"}))
                try:
                    await asyncio.wait_for(finalized.wait(), self.finalize_timeout)
                except TimeoutError:
                    # Nothing was left to flush: the last final result is the answer.
                    res.notes.append("no from_finalize reply")
                await ws.send(json.dumps({"type": "CloseStream"}))
                await asyncio.wait_for(asyncio.shield(reader), TIMEOUT_S)
        finally:
            await _finish(reader)
        return t_end


# ---- local Whisper (baseline) ------------------------------------------------------


class Whisper:
    """DMbot's own local Whisper transcriber, with the names as its prompt. It gets the
    whole clip after the speech ends, as DMbot does, so `final_s` is its processing time."""

    def __init__(self) -> None:
        from dmbot.transcription.config import load_transcription_settings
        from dmbot.transcription.whisper_local import LocalWhisperTranscriber

        self._t = LocalWhisperTranscriber(load_transcription_settings(os.environ))
        self.name = "whisper"

    async def transcribe(
        self, pcm: bytes, vocab: list[VocabEntry], *, realtime: bool = True
    ) -> Result:
        from dmbot.audio.segmenter import Utterance

        await self._t.warm_up()
        res = Result(audio_s=len(pcm) / (2 * RATE))
        t0 = time.perf_counter()
        try:
            text = await self._t.transcribe(
                Utterance(0, 0, 0, int(res.audio_s * 1000), pcm), [v.content for v in vocab]
            )
        except Exception as exc:
            res.fail(_error_text(exc))
            return res
        res.final_s = res.total_s = time.perf_counter() - t0
        res.text = text or ""
        res.words = [(w, None) for w in res.text.split()]
        return res
