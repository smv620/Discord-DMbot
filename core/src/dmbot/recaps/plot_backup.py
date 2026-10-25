"""PlotBot's issues and the DM's "Not a problem" notes in a campaign's backup (#2122).

A backup holds the whole campaign, so what the DM answered must not be lost with it: the issues
(with their answers) and the points the DM said were fine, so the same point is not raised again
after a restore. They are DM-only words, like the rest of a backup's secrets (CLAUDE.md, "Backups
are complete"). The check records (`plot_checks`) are left out on purpose: they only say that a
text was checked and when, and a restored campaign is checked afresh, so nothing is lost. Every row
is checked before it is loaded, as for every section, and ids from another server are never used.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

MAX_ROWS = 5000
INT64_MAX = 2**63 - 1
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)
ANSWERS = ("fix", "changed", "fine", "leave")
# The fixed words the table allows (migration 0210); the checker uses these same lists.
KINDS = (
    "contradiction", "dead_acts", "item_twice", "place", "timeline", "dropped_thread", "promise",
    "secret",
)  # fmt: skip
LEVELS = ("possible", "probable", "likely")
_HEX = re.compile(r"[0-9a-f]+")
_WORD = re.compile(r"[0-9A-Za-z_-]{1,64}")  # an id or a point's key: no spaces, no marks


def _int(value: Any, low: int = 0) -> bool:
    return type(value) is int and low <= value <= INT64_MAX


def _text(value: Any, limit: int, *, empty: bool = True) -> bool:
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= limit


def _user(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and 0 < int(value) <= INT64_MAX
    )


def _word(value: Any) -> bool:
    return isinstance(value, str) and bool(_WORD.fullmatch(value))


def _key(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 64 and bool(_HEX.fullmatch(value))


class PlotIssuesSection:
    """The issues PlotBot found, with the DM's answers (an `ExportSection`)."""

    name = "plot_issues"
    _KEYS = frozenset(
        {
            "id", "session_at", "text_hash", "kind", "level", "sentence", "sentence_text",
            "reason", "source_text", "source_session", "fine_key", "created_at", "answer_kind",
            "answer_note", "answered_by", "answered_at",
        }
    )  # fmt: skip

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, session_at, text_hash, kind, level, sentence, sentence_text, reason,"
            " source_text, source_session, fine_key, created_at, answer_kind, answer_note,"
            " answered_by, answered_at FROM plot_issues WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY session_at, sentence, id",
            (guild_id, campaign_id),
        )
        out: list[Any] = []
        for r in await cur.fetchall():
            row = dict(r)
            if row["answered_by"] is not None:
                row["answered_by"] = str(row["answered_by"])
            out.append(row)
        return out

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise CampaignError(DAMAGED)
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != self._KEYS:
                raise CampaignError(DAMAGED)
            answered = row["answer_kind"] is not None
            ok = (
                _key(row["id"])
                and row["id"] not in seen
                and _int(row["session_at"], 1)
                and _key(row["text_hash"])
                and row["kind"] in KINDS
                and row["level"] in LEVELS
                and _int(row["sentence"], 1)
                and _text(row["sentence_text"], 2000, empty=False)
                and _text(row["reason"], 600, empty=False)
                and _text(row["source_text"], 400, empty=False)
                and (row["source_session"] is None or _text(row["source_session"], 200))
                and _word(row["fine_key"])
                and _int(row["created_at"])
                and (row["answer_kind"] in ANSWERS if answered else True)
                and _text(row["answer_note"], 500)
                and (
                    (_user(row["answered_by"]) and _int(row["answered_at"]))
                    if answered
                    else (row["answered_by"] is None and row["answered_at"] is None)
                )
            )
            if not ok:
                raise CampaignError(DAMAGED)
            seen.add(row["id"])
        return list(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for r in self.check(rows or []):
            await conn.execute(
                "INSERT INTO plot_issues (guild_id, campaign_id, id, session_at, text_hash, kind,"
                " level, sentence, sentence_text, reason, source_text, source_session, fine_key,"
                " created_at, answer_kind, answer_note, answered_by, answered_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    guild_id, campaign_id, r["id"], r["session_at"], r["text_hash"], r["kind"],
                    r["level"], r["sentence"], r["sentence_text"], r["reason"], r["source_text"],
                    r["source_session"], r["fine_key"], r["created_at"], r["answer_kind"],
                    r["answer_note"],
                    int(r["answered_by"]) if r["answered_by"] is not None else None,
                    r["answered_at"],
                ),
            )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM plot_issues WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


class PlotFineSection:
    """The points the DM said were not a problem, with their notes (an `ExportSection`)."""

    name = "plot_fine"
    _KEYS = frozenset({"fine_key", "note", "by_user", "at"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT fine_key, note, by_user, at FROM plot_fine WHERE guild_id = %s"
            " AND campaign_id = %s ORDER BY fine_key",
            (guild_id, campaign_id),
        )
        return [{**dict(r), "by_user": str(r["by_user"])} for r in await cur.fetchall()]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise CampaignError(DAMAGED)
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != self._KEYS:
                raise CampaignError(DAMAGED)
            if not (
                _word(row["fine_key"])
                and row["fine_key"] not in seen
                and _text(row["note"], 500)
                and _user(row["by_user"])
                and _int(row["at"])
            ):
                raise CampaignError(DAMAGED)
            seen.add(row["fine_key"])
        return list(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for r in self.check(rows or []):
            await conn.execute(
                "INSERT INTO plot_fine (guild_id, campaign_id, fine_key, note, by_user, at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, r["fine_key"], r["note"], int(r["by_user"]), r["at"]),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM plot_fine WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
