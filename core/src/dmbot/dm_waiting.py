"""What waits for the DM after a lore upload or an import finishes, as one private message (#3314).

The live session's after-session steps hand `Ask`s to the DM's private chat (#3210). A lore upload
and an import have no table at the session, so those steps never ran and the DM only found a waiting
check by opening `/dmbot names`. This builds the same combined message from the campaign instead:
one button for each kind of check that has something waiting, counts only, never a name or a word
of the text. `WaitingSent` makes sure each message goes out once: a key (a lore upload's time, an
import's id) is claimed in the database before it is sent, so a restart or a second round does not
repeat it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import discord

from dmbot.db import Database
from dmbot.dm_questions import CLAIMS, CONNECTIONS, NAMES, PEOPLE, Ask

# (family links worked out from the DM's own wait behind the same 📜 button as the backstory's
# connections, so they are counted with them: two buttons with one id cannot sit in one message)
KINDS = (NAMES, PEOPLE, CLAIMS, CONNECTIONS)


@dataclass(frozen=True, slots=True)
class WaitingChecks:
    """How many checks of each kind wait for the DM now. Zero for a kind that cannot be counted."""

    names: int = 0
    people: int = 0
    claims: int = 0
    connections: int = 0  # backstory connections, suggested names, matches and family links

    @property
    def total(self) -> int:
        return self.names + self.people + self.claims + self.connections

    @staticmethod
    def parse(offered: str) -> WaitingChecks:
        """The counts a sent message offered, from its stored words (unknown words ignored)."""
        found: dict[str, int] = {}
        for word in offered.split():
            kind, _, count = word.partition("=")
            if kind in ("names", "people", "claims", "connections") and count.isdigit():
                found[kind] = int(count)
        return WaitingChecks(**found)

    def minus(self, earlier: WaitingChecks) -> WaitingChecks:
        """What waits beyond what an earlier message already offered (never below zero), so a
        second message does not offer the same checks again."""
        return WaitingChecks(
            names=max(0, self.names - earlier.names),
            people=max(0, self.people - earlier.people),
            claims=max(0, self.claims - earlier.claims),
            connections=max(0, self.connections - earlier.connections),
        )

    def offered(self) -> str:
        return " ".join(
            f"{k}={getattr(self, k)}" for k in ("names", "people", "claims", "connections")
        )


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _line(kind: str, n: int) -> str:
    return {
        NAMES: _plural(n, "name to check", "names to check"),
        PEOPLE: _plural(n, "who-is-who question", "who-is-who questions"),
        CLAIMS: _plural(n, "thing said at the table to check", "things said at the table to check"),
        CONNECTIONS: _plural(n, "connection to check", "connections to check"),
    }[kind]


def _alone(kind: str, n: int) -> str:
    """The words when this is the only kind waiting: what it is, then what the button does. Counts
    only; nothing is added until the DM says yes, and players are not told."""
    head = {
        NAMES: f"📝 **{_line(kind, n)}**.",
        PEOPLE: f"🧑 **{_line(kind, n)}**.",
        CLAIMS: f"🗣️ **{_line(kind, n)}**.",
        CONNECTIONS: f"📜 **{_line(kind, n)}**.",
    }[kind]
    return (
        f"{head}\nPress the button to look at them one by one. Nothing is added until you say yes."
    )


# The two kinds an upload's summary message can answer, with the button each one has elsewhere.
LINKED_BUTTONS = {NAMES: "📝 Check new names", CONNECTIONS: "📜 Check the connections"}


def _alone_linked(kind: str, n: int) -> str:
    """The words when this is the only kind waiting and its button opens the upload's summary."""
    head = _alone(kind, n).split("\n", 1)[0]
    return (
        f"{head}\nOpen the list in the DM Bot channel. Tap **Add all**, or go through them one "
        "at a time. Nothing is added until you say yes."
    )


def asks_for(
    waiting: WaitingChecks,
    buttons: dict[str, Callable[[], discord.ui.Item[Any]]],
    *,
    where: str = "your upload",
    summary_url: str = "",
    summary_names: int = 0,
) -> list[Ask]:
    """One `Ask` for each kind with something waiting (`buttons`: the kind's button maker; a kind
    without one is left out). `where` says what the count is from, in the line for several.
    `summary_url`: the upload's summary message in the DM Bot channel, which holds the Add all
    buttons; the names and connections buttons then open it (a link button, the same label), and
    every other kind keeps its own. The DM screen's fallback keeps today's buttons.
    `summary_names` is how many names that summary counts: names waiting beyond them (from an
    earlier session) get a second, ordinary names question, so none is left without a button."""
    out: list[Ask] = []
    for kind in KINDS:
        n = getattr(waiting, "connections" if kind == CONNECTIONS else kind)
        if n <= 0 or kind not in buttons:
            continue
        screen = _alone(kind, n)  # (the DM screen's words, and today's button, are unchanged)
        text, button = screen, buttons[kind]
        extra = 0
        if summary_url and kind in LINKED_BUTTONS:
            if kind == NAMES and 0 < summary_names < n:
                extra, n = n - summary_names, summary_names
            text = _alone_linked(kind, n)
            button = partial(_link_button, LINKED_BUTTONS[kind], summary_url)
            screen = _alone(kind, n + extra)
        out.append(
            Ask(
                kind,
                text,
                f"{_line(kind, n)} from {where}",
                button,
                buttons[kind],
                screen,
                count=n + extra,
            )
        )
        if extra:
            more = _plural(extra, "more name to check", "more names to check")
            own = f"📝 **{more}**.\n" + _alone(kind, extra).split("\n", 1)[1]
            out.append(Ask(kind, own, f"{more} (from earlier)", buttons[kind], buttons[kind], own))
    return out


def _link_button(label: str, url: str) -> discord.ui.Item[Any]:
    return discord.ui.Button(label=label, url=url)


class WaitingSent:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim(self, guild_id: int, campaign_id: str, key: str, offered: str = "") -> bool:
        """Take this message before sending it. False when it was already taken (sent): the caller
        sends nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO dm_waiting_sent (guild_id, campaign_id, key, sent_at, offered)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, key, int(self._now()), offered),
            )
            return bool(cur.rowcount)

    async def release(self, guild_id: int, campaign_id: str, key: str) -> None:
        """Give the key back (nothing could be sent anywhere), so a later round tries again."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM dm_waiting_sent WHERE guild_id = %s AND campaign_id = %s AND key = %s",
                (guild_id, campaign_id, key),
            )

    async def offered_of(self, guild_id: int, campaign_id: str, key: str) -> str | None:
        """What a sent message offered (its counts as words), or None when it was not sent."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT offered FROM dm_waiting_sent WHERE guild_id = %s AND campaign_id = %s"
                " AND key = %s",
                (guild_id, campaign_id, key),
            )
            row = await cur.fetchone()
            return None if row is None else str(row["offered"])

    async def sent(self, guild_id: int, campaign_id: str, key: str) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM dm_waiting_sent WHERE guild_id = %s AND campaign_id = %s"
                " AND key = %s",
                (guild_id, campaign_id, key),
            )
            return await cur.fetchone() is not None
