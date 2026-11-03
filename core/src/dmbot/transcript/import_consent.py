"""Speakers without Discord and the uploader's consent records, in a campaign's backup (#3305).

A speaker without Discord is a named speaker of one campaign with a negative number (see migration
0327). The lines stored under it carry that number, so a restore must give the speaker back **the
same number**: the lines come back through the transcripts files with their `user_id` as it was.
That is why the number is per campaign, not global: nothing here is renumbered.

`import_consents` is what the uploader confirmed (who, when, which wording version): kept as long as
the lines are, so it goes with them.

Both sections are checked before a restore writes anything; a row that is not exactly what
`dump` writes makes the file damaged.
"""

from __future__ import annotations

import uuid
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

INT64_MAX = 2**63 - 1
SPEAKERS_MAX = 500
CONSENTS_MAX = 20000
DAMAGED_SPEAKER = "This backup file is damaged (bad speaker without Discord)."
DAMAGED_CONSENT = "This backup file is damaged (bad import confirmation)."


def _int(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CampaignError(DAMAGED_CONSENT)
    return value


def _number(text: object, error: str, *, negative: bool) -> int:
    """A user number written as text (they are bigger than a script's numbers)."""
    digits = text[1:] if isinstance(text, str) and text.startswith("-") else text
    if (
        not isinstance(text, str)
        or not isinstance(digits, str)
        or not digits.isascii()
        or not digits.isdecimal()
        or len(digits) > 19
    ):
        raise CampaignError(error)
    number = int(text)
    if (number >= 0 if negative else number <= 0) or abs(number) > INT64_MAX:
        raise CampaignError(error)
    return number


class SpeakersSection:
    name = "campaign_speakers"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT user_id, name, created_by, created_at FROM campaign_speakers"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY user_id DESC",
            (guild_id, campaign_id),
        )
        return [
            {
                "id": str(r["user_id"]),
                "name": r["name"],
                "by": str(r["created_by"]),
                "at": int(r["created_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[tuple[int, str, int, int]]:
        if not isinstance(rows, list) or len(rows) > SPEAKERS_MAX:
            raise CampaignError(DAMAGED_SPEAKER)
        out: list[tuple[int, str, int, int]] = []
        ids: set[int] = set()
        names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "name", "by", "at"}:
                raise CampaignError(DAMAGED_SPEAKER)
            number = _number(row["id"], DAMAGED_SPEAKER, negative=True)
            by = _number(row["by"], DAMAGED_SPEAKER, negative=False)
            name = row["name"]
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 80
                or number in ids
                or name.casefold() in names
                or type(row["at"]) is not int
                or not 0 <= row["at"] <= INT64_MAX
            ):
                raise CampaignError(DAMAGED_SPEAKER)
            ids.add(number)
            names.add(name.casefold())
            out.append((number, name, by, row["at"]))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = rows if all(isinstance(r, tuple) for r in rows) else self.check(rows)
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO campaign_speakers (guild_id, campaign_id, user_id, name, created_by,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                [(guild_id, campaign_id, *row) for row in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM campaign_speakers WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


class ImportConsentsSection:
    name = "import_consents"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, import_id, user_id, basis, confirmed_by, confirmed_at, terms_version, lines"
            " FROM import_consents WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY confirmed_at, id",
            (guild_id, campaign_id),
        )
        return [
            {
                "id": r["id"],
                "import": r["import_id"],
                "user": str(r["user_id"]),
                "basis": r["basis"],
                "by": str(r["confirmed_by"]),
                "at": int(r["confirmed_at"]),
                "version": int(r["terms_version"]),
                "lines": int(r["lines"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[tuple[str, str, int, str, int, int, int, int]]:
        keys = {"id", "import", "user", "basis", "by", "at", "version", "lines"}
        if not isinstance(rows, list) or len(rows) > CONSENTS_MAX:
            raise CampaignError(DAMAGED_CONSENT)
        out: list[tuple[str, str, int, str, int, int, int, int]] = []
        seen: set[tuple[str, int]] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys:
                raise CampaignError(DAMAGED_CONSENT)
            rid, imp, basis = row["id"], row["import"], row["basis"]
            if (
                not isinstance(rid, str)
                or len(rid) != 32
                or not all(c in "0123456789abcdef" for c in rid)
                or not isinstance(imp, str)
                or not 1 <= len(imp) <= 64
                or basis != "uploader_confirmed"
            ):
                raise CampaignError(DAMAGED_CONSENT)
            raw_user = row["user"]
            user = (
                _number(raw_user, DAMAGED_CONSENT, negative=True)
                if isinstance(raw_user, str) and raw_user.startswith("-")
                else _number(raw_user, DAMAGED_CONSENT, negative=False)
            )
            key = (imp, user)
            if key in seen:
                raise CampaignError(DAMAGED_CONSENT)
            seen.add(key)
            out.append(
                (
                    rid,
                    imp,
                    user,
                    basis,
                    _number(row["by"], DAMAGED_CONSENT, negative=False),
                    _int(row["at"], 0, INT64_MAX),
                    _int(row["version"], 1, 1000),
                    _int(row["lines"], 0, 2**31 - 1),
                )
            )
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = rows if all(isinstance(r, tuple) for r in rows) else self.check(rows)
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id,"
                " basis, confirmed_by, confirmed_at, terms_version, lines)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                # (a new id for each: they are unique in the server, and a restore may sit next to
                # the campaign it was copied from)
                [(guild_id, campaign_id, uuid.uuid4().hex, *row[1:]) for row in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM import_consents WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
