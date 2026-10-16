"""The recap's audio step (#1383): after the text is ready, make the narration and the music.

It is a second, separate job on the same recap row (`audio`: queued → ready | failed | off), so
the text and its email never wait for it, and a failure here leaves the recap `ready` with its
text. A provider error is retried once later; a refusal that would fail the same way (a bad key,
no credits) is not. With no key set the audio is simply `off` and the recap goes out text-only.

Checks, at the start and again before anything is kept: the plan includes spoken recaps (fails
closed), everyone whose lines were used is still recorded (consent, after every async step), no
secret name is in any request (the script refuses one, and the names are read fresh), and the
recap's estimated cost is under the cap. Counts only in the log, never a name or the text.
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

from dmbot.recaps.audio import names as nm
from dmbot.recaps.audio import script as sc
from dmbot.recaps.audio.eleven import ElevenLabs, Locator, ProviderError
from dmbot.recaps.audio.files import FILE_NAMES, MUSIC, VOICE, AudioFiles
from dmbot.recaps.audio.render import Renderer, RenderError, estimate_credits
from dmbot.recaps.store import Job, RecapStore

log = logging.getLogger(__name__)

RETRY_AFTER_S = 3600  # the one retry waits an hour
MAX_TRIES = 2
DEFAULT_MAX_CREDITS = 15_000  # about $3 at the Creator plan's price: a recap is about 7,000

NOT_INCLUDED = "Spoken recaps aren't part of this campaign's plan, so the audio wasn't made."
STOPPED = "A player stopped being recorded, so the audio wasn't made. The text is not changed."
SECRET = "The recap names something you keep secret, so the audio wasn't made."
OVER_CAP = "This recap would cost more audio than DMbot allows for one, so it wasn't made."
BUSY = "The narration and music couldn't be made this time. The text is fine."
REFUSED = "The narration and music service refused the request. The text is fine."
NO_NAMES = "DMbot couldn't check your secret names just now, so the audio waits."


@dataclass(frozen=True, slots=True)
class AudioInputs:
    """What the audio reads, loaded fresh for a recap: this campaign's own data only."""

    secret_names: Collection[str]
    names: Sequence[nm.SpokenName]  # confirmed names with their "said as" spellings


Load = Callable[[int, str, Job], Awaitable["AudioInputs | None"]]
Consenting = Callable[[int], Awaitable[Collection[int]]]
Allowed = Callable[[int, str, Job], Awaitable[bool]]


def _hash(rules: Sequence[nm.Rule]) -> str:
    body = json.dumps([[r.string_to_replace, r.alias] for r in rules], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


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
        renderer: Renderer | None = None,
        max_credits: int = DEFAULT_MAX_CREDITS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._provider = provider
        self._files = files
        self._load = load
        self._consenting = consenting
        self._allowed = allowed
        self._renderer = renderer or (Renderer(provider, files) if provider else None)
        self._max = max_credits
        self._now = clock

    async def run(self, gid: int, cid: str, session_at: int) -> Job | None:
        """Make the audio of one recap, if it is waiting and nobody else has it."""
        if not await self._store.audio_lease(gid, cid, session_at):
            return None
        try:
            job = await self._store.get(gid, cid, session_at)
            if job is not None and job.audio == "queued" and job.state == "ready":
                await self._make(gid, cid, job)
        finally:
            await self._store.release(gid, cid, session_at)
        return await self._store.get(gid, cid, session_at)

    async def _set(self, gid: int, cid: str, job: Job, audio: str, **kw: object) -> None:
        await self._store.set_audio(gid, cid, job.session_at, audio=audio, **kw)  # type: ignore[arg-type]

    async def _give_up(self, gid: int, cid: str, job: Job, why: str) -> None:
        log.info("Recap audio: not made (%s)", why[:40])
        await self._set(gid, cid, job, "failed", error=why)

    async def _make(self, gid: int, cid: str, job: Job) -> None:
        if self._provider is None or self._renderer is None:
            return await self._set(gid, cid, job, "off")  # no key: text only, nothing fails
        if not await self._allowed(gid, cid, job):
            return await self._give_up(gid, cid, job, NOT_INCLUDED)
        inputs = await self._load(gid, cid, job)
        if inputs is None:
            return await self._later(gid, cid, job, NO_NAMES, permanent=False)
        agreed = await self._consenting(gid)
        if not set(job.speakers) <= set(agreed):
            return await self._give_up(gid, cid, job, STOPPED)
        try:
            script = sc.build(
                job.title, job.game_day, job.summary, secret_names=inputs.secret_names
            )
        except sc.SecretInScript:
            return await self._give_up(gid, cid, job, SECRET)
        # The estimate assumes a new ambience bed: the cap is a ceiling, not a forecast.
        if estimate_credits(script, ambience_needed=True) > self._max:
            return await self._give_up(gid, cid, job, OVER_CAP)
        folder = self._files.recap_dir(gid, cid, job.session_at)
        temp = folder.with_name(f"{folder.name}.new")
        try:
            dictionary = await self._dictionary(gid, cid, inputs)
            made = await self._renderer.render(
                gid,
                cid,
                script,
                into=temp,
                seed=job.session_at,
                dictionary=dictionary,
            )
            # Again after the long work: who is recorded now, and the secrets as they are now.
            if not set(job.speakers) <= set(await self._consenting(gid)):
                return await self._give_up(gid, cid, job, STOPPED)
            fresh = await self._load(gid, cid, job)
            if fresh is None:
                return await self._later(gid, cid, job, NO_NAMES, permanent=False)
            try:
                sc.build(job.title, job.game_day, job.summary, secret_names=fresh.secret_names)
            except sc.SecretInScript:
                return await self._give_up(gid, cid, job, SECRET)
            await asyncio.to_thread(_put_in_place, temp, folder)
        except ProviderError as exc:
            return await self._later(
                gid,
                cid,
                job,
                REFUSED if exc.permanent else BUSY,
                permanent=exc.permanent,
            )
        except RenderError:
            return await self._later(gid, cid, job, BUSY, permanent=False)
        except Exception as exc:  # the disk, the network: the text is fine, so only a note
            log.warning("Recap audio: failed (%s)", type(exc).__name__)
            return await self._later(gid, cid, job, BUSY, permanent=False)
        finally:
            await asyncio.to_thread(shutil.rmtree, temp, True)
        await self._set(gid, cid, job, "ready", credits=made.credits, error="")

    async def _dictionary(self, gid: int, cid: str, inputs: AudioInputs) -> Locator | None:
        """The campaign's pronunciation dictionary: made once, made again only when the rules
        change. Only non-secret names are in it (`names.rules` leaves the rest out)."""
        assert self._provider is not None
        rules = nm.rules(inputs.names)
        if not rules:
            return None
        digest = _hash(rules)
        stored = await self._store.dictionary(gid, cid)
        if stored is not None and stored[2] == digest:
            return Locator(stored[0], stored[1])
        locator = await self._provider.dictionary(f"dmbot-{cid}"[:60], rules)
        await self._store.save_dictionary(
            gid, cid, locator.dictionary_id, locator.version_id, digest
        )
        return locator

    async def _later(self, gid: int, cid: str, job: Job, why: str, *, permanent: bool) -> None:
        """Something went wrong: one retry an hour on, unless it would fail the same way."""
        tries = job.audio_tries + 1
        if permanent or tries >= MAX_TRIES:
            return await self._set(gid, cid, job, "failed", tries=tries, error=why)
        await self._set(
            gid,
            cid,
            job,
            "queued",
            tries=tries,
            next_at=int(self._now()) + RETRY_AFTER_S,
            error=why,
        )


def _put_in_place(temp: Path, folder: Path) -> None:
    """The finished files replace the old ones in one step."""
    for kind in (VOICE, MUSIC):
        if not (temp / FILE_NAMES[kind]).is_file():
            raise RenderError("files")
    shutil.rmtree(folder, ignore_errors=True)
    temp.rename(folder)
