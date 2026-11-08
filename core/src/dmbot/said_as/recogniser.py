"""The speech-sound recogniser, run apart from the bot (#2316).

A model that hears a short clip and writes the speech sounds (IPA) it contained: it needs a
machine-learning runtime and about a gigabyte of weights, so it never lives in the bot's own
process or its event loop. `WorkerRecogniser` starts `dmbot.said_as.worker` as a child process on
the first clip, feeds it one clip at a time (a lock), and stops it again after `IDLE_S` without
one, so the memory is only used while someone is saying a name. The child runs at a lower
priority, so a live table's transcription always comes first, and the bot does not start a clip at
all while a table is listening (dm_screen/said_as_listens.py).

Nothing here keeps or logs a clip. A failure kills the child (a fresh one starts next time) and
says only a fixed word.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

IDLE_S = 600  # the model is unloaded after ten minutes without a clip
LOAD_S = 180.0  # the longest the first clip waits for the weights to load
CLIP_S = 60.0  # the longest one clip may take once the model is loaded
NICE = 10  # the child's priority is lowered by this much: live recording first
MAX_REPLY = 4096  # a reply line longer than this is not an answer

VOICE_OFF = "voice_off"  # no model folder, or the model could not be loaded
FAILED = "failed"  # the worker died, took too long or answered with nonsense


class RecogniserError(Exception):
    """The clip was not recognised. `code` is VOICE_OFF or FAILED, safe to show and to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Recogniser(Protocol):
    async def recognise(self, pcm: bytes) -> str:
        """The speech sounds in 16 kHz mono s16le audio, as one string of IPA."""
        ...

    async def unload_if_idle(self) -> None: ...


def frame(pcm: bytes) -> bytes:
    """One request to the worker: the length in four bytes, then the audio."""
    return struct.pack(">I", len(pcm)) + pcm


class WorkerRecogniser:
    def __init__(
        self,
        model_dir: str,
        *,
        python: str = sys.executable,
        module: str = "dmbot.said_as.worker",
        command: Sequence[str] | None = None,
        idle_s: float = IDLE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model_dir
        self._python = python
        self._module = module
        self._command = command  # tests run a stand-in worker; the default is the real one
        self._idle_s = idle_s
        self._now = clock
        self._proc: asyncio.subprocess.Process | None = None
        self._used = 0.0
        self._lock = asyncio.Lock()  # one clip at a time

    @property
    def loaded(self) -> bool:
        return self._proc is not None

    async def recognise(self, pcm: bytes) -> str:
        if not self._model or not await asyncio.to_thread(Path(self._model).is_dir):
            raise RecogniserError(VOICE_OFF)
        async with self._lock:
            try:
                proc = await self._start()
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(frame(pcm))
                await proc.stdin.drain()
                line = await asyncio.wait_for(proc.stdout.readline(), CLIP_S)
                reply = json.loads(line[:MAX_REPLY])
                ipa = reply["ipa"]
                if not isinstance(ipa, str):
                    raise ValueError("ipa")
            except RecogniserError:
                await self._stop()
                raise
            except Exception as exc:  # the type only: a reply could carry the sounds heard
                log.warning("The speech-sound worker failed: %s", type(exc).__name__)
                await self._stop()
                raise RecogniserError(FAILED) from None
            self._used = self._now()
            return ipa

    async def _start(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        self._proc = None
        argv = self._command or (self._python, "-I", "-m", self._module, self._model)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # its messages could quote the audio's sounds
            preexec_fn=lambda: os.nice(NICE),
        )
        assert proc.stdout is not None
        self._proc = proc
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), LOAD_S)
            ready = json.loads(line[:MAX_REPLY])
        except Exception as exc:
            log.warning("The speech-sound model did not load: %s", type(exc).__name__)
            await self._stop()
            raise RecogniserError(VOICE_OFF) from None
        if ready.get("ready") is not True:
            await self._stop()
            raise RecogniserError(VOICE_OFF)
        self._used = self._now()
        return proc

    async def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)

    async def unload_if_idle(self) -> None:
        """Stop the worker, and so free its memory, once it has been idle `idle_s`. Never while a
        clip is being recognised."""
        if self._proc is None or self._lock.locked():
            return
        if self._now() - self._used >= self._idle_s:
            async with self._lock:
                await self._stop()
                log.info("The speech-sound model was unloaded after a quiet spell")

    async def close(self) -> None:
        async with self._lock:
            await self._stop()
