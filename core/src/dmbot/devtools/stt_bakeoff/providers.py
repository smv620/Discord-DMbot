"""One small client per speech-to-text service, all with the same shape.

Audio is streamed in 20 ms chunks at real-time pace (or as fast as possible for
re-listen), and the service is told when the utterance ends. Timings:
- `connect_s`: from opening the connection until the service is ready for audio;
- `final_s`: from the last audio chunk until the final text has arrived.

API keys come from the environment and are never printed or logged.
"""

from __future__ import annotations

import asyncio
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
    audio_s: float = 0.0
    error: str | None = None
    note: str = ""


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
    send: Callable[[bytes], Awaitable[None]], pcm: bytes, *, realtime: bool
) -> tuple[int, float]:
    """Send `pcm` in 20 ms chunks. Returns (chunks sent, time the last one went)."""
    start = time.perf_counter()
    count = 0
    for i in range(0, len(pcm), CHUNK_BYTES):
        if realtime:
            due = start + count * 0.020
            delay = due - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        await send(pcm[i : i + CHUNK_BYTES])
        count += 1
    return count, time.perf_counter()


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
        t0 = time.perf_counter()
        try:
            async with connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {key}"},
                open_timeout=TIMEOUT_S,
                max_size=None,
            ) as ws:
                await ws.send(
                    json.dumps(speechmatics_start(vocab, self.operating_point, self.max_delay))
                )
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT_S))
                    if msg.get("message") == "RecognitionStarted":
                        break
                    if msg.get("message") == "Error":
                        res.error = f"{msg.get('type')}: {msg.get('reason')}"
                        return res
                res.connect_s = time.perf_counter() - t0
                done: dict[str, float] = {}

                async def read() -> None:
                    while True:
                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            continue
                        m = json.loads(raw)
                        kind = m.get("message")
                        if kind == "AddTranscript":
                            results.extend(m.get("results", []))
                        elif kind == "EndOfTranscript":
                            done["t"] = time.perf_counter()
                            return
                        elif kind == "Error":
                            res.error = f"{m.get('type')}: {m.get('reason')}"
                            return

                reader = asyncio.create_task(read())
                count, t_end = await stream_audio(ws.send, pcm, realtime=realtime)
                await ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": count}))
                await asyncio.wait_for(reader, TIMEOUT_S)
                if "t" in done:
                    res.final_s = done["t"] - t_end
        except InvalidStatus as exc:
            res.error = f"HTTP {exc.response.status_code} when connecting"
        except (TimeoutError, OSError) as exc:
            res.error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # report and keep going with the next clip
            res.error = f"{type(exc).__name__}: {exc}"
        res.text, res.words = speechmatics_words(results)
        return res


# ---- Deepgram ----------------------------------------------------------------------

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


def deepgram_url(keyterms: list[str], model: str = "nova-3") -> str:
    params: list[tuple[str, str]] = [
        ("model", model),
        ("language", "en"),
        ("encoding", "linear16"),
        ("sample_rate", str(RATE)),
        ("channels", "1"),
        ("punctuate", "true"),
        ("smart_format", "true"),
        ("interim_results", "false"),
    ]
    params += [("keyterm", k) for k in keyterms]
    return f"{DEEPGRAM_URL}?{urlencode(params, quote_via=quote)}"


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
    """Nova-3 streaming. Keyterms over Deepgram's limit (~500 tokens) are rejected, so
    the list is halved until it's accepted, and the number used is recorded."""

    def __init__(self, model: str = "nova-3", *, key: str | None = None) -> None:
        self.name = "dg-" + model.replace("-", "")
        self.model = model
        self._key = key
        self._max_terms: int | None = None

    async def transcribe(
        self, pcm: bytes, vocab: list[VocabEntry], *, realtime: bool = True
    ) -> Result:
        terms = [v.content for v in vocab]
        if self._max_terms is not None:
            terms = terms[: self._max_terms]
        while True:
            res = await self._once(pcm, terms, realtime=realtime)
            rejected = res.error is not None and res.error.startswith("HTTP 400")
            if rejected and len(terms) > 1:
                terms = terms[: len(terms) // 2]
                self._max_terms = len(terms)
                continue
            if vocab:
                res.note = f"keyterms {len(terms)}/{len(vocab)}"
            return res

    async def _once(self, pcm: bytes, terms: list[str], *, realtime: bool) -> Result:
        key = self._key or api_key("DEEPGRAM_API_KEY")
        res = Result(audio_s=len(pcm) / (2 * RATE))
        texts: list[str] = []
        t0 = time.perf_counter()
        try:
            async with connect(
                deepgram_url(terms, self.model),
                additional_headers={"Authorization": f"Token {key}"},
                open_timeout=TIMEOUT_S,
                max_size=None,
            ) as ws:
                res.connect_s = time.perf_counter() - t0
                marks: dict[str, float] = {}
                res.final_s = await self._run(ws, pcm, realtime, texts, res, marks)
        except InvalidStatus as exc:
            res.error = f"HTTP {exc.response.status_code} when connecting"
        except (TimeoutError, OSError) as exc:
            res.error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            res.error = f"{type(exc).__name__}: {exc}"
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
                    if m.get("from_finalize"):
                        marks["final"] = time.perf_counter()
                        finalized.set()
                elif m.get("type") == "Error" or "err_code" in m:
                    res.error = str(m.get("description") or m.get("err_msg") or m)
            marks["closed"] = time.perf_counter()

        reader = asyncio.create_task(read())
        _count, t_end = await stream_audio(ws.send, pcm, realtime=realtime)
        await ws.send(json.dumps({"type": "Finalize"}))
        try:
            await asyncio.wait_for(finalized.wait(), 10)
        except TimeoutError:
            res.note = "no from_finalize reply"
        await ws.send(json.dumps({"type": "CloseStream"}))
        await asyncio.wait_for(reader, TIMEOUT_S)
        end = marks.get("final", marks.get("closed", time.perf_counter()))
        return end - t_end


# ---- local Whisper (baseline) ------------------------------------------------------


class Whisper:
    """DMbot's own local Whisper transcriber, with the names as its prompt."""

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
            res.error = f"{type(exc).__name__}: {exc}"
            return res
        res.final_s = time.perf_counter() - t0
        res.text = text or ""
        res.words = [(w, None) for w in res.text.split()]
        return res
