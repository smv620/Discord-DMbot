"""Homebrew: a campaign's own spells, monsters, items and rules (docs/PLAN.md, "Homebrew (part 1)";
#1520).

An entry is the DM's own creation in the DM's own words, never a published book's text (the form
says so; a rulebook goes through "Share a rulebook"). It is **DM-screen content by default**
(`players_may_know` false): a homebrew monster is a spoiler, so it never reaches a public answer
or a player's table question until the DM marks it known.

Each campaign keeps its own, never shared between campaigns or servers. Only the campaign's DMs
change anything: every change checks that in the same transaction, so a caller that forgets still
can't write. Every query filters on the server and the campaign and runs inside `Database.guild`,
where row-level security hides every other server's rows too.

Two DMs may look at the same entry. Each entry counts its changes (`version`); an edit or removal
says which version the DM was shown, and if another DM changed it meanwhile nothing is done.

Backups carry them (`HomebrewSection`), validated field by field, as the file is untrusted.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

KINDS = ("spell", "monster", "item", "feat", "condition", "rule", "other")
NAME_MAX = 80
TEXT_MAX = 2000
SUMMARY_MAX = 160
HOMEBREW_MAX = 200  # per campaign, so a campaign (and its backup) stays a sensible size
INT64_MAX = 2**63 - 1

NO_CAMPAIGN = "That campaign isn't here any more."
NOT_DM = (
    "Only this campaign's DMs can change its homebrew. You can still ask about it with "
    "`/dmbot rule`."
)
GONE = "That homebrew entry isn't here any more."
CHANGED = "Another DM changed that entry while you were looking."
SAME_NAME = (
    "This campaign already has homebrew with that name. Pick another name, or edit that one."
)
FULL = (
    f"This campaign already has {HOMEBREW_MAX} homebrew entries, so yours wasn't saved. "
    "Remove one first, then add it again."
)
BAD_KIND = "That isn't one of the kinds. Pick one from the list."
DAMAGED = "This backup file is damaged (bad homebrew entry)."


class HomebrewError(CampaignError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class Homebrew:
    id: int
    campaign_id: str
    kind: str
    name: str
    text: str
    summary: str | None
    players_may_know: bool
    created_by: int
    created_at: int
    updated_at: int
    version: int = 1

    def shown(self) -> str:
        """The one line alerts and lists show: the summary, else the start of the text."""
        if self.summary:
            return self.summary
        flat = " ".join(self.text.split())
        return flat if len(flat) <= SUMMARY_MAX else flat[: SUMMARY_MAX - 1].rstrip() + "…"


def name_key(name: str) -> str:
    """The name for comparing: case-folded, one space between words."""
    return " ".join(name.casefold().split())


def clean_name(raw: str) -> str:
    """The name on one line. Raises HomebrewError if empty or too long."""
    name = " ".join(_plain(raw).split())
    if not name:
        raise HomebrewError("The name box was empty, so nothing was saved.")
    if len(name) > NAME_MAX:
        raise HomebrewError(
            f"That name is too long, so nothing was saved. Keep it under {NAME_MAX} characters."
        )
    return name


def clean_text(raw: str) -> str:
    """The entry's words as plain text: line breaks kept (at most one blank line in a row),
    control and invisible direction characters removed. Raises HomebrewError if empty or too
    long."""
    lines = [" ".join(line.split()) for line in _plain(raw, keep_breaks=True).split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    if not text:
        raise HomebrewError("The text box was empty, so nothing was saved.")
    if len(text) > TEXT_MAX:
        raise HomebrewError(
            f"That's too long, so nothing was saved. Keep the text under {TEXT_MAX} characters."
        )
    return text


def clean_summary(raw: str | None) -> str | None:
    """The one-line summary: None when left empty. Raises HomebrewError if too long."""
    summary = " ".join(_plain(raw or "").split())
    if len(summary) > SUMMARY_MAX:
        raise HomebrewError(
            f"The summary is too long, so nothing was saved. Keep it under {SUMMARY_MAX} "
            "characters."
        )
    return summary or None


def clean_kind(raw: str) -> str:
    kind = raw.strip().lower()
    if kind not in KINDS:
        raise HomebrewError(BAD_KIND)
    return kind


def _plain(raw: str, *, keep_breaks: bool = False) -> str:
    """Without control and invisible direction characters (they can flip how a line reads)."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    keep = {"\n", "\t"} if keep_breaks else {"\t"}
    return "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Cc") or c in keep)


_COLUMNS = (
    "id, campaign_id, kind, name, text, summary, players_may_know, created_by, created_at,"
    " updated_at, version"
)


def _entry(row: dict[str, Any]) -> Homebrew:
    return Homebrew(
        int(row["id"]),
        row["campaign_id"],
        row["kind"],
        row["name"],
        row["text"],
        row["summary"],
        bool(row["players_may_know"]),
        int(row["created_by"]),
        int(row["created_at"]),
        int(row["updated_at"]),
        int(row["version"]),
    )


class HomebrewStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def list(self, guild_id: int, campaign_id: str) -> list[Homebrew]:
        """The campaign's homebrew, by name. Every entry, hidden or not: the callers that
        reach players filter on `players_may_know`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY name_key",
                (guild_id, campaign_id),
            )
            return [_entry(r) for r in await cur.fetchall()]

    async def get(self, guild_id: int, campaign_id: str, entry_id: int) -> Homebrew | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM homebrew_entries WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, entry_id),
            )
            row = await cur.fetchone()
        return None if row is None else _entry(row)

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        *,
        kind: str,
        name: str,
        text: str,
        summary: str | None = None,
        players_may_know: bool = False,
    ) -> Homebrew:
        """Save a new entry. Only the campaign's DMs; at most `HOMEBREW_MAX`; the name is
        unique in the campaign, whatever its case."""
        clean = (clean_kind(kind), clean_name(name), clean_text(text), clean_summary(summary))
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            # Locking the campaign makes two adds at once take turns: the limit and the name hold.
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return await insert_entry(
                conn, guild_id, campaign_id, user_id, *clean, players_may_know, now, now
            )

    async def edit(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        *,
        kind: str,
        name: str,
        text: str,
        summary: str | None = None,
        players_may_know: bool = False,
        unchanged_since: int | None = None,
    ) -> Homebrew:
        """Change an entry. Only the campaign's DMs. `unchanged_since`: the entry's `version` as
        the DM was shown it; if another DM changed it meanwhile nothing is saved (`CHANGED`)."""
        kind_, name_, text_, summary_ = (
            clean_kind(kind),
            clean_name(name),
            clean_text(text),
            clean_summary(summary),
        )
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " AND name_key = %s AND id <> %s",
                (guild_id, campaign_id, name_key(name_), entry_id),
            )
            if await cur.fetchone() is not None:
                raise HomebrewError(SAME_NAME)
            cur = await conn.execute(
                "UPDATE homebrew_entries SET kind = %s, name = %s, name_key = %s, text = %s,"
                " summary = %s, players_may_know = %s, updated_at = %s, version = version + 1"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND (%s::int IS NULL OR version = %s)"
                f" RETURNING {_COLUMNS}",
                (
                    kind_,
                    name_,
                    name_key(name_),
                    text_,
                    summary_,
                    players_may_know,
                    int(self._clock()),
                    guild_id,
                    campaign_id,
                    entry_id,
                    unchanged_since,
                    unchanged_since,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, entry_id)
            return _entry(row)

    async def remove(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        *,
        unchanged_since: int | None = None,
    ) -> Homebrew:
        """Take an entry away; returns it (so it can be put back). Only the campaign's DMs."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND (%s::int IS NULL OR version = %s)"
                f" RETURNING {_COLUMNS}",
                (guild_id, campaign_id, entry_id, unchanged_since, unchanged_since),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, entry_id)
            return _entry(row)

    async def put_back(
        self, guild_id: int, campaign_id: str, user_id: int, entry: Homebrew
    ) -> Homebrew:
        """Undo a removal: the entry as it was (its dates too), as a new row. Only the campaign's
        DMs; refused like an add if the name is taken again or the campaign is full."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return await insert_entry(
                conn,
                guild_id,
                campaign_id,
                entry.created_by,
                entry.kind,
                entry.name,
                entry.text,
                entry.summary,
                entry.players_may_know,
                entry.created_at,
                entry.updated_at,
            )


async def _missing(conn: Conn, guild_id: int, campaign_id: str, entry_id: int) -> HomebrewError:
    """Why a change touched no entry: it's gone, or another DM changed it (it's still here)."""
    cur = await conn.execute(
        "SELECT 1 AS ok FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s AND id = %s",
        (guild_id, campaign_id, entry_id),
    )
    return HomebrewError(GONE if await cur.fetchone() is None else CHANGED)


async def insert_entry(
    conn: Conn,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    kind: str,
    name: str,
    text: str,
    summary: str | None,
    players_may_know: bool,
    created_at: int,
    updated_at: int,
) -> Homebrew:
    """Insert an already-cleaned entry; the caller holds the campaign's lock."""
    cur = await conn.execute(
        "SELECT count(*) AS n FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    if row is not None and int(row["n"]) >= HOMEBREW_MAX:
        raise HomebrewError(FULL)
    cur = await conn.execute(
        "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key, text, summary,"
        " players_may_know, created_by, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (guild_id, campaign_id, name_key) DO NOTHING"
        f" RETURNING {_COLUMNS}",
        (
            guild_id,
            campaign_id,
            kind,
            name,
            name_key(name),
            text,
            summary,
            players_may_know,
            user_id,
            created_at,
            updated_at,
        ),
    )
    saved = await cur.fetchone()
    if saved is None:
        raise HomebrewError(SAME_NAME)
    return _entry(saved)


async def require_dm(
    conn: Conn, guild_id: int, campaign_id: str, user_id: int, *, lock: bool = False
) -> None:
    """The campaign exists in this server, and this person is one of its DMs."""
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaigns WHERE guild_id = %s AND id = %s"
        + (" FOR UPDATE" if lock else ""),
        (guild_id, campaign_id),
    )
    if await cur.fetchone() is None:
        raise HomebrewError(NO_CAMPAIGN)
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
        " AND user_id = %s",
        (guild_id, campaign_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HomebrewError(NOT_DM)


# ---- backups ------------------------------------------------------------------------

_KEYS = frozenset(
    {"kind", "name", "text", "summary", "players_may_know", "by", "created_at", "updated_at"}
)


def _timestamp(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
        return value
    raise CampaignError(DAMAGED)


def _person(value: object) -> int:
    digits = isinstance(value, str) and value.isascii() and value.isdigit()
    if not digits or not 0 < int(str(value)) <= INT64_MAX:
        raise CampaignError(DAMAGED)
    return int(str(value))


@dataclass(frozen=True, slots=True)
class _Checked:
    """One backup row after `check`: every field validated."""

    kind: str
    name: str
    text: str
    summary: str | None
    players_may_know: bool
    created_by: int
    created_at: int
    updated_at: int


class HomebrewSection:
    """Homebrew in campaign backups (an `ExportSection`). The ids mean nothing in another
    campaign, so they are not exported."""

    name = "homebrew"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT kind, name, text, summary, players_may_know, created_by, created_at,"
            " updated_at FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY name_key",
            (guild_id, campaign_id),
        )
        return [
            {
                "kind": r["kind"],
                "name": r["name"],
                "text": r["text"],
                "summary": r["summary"],
                "players_may_know": bool(r["players_may_know"]),
                "by": str(r["created_by"]),
                "created_at": int(r["created_at"]),
                "updated_at": int(r["updated_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        """Every row, validated; nothing is written until all of them are right."""
        if len(rows) > HOMEBREW_MAX:
            raise CampaignError(DAMAGED)
        checked: list[_Checked] = []
        keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != _KEYS:
                raise CampaignError(DAMAGED)
            if not all(isinstance(row[k], str) for k in ("kind", "name", "text")):
                raise CampaignError(DAMAGED)
            if row["summary"] is not None and not isinstance(row["summary"], str):
                raise CampaignError(DAMAGED)
            if not isinstance(row["players_may_know"], bool):
                raise CampaignError(DAMAGED)
            try:
                kind, name = clean_kind(row["kind"]), clean_name(row["name"])
                text, summary = clean_text(row["text"]), clean_summary(row["summary"])
            except HomebrewError:
                raise CampaignError(DAMAGED) from None
            # A file made by DMbot is already in its cleaned form.
            if (kind, name, text, summary) != (
                row["kind"],
                row["name"],
                row["text"],
                row["summary"],
            ):
                raise CampaignError(DAMAGED)
            if name_key(name) in keys:  # two entries can't share a name
                raise CampaignError(DAMAGED)
            keys.add(name_key(name))
            created, updated = _timestamp(row["created_at"]), _timestamp(row["updated_at"])
            if updated < created:
                raise CampaignError(DAMAGED)
            checked.append(
                _Checked(
                    kind,
                    name,
                    text,
                    summary,
                    row["players_may_know"],
                    _person(row["by"]),
                    created,
                    updated,
                )
            )
        return checked

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """`rows` as `check` returned them; a caller with only the file's rows gets them checked
        here, so nothing unchecked is ever written."""
        loaded = rows if self._is_checked(rows) else self.check(rows)
        if loaded:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, summary, players_may_know, created_by, created_at, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            guild_id,
                            campaign_id,
                            r.kind,
                            r.name,
                            name_key(r.name),
                            r.text,
                            r.summary,
                            r.players_may_know,
                            r.created_by,
                            r.created_at,
                            r.updated_at,
                        )
                        for r in loaded
                    ],
                )

    @staticmethod
    def _is_checked(rows: Any) -> bool:
        return isinstance(rows, list) and all(isinstance(r, _Checked) for r in rows) and bool(rows)

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
