"""The must-haves every recap is written to (#3328; docs/RECAP_STANDARD.md).

A campaign has a list of short lines naming what a recap must capture for someone who missed the
session (where it began and ended, travel, each combat, loot, twists, clues, the characters). With
none of its own, a campaign gets the 13 defaults shipped in `must_have_defaults/defaults.txt`, one
`group: line` each, so the wording can change without a migration and a campaign that never edits
always has the current defaults. The DM's list replaces them, and saving an empty list, or one that
is word for word the defaults, brings the defaults back.

The list goes only to the writer's instructions and the coverage check (never the beat sheet or
the fact check). Each line is a thing to include **when the session gave it**: the writer never
invents a direction, a journey time or a feeling the session did not state, and the existing
grounding rules and the fact check always win (docs/RECAP_STANDARD.md).

Per campaign, isolated, deleted with the campaign, and in its backup (CLAUDE.md: campaign
isolation). The limits are mirrored by the website page (`MUSTS_MAX_LINES`, `MUSTS_LINE_CHARS`) and
by migration 0330's door; a test keeps the three in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

MAX_LINES = 40
LINE_CHARS = 300
INT64_MAX = 2**63 - 1
GROUPS = ("framing", "travel", "action", "plot", "character")

DAMAGED = "This backup file is damaged (bad recap must-haves)."
EMPTY = "That line is empty. Type something in it, or remove it."
TOO_LONG = f"That line is too long. Keep it to {LINE_CHARS} letters or fewer."
TOO_MANY = f"You can keep {MAX_LINES} lines. Remove one to add another."


@dataclass(frozen=True, slots=True)
class Default:
    group: str
    text: str


def defaults() -> list[Default]:
    """The shipped defaults, in file order, each with its group heading."""
    raw = resources.files("dmbot.recaps").joinpath("must_have_defaults/defaults.txt")
    out: list[Default] = []
    for row in raw.read_text(encoding="utf-8").splitlines():
        group, _, text = row.partition(":")
        if text.strip():
            out.append(Default(group.strip(), text.strip()))
    return out


def default_texts() -> list[str]:
    return [d.text for d in defaults()]


def clean_line(text: str) -> str:
    """One line as kept: trimmed, refused when empty or over the character limit."""
    kept = text.strip()
    if not kept:
        raise CampaignError(EMPTY)
    if len(kept) > LINE_CHARS:
        raise CampaignError(TOO_LONG)
    return kept


def clean_list(texts: list[str]) -> list[str]:
    """The list as kept. An empty list, or the defaults word for word, is `[]`: no list of its
    own, so the current defaults are used."""
    if len(texts) > MAX_LINES:
        raise CampaignError(TOO_MANY)
    kept = [clean_line(t) for t in texts]
    return [] if kept == default_texts() else kept


class MustStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def own(self, guild_id: int, campaign_id: str) -> list[tuple[int, str]]:
        """The DM's own lines, in order, as (id, text); none means the defaults are in use."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, text FROM recap_must_haves WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY position, id",
                (guild_id, campaign_id),
            )
            return [(int(r["id"]), str(r["text"])) for r in await cur.fetchall()]

    async def lines_for(self, guild_id: int, campaign_id: str) -> list[str]:
        """What the writer reads: the DM's own lines, or the defaults when there are none."""
        own = await self.own(guild_id, campaign_id)
        return [text for _, text in own] if own else default_texts()

    async def replace(
        self, guild_id: int, campaign_id: str, user_id: int, texts: list[str]
    ) -> None:
        """Replace the whole list in one step (the page never sends a half-edited list). Only the
        campaign's DMs. An empty list or the defaults word for word brings the defaults back."""
        from dmbot.rules.house import require_dm  # (kept out of the top: this module is light)

        kept = clean_list(texts)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            await conn.execute(
                "DELETE FROM recap_must_haves WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            now = int(self._clock())
            for n, text in enumerate(kept, start=1):
                await conn.execute(
                    "INSERT INTO recap_must_haves (guild_id, campaign_id, position, text,"
                    " created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (guild_id, campaign_id, n, text, user_id, now),
                )

    async def use_defaults(self, guild_id: int, campaign_id: str, user_id: int) -> None:
        await self.replace(guild_id, campaign_id, user_id, [])


def block(lines: list[str]) -> str:
    """The `<must_haves>` for the writer's instructions; "" when there are none."""
    if not lines:
        return ""
    listed = "\n".join(f"- {_no_brackets(line)}" for line in lines)
    return f"{INTRO}\n<must_haves>\n{listed}\n</must_haves>"


def _no_brackets(text: str) -> str:
    """One line, on one line: no bracket can close the tag and no newline can fake another entry."""
    return " ".join(text.split()).replace("<", "‹").replace(">", "›")


INTRO = """\
This campaign has a list of must-haves: things a recap must capture so that someone who missed \
the session can follow. Include each one WHEN THE SESSION GAVE IT, in the beats, the transcript \
or the confirmed story, and put it where it happened in the story. If the session did not give \
it, leave it out: never invent a direction, how long a journey took, how a character felt, a name \
or a detail to fill a line. The must-haves decide what goes in, not how long the recap is. They \
change none of the numbered rules above; those, and the facts of the beat sheet, always win."""


class MustSection:
    """A campaign's own must-have lines in its backups (an `ExportSection`)."""

    name = "recap_must_haves"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT text, created_by, created_at FROM recap_must_haves"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY position, id",
            (guild_id, campaign_id),
        )
        return [
            {"text": r["text"], "by": str(r["created_by"]), "at": int(r["created_at"])}
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[tuple[str, int, int]]:
        if not isinstance(rows, list) or len(rows) > MAX_LINES:
            raise CampaignError(DAMAGED)
        out: list[tuple[str, int, int]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"text", "by", "at"}:
                raise CampaignError(DAMAGED)
            text, by, at = row["text"], row["by"], row["at"]
            digits = isinstance(by, str) and by.isascii() and by.isdigit()
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.strip()) > LINE_CHARS
                or not digits
                or not 0 < int(str(by)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            out.append((text.strip(), int(str(by)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, tuple) for r in rows)
            else self.check(rows)
        )
        for n, (text, by, at) in enumerate(checked, start=1):
            await conn.execute(
                "INSERT INTO recap_must_haves (guild_id, campaign_id, position, text,"
                " created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, n, text, by, at),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_must_haves WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
