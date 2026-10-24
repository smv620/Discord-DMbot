"""Making the two audio tracks of one recap (#1383): the narrator reads it, the music follows the
moods, an ambience bed sits under both, and ffmpeg mixes them. The plans come from the pure
modules (`script`, `music`, `mix`); this module makes the calls and joins the results.

Order of the work: one voice call for each paragraph (with the neighbours' words, so the delivery
carries across them), whose timestamps say when each mood begins; then one music call whose
sections are as long as the voice spends in each mood; then the ambience loop (cached for the
campaign and scene kind, because it never changes); then the mix. The voice track has two
seconds of room before the first word and four after the last, so the music has a lead-in and a
tail, and every file is exactly as long as the voice track and starts at 0, so they play in sync
and each stands alone.

Nothing is written outside the folder it is given; the caller moves it into place once it has
re-checked consent. Counts only in the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import shutil
import wave
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.recaps import approval
from dmbot.recaps.audio import mix, music, parts
from dmbot.recaps.audio.eleven import SPEECH_RATE, ElevenLabs, Locator, ProviderError
from dmbot.recaps.audio.files import FILE_NAMES, MIX, MUSIC, VOICE, AudioFiles
from dmbot.recaps.audio.script import EMOTION, Script

log = logging.getLogger(__name__)

HEAD_SECONDS = 2.0  # music before the first word
TAIL_SECONDS = 1.5  # the music runs this long past the last spoken word (owner, 2026-10-24, #2629)
GAP_SECONDS = 0.4  # breath between paragraphs
# The silence before the closing line (owner, 2026-10-24, #2627): a dramatic pause, with the
# music playing on under it.
CLOSING_GAP_SECONDS = 1.5
FFMPEG_SECONDS = 300

Runner = Callable[[list[str]], Awaitable[int]]
Guard = Callable[[], Awaitable[None]]  # raises to stop the work (consent, secrets, the cap)
# Where the work has got to: ("voice", parts made, parts to make), then ("music", 0, 1) and
# ("mix", 0, 1). Counts only (#2630: every wait shows that DMbot is still working).
Progress = Callable[[str, int, int], Awaitable[None]]


async def no_progress(stage: str, done: int, total: int) -> None:
    return None


@dataclass(frozen=True, slots=True)
class Reuse:
    """Remake only the voice (#2630): `folder` is the recap's finished audio folder, whose kept
    speech, music and `manifest` are reused for everything but the paragraphs in `changed`."""

    folder: Path
    manifest: dict[str, Any]
    changed: frozenset[int]


async def no_guard() -> None:
    return None


class RenderError(Exception):
    """The mix failed. The kind is a fixed word."""


async def run_ffmpeg(argv: list[str]) -> int:
    """Run the mix. Output is thrown away (it can echo file names); only the exit code is used."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        return await asyncio.wait_for(proc.wait(), FFMPEG_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1


@dataclass(frozen=True, slots=True)
class Made:
    folder: Path  # holds the files named in `files`
    files: tuple[str, ...]  # the kinds that were made
    seconds: float  # the length of every file
    credits: int  # what the provider's price list says this cost


def kinds_for(mode: str) -> tuple[str, ...]:
    """The files kept for a choice (#2083): the voice-over alone is its voice and its mix, the
    background music alone is the bed, and both keep all three."""
    return {
        approval.VOICE: (VOICE, MIX),
        approval.MUSIC: (MUSIC,),
        approval.BOTH: (VOICE, MUSIC, MIX),
    }[mode]


def estimate_credits(
    script: Script,
    *,
    ambience_needed: bool,
    mode: str = approval.BOTH,
    only: Collection[int] | None = None,
) -> int:
    """What making this recap will cost in provider credits, before any call (for the cap).
    `only`: the paragraphs spoken again when just the voice is remade (#2630); the music is kept,
    so it costs nothing then."""
    seconds = music.total_seconds(len(script.spoken)) + HEAD_SECONDS + TAIL_SECONDS
    speech = (
        sum(len(p.tagged) for i, p in enumerate(script.paragraphs) if only is None or i in only)
        if mode != approval.MUSIC
        else 0
    )
    if only is not None:
        return speech
    bed = math.ceil(seconds / 60) * 900 if mode != approval.VOICE else 0
    return speech + bed + (200 if ambience_needed and mode != approval.VOICE else 0)


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * round(seconds * SPEECH_RATE)


def _write_wav(path: Path, pcm: bytes | bytearray) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SPEECH_RATE)
        out.writeframes(pcm)


def _fresh(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)


def _keep(path: Path, data: bytes) -> None:
    """A cached loop, whole or not there: never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_bytes(data)
    os.replace(part, path)


def _keep_parts(
    folder: Path,
    script: Script,
    mode: str,
    seed: int,
    spoken: list[bytes],
    seconds: list[float],
    cue_at: list[list[float]],
    piece: Path | None,
) -> None:
    """Keep each paragraph's speech, the music as made, and the manifest beside the audio, so a
    small change to the words can remake only the voice (#2630)."""
    for number, voice in enumerate(spoken):
        parts.save_speech(folder, number, voice)
    if piece is not None:
        shutil.copyfile(piece, folder / parts.WORK / parts.MUSIC_PIECE)
    parts.save_manifest(
        folder, parts.manifest_of(script, mode=mode, seed=seed, seconds=seconds, cue_at=cue_at)
    )


def _tidy(folder: Path) -> None:
    for name in ("in-voice.wav", "in-music.mp3"):
        with contextlib.suppress(OSError):
            (folder / name).unlink()


def _made(folder: Path, kinds: tuple[str, ...] = (VOICE, MUSIC, MIX)) -> tuple[str, ...]:
    """The kept files that exist. A choice keeps only its own files."""
    for kind in (VOICE, MUSIC, MIX):
        if kind not in kinds:
            with contextlib.suppress(OSError):
                (folder / FILE_NAMES[kind]).unlink()
    return tuple(k for k in kinds if (folder / FILE_NAMES[k]).is_file())


class Renderer:
    def __init__(
        self, provider: ElevenLabs, files: AudioFiles, *, runner: Runner = run_ffmpeg
    ) -> None:
        self._provider = provider
        self._files = files
        self._run = runner

    async def render(
        self,
        guild_id: int,
        campaign_id: str,
        script: Script,
        *,
        into: Path,
        seed: int,
        dictionary: Locator | None,
        guard: Guard = no_guard,
        mode: str = approval.BOTH,
        reuse: Reuse | None = None,
        progress: Progress = no_progress,
    ) -> Made:
        """`mode` (#2083): the voice-over alone, the background music alone, or both. The music
        alone is as long as the voice-over would have been (the same estimate), in the same mood
        sections as the text's cues share it. `reuse` (#2630): remake only the voice. The
        paragraphs not in `reuse.changed` keep their saved speech and the saved music is kept,
        so only the changed paragraphs are spoken, nothing is composed, and the mix is made again
        over the new length."""
        await asyncio.to_thread(_fresh, into)  # nothing left by a stopped earlier try
        before = self._provider.credits
        if mode not in (approval.VOICE, approval.MUSIC, approval.BOTH):
            raise ValueError(mode)  # a choice that makes no audio never reaches a paid call
        speaking = mode != approval.MUSIC
        scoring = mode != approval.VOICE
        # 1. The voice, a paragraph at a time; the moods' times come from its timestamps.
        pcm = bytearray(_silence(HEAD_SECONDS))
        starts: dict[int, float] = {}
        closing = 0.0  # how long the last paragraph (the closing line) takes to say
        spoken_pcm: list[bytes] = []  # each paragraph's speech, kept for a later small change
        seconds_of: list[float] = []
        cue_at: list[list[float]] = []  # when each cue's word begins in its paragraph, -1 if none
        to_make = len(reuse.changed) if reuse else len(script.paragraphs)
        made_now = 0
        for number, paragraph in enumerate(script.paragraphs if speaking else ()):
            if number:
                # a breath between paragraphs, none after the last; a longer pause before the end
                pcm += _silence(CLOSING_GAP_SECONDS if paragraph.closing else GAP_SECONDS)
            begun = len(pcm) / (2 * SPEECH_RATE)
            at_in: list[float] = []
            if reuse is not None and number not in reuse.changed:
                saved = await asyncio.to_thread(parts.load_speech, reuse.folder, number)
                if saved is None:
                    raise RenderError("parts")  # the saved speech is gone: the caller starts over
                voice = saved
                at_in = [float(x) for x in reuse.manifest["paragraphs"][number]["cueAt"]]
                for cue, at in zip(paragraph.cues, at_in, strict=False):
                    if cue.kind == EMOTION and at >= 0:
                        starts.setdefault(cue.offset, begun + at)
            else:
                await guard()  # before each paid call: consent, secrets and the cap, again
                speech = await self._provider.speech(
                    paragraph.tagged,
                    previous_text=paragraph.previous_text,
                    next_text=paragraph.next_text,
                    seed=seed,
                    dictionary=dictionary,
                )
                for cue in paragraph.cues:
                    at = -1.0
                    if cue.kind == EMOTION and speech.starts:
                        at = speech.starts[min(cue.tagged_offset, len(speech.starts) - 1)]
                        starts.setdefault(cue.offset, begun + at)
                    at_in.append(at)
                voice = speech.pcm
                made_now += 1
                await progress("voice", made_now, to_make)
            pcm += voice
            spoken_pcm.append(bytes(voice))
            seconds_of.append(len(voice) / (2 * SPEECH_RATE))
            cue_at.append(at_in)
            closing = len(voice) / (2 * SPEECH_RATE)
        if not speaking:
            # No voice was asked for: the music is as long as the voice would have been, from the
            # same estimate the cap uses, and the mix is built over that much silence.
            estimate = music.total_seconds(len(script.spoken)) + HEAD_SECONDS
            pcm = bytearray(_silence(estimate))
        pcm += _silence(TAIL_SECONDS)
        seconds = len(pcm) / (2 * SPEECH_RATE)
        voice_wav = into / "in-voice.wav"
        await asyncio.to_thread(_write_wav, voice_wav, pcm)
        # 2. The music: sections as long as the voice spends in each mood (or, with no voice,
        # as long as each mood's share of the words).
        music_mp3 = into / "in-music.mp3"
        if scoring and reuse is not None:
            piece_path = reuse.folder / parts.WORK / parts.MUSIC_PIECE
            if not await asyncio.to_thread(piece_path.is_file):
                raise RenderError("parts")
            await asyncio.to_thread(shutil.copyfile, piece_path, music_mp3)  # the music is kept
        elif scoring:
            await progress("music", 0, 1)
            plan = music.composition_request(
                script,
                seconds=math.ceil(seconds),
                starts=starts if speaking else None,
                closing_seconds=closing or None,
            )
            total_ms = sum(s["duration_ms"] for s in plan["composition_plan"]["sections"])
            await guard()
            piece = await self._provider.music(plan, seconds=total_ms / 1000)
            await asyncio.to_thread(music_mp3.write_bytes, piece)
        # 3. One ambience loop (the first scene found), kept for this campaign and scene kind.
        bed: Path | None = None
        beds = music.ambience_requests(script) if scoring else []
        if beds:
            bed = self._files.ambience_path(guild_id, campaign_id, beds[0].kind)
            if not await asyncio.to_thread(bed.is_file):
                await guard()
                data = await self._provider.ambience(beds[0].as_request())
                await asyncio.to_thread(_keep, bed, data)
        # 4. The mix.
        await progress("mix", 0, 1)
        mixing = mix.plan(
            seconds,
            with_music=scoring,
            with_ambience=bed is not None,
            ending=music.ending_of(script, seconds=math.ceil(seconds)),
        )
        argv = mix.command(
            mixing,
            voice=str(voice_wav),
            music=str(music_mp3) if scoring else None,
            ambience=None if bed is None else str(bed),
            out_voice=str(into / FILE_NAMES[VOICE]),
            out_bed=str(into / FILE_NAMES[MUSIC]),
            out_mix=str(into / FILE_NAMES[MIX]),
        )
        await guard()
        if await self._run(argv) != 0:
            raise RenderError("mix")
        if speaking:  # what a small change later reuses (#2630)
            await asyncio.to_thread(
                _keep_parts, into, script, mode, seed, spoken_pcm, seconds_of, cue_at,
                music_mp3 if scoring else None,
            )  # fmt: skip
        await asyncio.to_thread(_tidy, into)
        kept = kinds_for(mode)
        made = await asyncio.to_thread(_made, into, kept)
        if len(made) != len(kept):
            raise RenderError("mix")
        credits = self._provider.credits - before
        log.info("Recap audio made: %d paragraph(s), %d credits", len(script.paragraphs), credits)
        return Made(into, made, seconds, credits)


__all__ = [
    "Made",
    "Progress",
    "ProviderError",
    "RenderError",
    "Renderer",
    "Reuse",
    "estimate_credits",
    "kinds_for",
    "run_ffmpeg",
]
