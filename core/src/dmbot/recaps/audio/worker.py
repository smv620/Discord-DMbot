"""The recap's audio step (#1383): after the text is ready, make the narration and the music.

It is a second, separate job on the same recap row (`audio`: queued → ready | failed | off), so
the text and its email never wait for it, and a failure here leaves the recap `ready` with its
text. A provider error is retried once later; a refusal that would fail the same way (a bad key,
no credits) is not. With no key set the audio is simply `off` and the recap goes out text-only.

Checks, at the start, before every paid call, and again before anything is kept: the plan
includes spoken recaps (fails closed), everyone whose lines were used is still recorded (consent,
after every async step), no secret name is in any request (the script refuses one, and the names
are read fresh), and the spend is under the cap. The audio belongs to the text it was made from
(`audio_text_hash`): if the DM changes the text meanwhile, nothing is kept. Counts only in the log,
never a name or the text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.recaps.approval import BOTH, text_hash
from dmbot.recaps.approval import MUSIC as MUSIC_CHOICE
from dmbot.recaps.approval import VOICE as VOICE_CHOICE
from dmbot.recaps.audio import names as nm
from dmbot.recaps.audio import parts
from dmbot.recaps.audio import script as sc
from dmbot.recaps.audio.eleven import ElevenLabs, Locator, ProviderError
from dmbot.recaps.audio.files import FILE_NAMES, AudioFiles
from dmbot.recaps.audio.render import (
    Made,
    Renderer,
    RenderError,
    Reuse,
    estimate_credits,
    kinds_for,
)
from dmbot.recaps.store import AUDIO_LEASE_S, AUDIO_MAX_TRIES, Job, RecapStore

log = logging.getLogger(__name__)

RETRY_AFTER_S = 3600  # the one retry waits an hour
DEADLINE_S = AUDIO_LEASE_S - 120  # all the work must end before the lease can lapse
DEFAULT_MAX_CREDITS = 15_000  # about $3 at the Creator plan's price: a recap is about 7,000

NOT_INCLUDED = "Narration and music aren't part of this campaign's plan, so they weren't made."
STOPPED = "A player stopped being recorded, so the audio wasn't made. The text is not changed."
SECRET = "The recap names something you keep secret, so the audio wasn't made."
OVER_CAP = "This recap would cost more audio than DMbot allows for one, so it wasn't made."
BUSY = "The narration and music couldn't be made this time. The text is fine."
REFUSED = "The narration and music service refused the request. The text is fine."
NO_NAMES = "DMbot couldn't check your secret names just now, so the audio waits."
NO_MIXER = "DMbot can't mix audio on this server yet, so the audio wasn't made. The text is fine."
EDITED = "The text changed while the audio was made, so the audio was not kept."


class Off(Exception):
    """The DM switched the audio off: nothing is made, and nothing is said about it."""


class Stop(Exception):
    """Work must stop for a plain reason (one of the sentences above)."""

    def __init__(self, why: str, *, permanent: bool = True) -> None:
        super().__init__(why)
        self.why = why
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class AudioInputs:
    """What the audio reads, loaded fresh for a recap: this campaign's own data only."""

    secret_names: Collection[str]
    names: Sequence[nm.SpokenName]  # confirmed names with their "said as" spellings

    @property
    def allowed_names(self) -> list[str]:
        """The confirmed names that are not secret: the ones a secret name's word may share (the
        recap check's own list (`inputs.py`: confirmed, not secret, not a person), so the two
        secret locks agree; #2050)."""
        return [n.text for n in self.names if n.confirmed and not n.secret and not n.person]


AUDIO_CHOICES = (VOICE_CHOICE, MUSIC_CHOICE, BOTH)  # what makes audio; `text` makes none

Load = Callable[[int, str, Job], Awaitable["AudioInputs | None"]]
Consenting = Callable[[int], Awaitable[Collection[int]]]
Allowed = Callable[[int, str, Job], Awaitable[bool]]
Allowance = Callable[[int, str, Job], Awaitable["str | None"]]  # why not, or None (#1428)
Switched = Callable[[int, str, Job], Awaitable[bool]]  # the DM's own choice (#1421)


def _hash(rules: Sequence[nm.Rule]) -> str:
    body = json.dumps([[r.string_to_replace, r.alias] for r in rules], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


class AudioWorker:
    def __init__(
        self,
        store: RecapStore,
        provider: ElevenLabs | None,
        files: AudioFiles,
        *,
        load: Load,
        consenting: Consenting,
        allowed: Allowed,
        switched: Switched | None = None,
        allowance: Allowance | None = None,
        renderer: Renderer | None = None,
        max_credits: int = DEFAULT_MAX_CREDITS,
        voice_tags: bool = True,
        have_ffmpeg: Callable[[], bool] = _has_ffmpeg,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._voice_tags = voice_tags  # mood cues become delivery tags (#1983)
        self._provider = provider
        self._files = files
        self._load = load
        self._consenting = consenting
        self._allowed = allowed
        self._switched = switched
        self._allowance = allowance
        self._renderer = renderer or (Renderer(provider, files) if provider else None)
        self._max = max_credits
        self._have_ffmpeg = have_ffmpeg
        self._now = clock
        self._one = asyncio.Lock()  # one recap's audio at a time: memory, credits, the counter
        self._start = 0  # the provider's credit count when this job began
        self._choice = ""  # the approved choice read by the last check (#2083)

    async def run(self, gid: int, cid: str, session_at: int) -> Job | None:
        """Make the audio of one recap, if it is waiting and nobody else has it. None if it
        was not this worker's to make."""
        async with self._one:
            if not await self._store.audio_lease(gid, cid, session_at):
                return None
            try:
                job = await self._store.get(gid, cid, session_at)
                if job is not None and job.audio == "queued" and job.state == "ready":
                    await self._guarded(gid, cid, job)
            finally:
                await self._store.release(gid, cid, session_at)
            return await self._store.get(gid, cid, session_at)

    async def _guarded(self, gid: int, cid: str, job: Job) -> None:
        """`_make` with every way out handled: nothing it raises leaves the job queued to run
        again at once, and a hung provider can't hold it past its lease (a second worker would
        then start the same paid work)."""
        try:
            async with asyncio.timeout(DEADLINE_S):
                await self._make(gid, cid, job)
        except Off:  # (what was spent before it stopped is kept: it counts toward the caps)
            await self._store.set_audio(
                gid, cid, job.session_at, audio="off", credits=self._spent(job)
            )
        except Stop as stop:
            await self._finish(gid, cid, job, stop.why, permanent=stop.permanent)
        except ProviderError as exc:
            # The kind, the HTTP status and which call: fixed words and a number, so a live
            # recap's failure can be told apart in the server log (#2042). Never a body.
            log.warning("Recap audio: a provider call failed (%s)", exc.describe())
            await self._finish(
                gid, cid, job, REFUSED if exc.permanent else BUSY, permanent=exc.permanent
            )
        except TimeoutError:
            log.warning("Recap audio: ran out of time")
            await self._finish(gid, cid, job, BUSY, permanent=False)
        except Exception as exc:  # the disk, the network, our own bug: the text is fine
            log.warning("Recap audio: failed (%s)", type(exc).__name__)
            await self._finish(gid, cid, job, BUSY, permanent=False)

    def _spent(self, job: Job) -> int:
        """Credits this recap's audio has cost so far, across tries (the cap is for the recap)."""
        return job.audio_credits + (self._provider.credits - self._start if self._provider else 0)

    async def _finish(self, gid: int, cid: str, job: Job, why: str, *, permanent: bool) -> None:
        """Stopped for a reason: failed for good, or one more try an hour on. `job` was read
        after the lease counted this start, so `audio_tries` already includes it."""
        credits = self._spent(job)
        log.info("Recap audio: stopped (%s)", why[:40])
        if permanent or job.audio_tries >= AUDIO_MAX_TRIES:
            await self._store.set_audio(
                gid, cid, job.session_at, audio="failed", credits=credits, error=why
            )
            return
        await self._store.set_audio(
            gid,
            cid,
            job.session_at,
            audio="queued",
            next_at=int(self._now()) + RETRY_AFTER_S,
            credits=credits,
            error=why,
        )

    async def _check(self, gid: int, cid: str, job: Job) -> AudioInputs:
        """Everything that must hold before a paid call, read now: the plan, consent, the
        secret names, the cap. Raises `Stop` otherwise."""
        # No paid call without the DM's approval of this very text and a choice that makes audio
        # (#2083): read fresh every time, so an edit or a change of mind stops the work.
        fresh = await self._store.get(gid, cid, job.session_at)
        if fresh is None or not fresh.approved or fresh.audio_choice not in AUDIO_CHOICES:
            raise Off
        self._choice = fresh.audio_choice  # what is made is what the DM approved just now
        if self._switched is not None and not await self._switched(gid, cid, job):
            raise Off  # the DM chose no narration and music; the plan gate below still wins
        if not await self._allowed(gid, cid, job):
            raise Stop(NOT_INCLUDED)
        inputs = await self._load(gid, cid, job)
        if inputs is None:
            raise Stop(NO_NAMES, permanent=False)
        if not set(job.speakers) <= set(await self._consenting(gid)):
            raise Stop(STOPPED)
        if self._spent(job) > self._max:
            raise Stop(OVER_CAP)
        return inputs

    async def _make(self, gid: int, cid: str, job: Job) -> None:
        self._start = self._provider.credits if self._provider else 0
        if self._provider is None or self._renderer is None:
            await self._store.set_audio(gid, cid, job.session_at, audio="off")
            return  # no key: text only, nothing fails
        if not self._have_ffmpeg():
            raise Stop(NO_MIXER)  # before a single paid call, not after them
        inputs = await self._check(gid, cid, job)  # the DM's choice and the plan come first
        if self._allowance is not None:
            over = await self._allowance(gid, cid, job)  # can't be read: the generic retry
            if over:
                raise Stop(over)  # the narration is skipped; the text goes out as usual
        try:
            script = sc.build(
                job.title,
                job.game_day,
                job.summary,
                secret_names=inputs.secret_names,
                allowed_names=inputs.allowed_names,
                emotion_tags=self._voice_tags,
            )
        except sc.SecretInScript as exc:
            log.warning("Recap audio: refused, a secret name (rule %s)", exc.rule)  # the rule only
            raise Stop(SECRET) from None
        folder = self._files.recap_dir(gid, cid, job.session_at)
        change = await self._change(folder, job, script)
        reuse = await self._reuse(folder, change)
        if reuse is None and change.kind == parts.VOICE:
            change = parts.Change(parts.ALL, parts.NO_PARTS, total=change.total)  # (files gone)
        # The estimate assumes a new ambience bed: the cap is a ceiling, not a forecast. Remaking
        # only the voice costs the changed paragraphs alone: the music is kept (#2630).
        only = reuse.changed if reuse is not None else None
        if (
            job.audio_credits
            + estimate_credits(script, ambience_needed=True, mode=self._choice, only=only)
            > self._max
        ):
            raise Stop(OVER_CAP)

        async def guard() -> None:
            fresh = await self._check(gid, cid, job)  # again before each paid call
            try:
                sc.build(
                    job.title,
                    job.game_day,
                    job.summary,
                    secret_names=fresh.secret_names,
                    allowed_names=fresh.allowed_names,
                    emotion_tags=self._voice_tags,
                )
            except sc.SecretInScript:
                raise Stop(SECRET) from None

        if change.kind == parts.SAME and not await self._whole(folder):
            change = parts.Change(parts.ALL, parts.NO_PARTS, total=change.total)  # files are gone
        if change.kind == parts.SAME:
            # The words the voice reads are the same (an edit to something the voice never says):
            # nothing is made again, the audio is simply the audio of the new text (#2630).
            kept = await self._store.set_audio(
                gid, cid, job.session_at, audio="ready", credits=self._spent(job), error="",
                text_hash=text_hash(job.title, job.summary),
                unchanged=(job.title, job.summary), how=parts.SAME,
            )  # fmt: skip
            if not kept:
                raise Stop(EDITED, permanent=False)
            return

        async def progress(stage: str, done: int, total: int) -> None:
            await self._store.set_audio_progress(
                gid, cid, job.session_at, stage=stage, done=done, total=total
            )

        temp = folder.with_name(f"{folder.name}.new")
        try:
            dictionary = await self._dictionary(gid, cid, inputs)
            await progress(
                "voice", 0, len(reuse.changed) if reuse is not None else len(script.paragraphs)
            )
            try:
                made = await self._render(
                    gid, cid, script, temp, job, dictionary, guard, reuse, progress
                )
            except RenderError as exc:
                if reuse is None or str(exc) != "parts":
                    raise
                # The saved speech or music is gone or unreadable: start over, whole.
                reuse, change = None, parts.Change(parts.ALL, parts.NO_PARTS, total=change.total)
                made = await self._render(
                    gid, cid, script, temp, job, dictionary, guard, None, progress
                )
            await guard()  # and once more after the last call, before anything is kept
            now_job = await self._store.get(gid, cid, job.session_at)
            if (
                now_job is None
                or now_job.state != "ready"
                or now_job.status not in ("draft", "posting", "posted")
                or (now_job.title, now_job.summary) != (job.title, job.summary)
            ):
                raise Stop(EDITED, permanent=False)  # the recap went, or the DM changed the text
            await asyncio.to_thread(_put_in_place, temp, folder, kinds_for(self._choice))
        finally:
            await asyncio.to_thread(shutil.rmtree, temp, True)
        how = parts.VOICE if reuse is not None else change.kind
        kept = await self._store.set_audio(
            gid,
            cid,
            job.session_at,
            audio="ready",
            credits=self._spent(job),
            error="",
            text_hash=text_hash(job.title, job.summary),
            unchanged=(job.title, job.summary),  # in the same statement: no edit slips between
            how=how,
            why=change.why if how == parts.ALL else "",
        )
        if not kept:
            await asyncio.to_thread(shutil.rmtree, folder, True)
            raise Stop(EDITED, permanent=False)
        log.info("Recap audio: ready, %d credits, %.0f s", made.credits, made.seconds)

    async def _change(self, folder: Path, job: Job, script: sc.Script) -> parts.Change:
        """What this making is, by what the audio already there was made from (#2630): only for
        a voice-over, and only when audio was made before (`audio_text_hash`)."""
        if not job.audio_text_hash:
            return parts.Change(parts.NEW, total=len(script.paragraphs))  # nothing before it
        if self._choice not in (VOICE_CHOICE, BOTH):
            return parts.Change(parts.ALL, parts.NO_PARTS, total=len(script.paragraphs))
        if job.audio_text_hash == text_hash(job.title, job.summary):
            # "Make it again" on the very text the audio was made from: a new take, all of it
            return parts.Change(parts.ALL, "", total=len(script.paragraphs))
        manifest = await asyncio.to_thread(parts.read_manifest, folder)
        return parts.classify(manifest, script, mode=self._choice, seed=job.session_at)

    async def _reuse(self, folder: Path, change: parts.Change) -> Reuse | None:
        """The saved speech and music to build on, when only the voice is remade and every kept
        file is there; else None, and everything is made again."""
        if change.kind != parts.VOICE:
            return None
        manifest = await asyncio.to_thread(parts.read_manifest, folder)
        if manifest is None or not await asyncio.to_thread(
            _parts_there, folder, manifest, self._choice
        ):
            return None
        return Reuse(folder, manifest, frozenset(change.changed))

    async def _whole(self, folder: Path) -> bool:
        """Whether every file the DM's choice keeps is there."""
        return await asyncio.to_thread(
            lambda: all((folder / FILE_NAMES[k]).is_file() for k in kinds_for(self._choice))
        )

    async def _render(
        self,
        gid: int,
        cid: str,
        script: sc.Script,
        into: Path,
        job: Job,
        dictionary: Locator | None,
        guard: Callable[[], Awaitable[None]],
        reuse: Reuse | None,
        progress: Callable[[str, int, int], Awaitable[None]],
    ) -> Made:
        assert self._renderer is not None
        return await self._renderer.render(
            gid, cid, script, into=into, seed=job.session_at, dictionary=dictionary,
            guard=guard, mode=self._choice, reuse=reuse, progress=progress,
        )  # fmt: skip

    async def _dictionary(self, gid: int, cid: str, inputs: AudioInputs) -> Locator | None:
        """The campaign's pronunciation dictionary: made once, made again only when the rules
        change (the older one is deleted at the company). Only non-secret names are in it
        (`names.rules` leaves the rest out). Its name is opaque: no campaign id leaves DMbot."""
        assert self._provider is not None
        rules = nm.rules(inputs.names)
        if not rules:
            return None
        digest = _hash(rules)
        stored = await self._store.dictionary(gid, cid)
        if stored is not None and stored[2] == digest:
            return Locator(stored[0], stored[1])
        opaque = hashlib.sha256(f"{gid}/{cid}".encode()).hexdigest()[:16]
        locator = await self._provider.dictionary(f"dmbot-{opaque}", rules)
        await self._store.save_dictionary(
            gid, cid, locator.dictionary_id, locator.version_id, digest
        )
        if stored is not None:
            await self._provider.forget_dictionary(stored[0])
        return locator


def _parts_there(folder: Path, manifest: dict[str, Any], mode: str) -> bool:
    """Every saved speech, and the music when the choice has music, is on the disk."""
    count = len(manifest["paragraphs"])
    if any(not parts.speech_path(folder, i).is_file() for i in range(count)):
        return False
    return mode != BOTH or (folder / parts.WORK / parts.MUSIC_PIECE).is_file()


def _put_in_place(temp: Path, folder: Path, kinds: Sequence[str]) -> None:
    """The finished files replace the old ones: the old folder is moved aside first, so a reader
    never finds the recap with no files."""
    for kind in kinds:
        if not (temp / FILE_NAMES[kind]).is_file():
            raise RenderError("files")
    aside = folder.with_name(f"{folder.name}.old")
    shutil.rmtree(aside, ignore_errors=True)
    if folder.exists():
        folder.rename(aside)
    temp.rename(folder)
    shutil.rmtree(aside, ignore_errors=True)
