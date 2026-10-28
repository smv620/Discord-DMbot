"""The DM's answers to the after-session report in a campaign's backup (#2661).

A backup holds the whole campaign, and an answer is a decision the DM made that cannot be made
again ("That's intended" must not be raised again after a restore), so the answers travel with it.
The items themselves (the minor contradictions and what the party has wrong) are made again by the
next check and are left out. An answer holds an item's key (a hash of its own ids), the answer, who
gave it and when: no transcript word. Every row is checked before it is loaded, as for every
section, and a person from another server is never trusted by id alone."""

from __future__ import annotations

import re
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.report_fixed import FIXED
from dmbot.memory.report_store import ANSWERS, CONTRADICTION, DIFFERENCE, REMIND

MAX_ROWS = 20000
INT64_MAX = 2**63 - 1
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)
_KEY = re.compile(r"[0-9a-f]{32}")


def _int(value: Any, low: int = 0) -> bool:
    return type(value) is int and low <= value <= INT64_MAX


def _user(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and 0 < int(value) <= INT64_MAX
    )


class ReportAnswersSection:
    """The DM's answers to the report's items (an `ExportSection`)."""

    name = "report_answers"
    _KEYS = frozenset(
        {
            "item_key",
            "item_kind",
            "answer",
            "answered_by",
            "answered_at",
            "remind_from",
        }
    )

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT item_key, item_kind, answer, answered_by, answered_at, remind_from, remind_was"
            " FROM report_answers WHERE guild_id = %s AND campaign_id = %s ORDER BY item_key",
            (guild_id, campaign_id),
        )
        return [{**dict(r), "answered_by": str(r["answered_by"])} for r in await cur.fetchall()]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise CampaignError(DAMAGED)
        seen: set[str] = set()
        for row in rows:
            # (`remind_was` came later, #2661: a backup made before it has none)
            if not isinstance(row, dict) or set(row) - {"remind_was"} != self._KEYS:
                raise CampaignError(DAMAGED)
            row.setdefault("remind_was", None)
            key = row["item_key"]
            if not (
                isinstance(key, str)
                and _KEY.fullmatch(key)
                and key not in seen
                and row["item_kind"] in (CONTRADICTION, DIFFERENCE)
                and row["answer"] in ANSWERS
                and _user(row["answered_by"])
                and _int(row["answered_at"])
                and (
                    _int(row["remind_from"], 1)
                    if row["answer"] == REMIND
                    else row["remind_from"] is None
                )
                and (
                    row["remind_was"] is None
                    or (row["answer"] == FIXED and _int(row["remind_was"], 1))
                )
            ):
                raise CampaignError(DAMAGED)
            seen.add(key)
        return list(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for r in self.check(rows or []):
            await conn.execute(
                "INSERT INTO report_answers (guild_id, campaign_id, item_key, item_kind, answer,"
                " answered_by, answered_at, remind_from, remind_was)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    guild_id, campaign_id, r["item_key"], r["item_kind"], r["answer"],
                    int(r["answered_by"]), r["answered_at"], r["remind_from"], r["remind_was"],
                ),
            )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM report_answers WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
