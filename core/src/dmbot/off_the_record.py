"""Off the record (#2466, docs/PLAN.md, "Consent"): a person, or the DM for the whole table, pauses
recording for a while.

A pause means nothing is captured at all: the person goes off ears' allowlist, the same as someone
who never agreed, so no audio is decoded, sent to speech-to-text or stored. That is why a button
is the way in; spotting the words "off the record" in speech would mean the line was already
captured. The pause itself lives in `ConsentStore` (so every consent check sees it, including the
re-checks after each await); this service owns the rest: the countdown that ends it, the new
allowlist for ears, and the plain lines everyone at the table can see, so nobody assumes they are
recorded when they are not, or the other way round.

A pause only exists while DMbot is listening (it ends with the session), it never changes
anyone's consent, and the words are "off the record" and "back on the record" everywhere.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.consent import ConsentStore

log = logging.getLogger(__name__)

# The choices on the pause buttons, in minutes; None is "until I press Resume".
CHOICES: tuple[int | None, ...] = (5, 15, None)

NOT_LISTENING = "DMbot isn't listening in a voice channel right now, so there's nothing to pause."
NOT_RECORDED = "DMbot isn't recording you right now, so there's nothing to pause."
TRY_AGAIN = "That didn't work. Press ⏸️ Pause recording me again."
NOT_TOLD = " (DMbot could not tell the table in its chat.)"


def paused_text(until: int | None) -> str:
    """The private message to someone who just went off the record. It says the yes stands, so
    nobody thinks a pause is a stop."""
    back = (
        f"Back on <t:{until}:R>." if until is not None else "Back on when you press ▶️ Resume now."
    )
    return f"⏸️ You're off the record. DMbot isn't recording you. {back} Your yes stays as it is."


def table_paused_text(until: int | None) -> str:
    back = (
        f"Back on <t:{until}:R>." if until is not None else "Back on when you press ▶️ Resume now."
    )
    return f"⏸️ The table is off the record. Nobody is recorded. {back}"


def resumed_text() -> str:
    return "▶️ Back on the record. DMbot is recording you again."


def said_off(name: str, minutes: int | None) -> str:
    if minutes is None:
        return f"⏸️ {name} is off the record until they resume."
    return f"⏸️ {name} is off the record for {minutes} minutes."


def said_back(name: str) -> str:
    return f"▶️ {name} is back on the record."


TABLE_OFF = "⏸️ The table is off the record."
TABLE_BACK = "▶️ The table is back on the record."


@dataclass(frozen=True, slots=True)
class Paused:
    ok: bool
    until: int | None = None  # when it ends, in Unix seconds; None: until they resume
    why: str = ""  # in plain words, when it did not work
    told: bool = True  # the table was told in the DMbot chat


class OffTheRecord:
    """The pauses of one process. `push` sends ears the allowlist again; `say` posts a line in the
    table's DMbot chat (False when there is no table listening); `name_of` is a person's name for
    that line; `listening` is whether DMbot is listening to a table in that server; `drop` throws
    away the words a person is in the middle of saying when the pause starts."""

    def __init__(
        self,
        consent: ConsentStore,
        *,
        push: Callable[[int], Awaitable[None]],
        say: Callable[[int, str], Awaitable[bool]],
        name_of: Callable[[int, int], str],
        listening: Callable[[int], bool],
        drop: Callable[[int, int | None], None] = lambda guild_id, user_id: None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._consent = consent
        self._push = push
        self._say = say
        self._name_of = name_of
        self._listening = listening
        self._drop = drop  # forget what the person is saying right now (None: everyone)
        self._wall = wall
        self._timers: dict[tuple[int, int], asyncio.Task[None]] = {}

    async def _tell_ears(self, guild_id: int) -> None:
        """Send ears the allowlist again. A failure is logged, never thrown: the pause or the
        resume already holds in core, and ears gets the next list at the next change."""
        try:
            await self._push(guild_id)
        except Exception as exc:
            log.error("Couldn't send ears the new list for a pause (%s)", type(exc).__name__)

    async def _tell_table(self, guild_id: int, text: str) -> bool:
        try:
            return await self._say(guild_id, text)
        except Exception as exc:
            log.error("Couldn't tell the table about a pause (%s)", type(exc).__name__)
            return False

    def _until(self, minutes: int | None) -> int | None:
        return None if minutes is None else int(self._wall() + minutes * 60)

    def _arm(self, key: tuple[int, int], minutes: int | None) -> None:
        old = self._timers.pop(key, None)
        if old is not None:
            old.cancel()
        if minutes is not None:
            self._timers[key] = asyncio.create_task(self._after(key, minutes * 60))

    async def _after(self, key: tuple[int, int], seconds: float) -> None:
        """The countdown. Whatever goes wrong is logged: a timer that dies silently would leave
        ears with a stale list."""
        guild_id, user_id = key
        try:
            await asyncio.sleep(seconds)
            self._timers.pop(key, None)
            if user_id == 0:
                self._consent.resume_table(guild_id)
                await self._back_table(guild_id)
            elif not self._consent.is_paused(guild_id, user_id):
                await self._back_person(guild_id, user_id)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.error("A pause's timer failed (%s)", type(exc).__name__)

    async def _back_person(self, guild_id: int, user_id: int) -> None:
        await self._tell_ears(guild_id)
        if not self._consent.table_paused(guild_id):  # the table is still on its break
            await self._tell_table(guild_id, said_back(self._name_of(guild_id, user_id)))

    async def _back_table(self, guild_id: int) -> None:
        await self._tell_ears(guild_id)
        await self._tell_table(guild_id, TABLE_BACK)

    async def pause(self, guild_id: int, user_id: int, minutes: int | None) -> Paused:
        """Take this person off the record. Only while listening, and only someone who agreed.
        Pausing again replaces the earlier pause (a new length)."""
        if minutes not in CHOICES:
            return Paused(False, why=TRY_AGAIN)
        if not self._listening(guild_id):
            return Paused(False, why=NOT_LISTENING)
        if not self._consent.agreed_now(guild_id, user_id):
            return Paused(False, why=NOT_RECORDED)
        self._consent.pause(
            guild_id, user_id, None if minutes is None else minutes * 60
        )  # (nothing is captured from here on: the allowlist follows at once)
        self._drop(guild_id, user_id)
        self._arm((guild_id, user_id), minutes)
        await self._tell_ears(guild_id)
        told = await self._tell_table(guild_id, said_off(self._name_of(guild_id, user_id), minutes))
        log.info("Off the record: a person paused for %s", minutes)
        return Paused(True, self._until(minutes), told=told)

    async def resume(self, guild_id: int, user_id: int) -> bool:
        """Back on the record now. False if they were not off it."""
        had_timer = self._timers.pop((guild_id, user_id), None)
        if had_timer is not None:
            had_timer.cancel()
        # (a pause whose time just ran out still gets its list sent and its line said)
        if not self._consent.resume(guild_id, user_id) and had_timer is None:
            return False
        await self._back_person(guild_id, user_id)
        return True

    async def pause_table(self, guild_id: int, minutes: int | None) -> Paused:
        """The DM's pause: everyone is off the record. The caller has checked they are a DM."""
        if minutes not in CHOICES:
            return Paused(False, why=TRY_AGAIN)
        if not self._listening(guild_id):
            return Paused(False, why=NOT_LISTENING)
        self._consent.pause_table(guild_id, None if minutes is None else minutes * 60)
        self._drop(guild_id, None)
        self._arm((guild_id, 0), minutes)
        await self._tell_ears(guild_id)
        told = await self._tell_table(guild_id, TABLE_OFF)
        log.info("Off the record: the table paused for %s", minutes)
        return Paused(True, self._until(minutes), told=told)

    async def resume_table(self, guild_id: int) -> bool:
        had_timer = self._timers.pop((guild_id, 0), None)
        if had_timer is not None:
            had_timer.cancel()
        if not self._consent.resume_table(guild_id) and had_timer is None:
            return False
        await self._back_table(guild_id)
        return True

    def end_session(self, guild_id: int) -> None:
        """The listening session is over: every pause ends with it, quietly."""
        self._consent.end_session(guild_id)
        for key in [k for k in self._timers if k[0] == guild_id]:
            self._timers.pop(key).cancel()
