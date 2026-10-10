"""Timed effects the DM starts, on the game clock (#998; docs/PLAN.md, "TimeBot"): Bless,
Mage Armor and the like. Nothing is guessed and nothing ends by itself: when the clock passes
an effect's end DMbot says once that it has likely ended, and the DM decides (Ended, or Still
going). Stored per campaign in its own server scope, changed only by a DM of the campaign
(checked in the same transaction), at most `MAX_RUNNING`, carried in backups, deleted with the
campaign. They live on the game clock, not in real time, so a restart keeps them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.timebot import clock as game
from dmbot.timebot.store import NOT_A_DM

MAX_RUNNING = 20
NAME_MAX = 60
TARGET_MAX = 60
SHOWN = 5  # effects listed on the clock message
EXTRA_MINUTES = 10  # "Still going +10 min"
NO_CLOCK = "Set the game clock first (⚙️ Settings, then Game clock), then start the timer."
TOO_MANY = f"{MAX_RUNNING} timers are running already. End one first."
GONE = "That timer isn't there any more."
DAMAGED = "That backup file's timers are damaged, so nothing was restored."


@dataclass(frozen=True, slots=True)
class Effect:
    number: int
    name: str
    target: str | None
    minutes: int  # how long it was timed for
    concentration: bool
    ends: int  # game minute
    told: bool  # the "has likely ended" line was said

    def line(self, now: int) -> str:
        who = f" on {self.target}" if self.target else ""
        mind = " 🧠" if self.concentration else ""
        if self.ends <= now:
            return f"⏳ {self.name}{who}: likely ended{mind}"
        return f"⏳ {self.name}{who}: until {game.short_label(self.ends)}{mind}"

    def ended_line(self) -> str:
        who = f" on {self.target}" if self.target else ""
        from dmbot.timebot.durations import words

        mind = " (concentration)" if self.concentration else ""
        return f"⏳ {self.name}{who} has likely ended ({words(self.minutes)}){mind}."


def _effect(row: dict[str, Any]) -> Effect:
    return Effect(
        int(row["number"]),
        row["name"],
        row["target"],
        int(row["minutes"]),
        bool(row["concentration"]),
        int(row["ends_minute"]),
        bool(row["told"]),
    )


def clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


class EffectStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def running(self, guild_id: int, campaign_id: str) -> list[Effect]:
        """The campaign's running effects, soonest to end first."""
        async with self._db.guild(guild_id) as conn:
            return await self._list(conn, guild_id, campaign_id)

    async def start(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        name: str,
        target: str | None,
        minutes: int,
        concentration: bool,
    ) -> Effect:
        name, target = clean(name, NAME_MAX), clean(target or "", TARGET_MAX) or None
        if not name or minutes < 1:
            raise CampaignError("Give the timer a name and a length.")
        async with self._db.guild(guild_id) as conn:
            await self._require_dm(conn, guild_id, campaign_id, user_id)
            now = await self._lock_clock(conn, guild_id, campaign_id)
            running = await self._list(conn, guild_id, campaign_id)
            if len(running) >= MAX_RUNNING:
                raise CampaignError(TOO_MANY)
            number = max((e.number for e in running), default=0) + 1
            cur = await conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM game_effects"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            number = max(number, int(row["n"]))
            ends = min(game.MAX_MINUTE, now + minutes)
            await conn.execute(
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, target,"
                " minutes, concentration, ends_minute, told) VALUES"
                " (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)",
                (guild_id, campaign_id, number, name, target, minutes, concentration, ends),
            )
            return Effect(number, name, target, minutes, concentration, ends, False)

    async def claim_due(self, guild_id: int, campaign_id: str, now: int) -> list[Effect]:
        """The effects whose end the clock has passed and that were not yet told about, marked
        as told in the same statement (so each is said once, however presses interleave)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE game_effects SET told = TRUE WHERE guild_id = %s AND campaign_id = %s"
                " AND ends_minute <= %s AND NOT told"
                " RETURNING number, name, target, minutes, concentration, ends_minute, told",
                (guild_id, campaign_id, now),
            )
            rows = sorted(await cur.fetchall(), key=lambda r: (r["ends_minute"], r["number"]))
            return [_effect(r) for r in rows]

    async def end(self, guild_id: int, campaign_id: str, user_id: int, number: int) -> Effect:
        async with self._db.guild(guild_id) as conn:
            await self._require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM game_effects WHERE guild_id = %s AND campaign_id = %s"
                " AND number = %s RETURNING number, name, target, minutes, concentration,"
                " ends_minute, told",
                (guild_id, campaign_id, number),
            )
            row = await cur.fetchone()
            if row is None:
                raise CampaignError(GONE)
            return _effect(row)

    async def extend(self, guild_id: int, campaign_id: str, user_id: int, number: int) -> Effect:
        """ "Still going": it runs EXTRA_MINUTES more from where the clock is now, and is told
        about again when that passes."""
        async with self._db.guild(guild_id) as conn:
            await self._require_dm(conn, guild_id, campaign_id, user_id)
            now = await self._lock_clock(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "UPDATE game_effects SET ends_minute = %s, told = FALSE"
                " WHERE guild_id = %s AND campaign_id = %s AND number = %s"
                " RETURNING number, name, target, minutes, concentration, ends_minute, told",
                (min(game.MAX_MINUTE, now + EXTRA_MINUTES), guild_id, campaign_id, number),
            )
            row = await cur.fetchone()
            if row is None:
                raise CampaignError(GONE)
            return _effect(row)

    @staticmethod
    async def _require_dm(conn: Conn, guild_id: int, campaign_id: str, user_id: int) -> None:
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, user_id),
        )
        if await cur.fetchone() is None:
            raise CampaignError(NOT_A_DM)

    @staticmethod
    async def _lock_clock(conn: Conn, guild_id: int, campaign_id: str) -> int:
        """The game minute now, with the clock row locked so numbering and counting are one
        at a time per campaign."""
        cur = await conn.execute(
            "SELECT minute FROM game_clocks WHERE guild_id = %s AND campaign_id = %s FOR UPDATE",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CampaignError(NO_CLOCK)
        return int(row["minute"])

    @staticmethod
    async def _list(conn: Conn, guild_id: int, campaign_id: str) -> list[Effect]:
        cur = await conn.execute(
            "SELECT number, name, target, minutes, concentration, ends_minute, told"
            " FROM game_effects WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY ends_minute, number",
            (guild_id, campaign_id),
        )
        return [_effect(r) for r in await cur.fetchall()]


def lines(effects: Sequence[Effect], now: int) -> list[str]:
    """The clock message's lines: at most SHOWN, the soonest first, and how many more."""
    shown = [e.line(now) for e in effects[:SHOWN]]
    if len(effects) > SHOWN:
        shown.append(f"… and {len(effects) - SHOWN} more")
    return shown


class EffectsSection:
    """Timers in campaign backups (an `ExportSection`). They hang on the game clock, so a
    backup carries both; ends are game minutes, which mean the same in the copy."""

    name = "game_effects"
    _KEYS: ClassVar[frozenset[str]] = frozenset(
        {"name", "target", "minutes", "concentration", "ends", "told"}
    )

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        return [
            {
                "name": e.name,
                "target": e.target,
                "minutes": e.minutes,
                "concentration": e.concentration,
                "ends": e.ends,
                "told": e.told,
            }
            for e in await EffectStore._list(conn, guild_id, campaign_id)
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if len(rows) > MAX_RUNNING:
            raise CampaignError(DAMAGED)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(self._KEYS):
                raise CampaignError(DAMAGED)
            name, target = row["name"], row["target"]
            if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX:
                raise CampaignError(DAMAGED)
            if target is not None and (not isinstance(target, str) or len(target) > TARGET_MAX):
                raise CampaignError(DAMAGED)
            if not target:
                target = None
            for key in ("minutes", "ends"):
                if type(row[key]) is not int or not 0 <= row[key] <= game.MAX_MINUTE:
                    raise CampaignError(DAMAGED)
            if row["minutes"] < 1 or type(row["concentration"]) is not bool:
                raise CampaignError(DAMAGED)
            if type(row["told"]) is not bool:
                raise CampaignError(DAMAGED)
            out.append({**row, "target": target})
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = self.check(rows) if rows and not isinstance(rows[0], dict) else rows
        checked = self.check(list(checked)) if checked else []
        for number, row in enumerate(checked, start=1):
            await conn.execute(
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, target, minutes,"
                " concentration, ends_minute, told) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    guild_id,
                    campaign_id,
                    number,
                    row["name"],
                    row["target"],
                    row["minutes"],
                    row["concentration"],
                    row["ends"],
                    row["told"],
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM game_effects WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
