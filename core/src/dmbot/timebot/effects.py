"""Timed effects the DM starts, on the game clock (#998; docs/PLAN.md, "TimeBot"): Bless,
Mage Armor and the like. Nothing is guessed and nothing ends by itself: when the clock passes
an effect's end DMbot says once that it has likely ended, and the DM decides (Ended, or Still
going). Stored per campaign in its own server scope, changed only by a DM of the campaign
(checked in the same transaction), at most `MAX_RUNNING`, carried in backups, deleted with the
campaign. They live on the game clock, not in real time, so a restart keeps them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.timebot import clock as game
from dmbot.timebot.durations import words
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
    extended: int = 0  # minutes the DM added with "Still going" (#1112)
    # Split party (#1607): the group it counts on (None: the one clock, or the first group)
    group: int | None = None

    def line(self, now: int, group_name: str | None = None) -> str:
        who = f" on {self.target}" if self.target else ""
        mind = " 🧠" if self.concentration else ""
        where = f" ({group_name})" if group_name else ""
        if self.ends <= now:
            return f"⏳ {self.name}{who}{where}: likely ended{mind}"
        return f"⏳ {self.name}{who}{where}: until {game.short_label(self.ends)}{mind}"

    def ended_line(self, group_name: str | None = None) -> str:
        who = f" on {self.target}" if self.target else ""
        mind = " (concentration)" if self.concentration else ""
        # The total, so a timer the DM kept going doesn't read as if it only ran its first
        # length, and a plain note that the DM extended it (#1112).
        total = words(self.minutes + self.extended)
        how = f"{total}, extended by the DM" if self.extended else total
        line = f"⏳ {self.name}{who} has likely ended ({how}){mind}."
        return f"{group_name}: {line}" if group_name else line


def _effect(row: dict[str, Any]) -> Effect:
    return Effect(
        int(row["number"]),
        row["name"],
        row["target"],
        int(row["minutes"]),
        bool(row["concentration"]),
        int(row["ends_minute"]),
        bool(row["told"]),
        int(row["extended"]),
        row.get("group_position"),
    )


# The columns every statement that returns an effect names, in one place.
_COLS = "number, name, target, minutes, concentration, ends_minute, told, extended, group_position"


def clean(text: str, limit: int) -> str:
    """One line, no control characters (a NUL would be refused by the database), cut to size."""
    kept = "".join(c if c.isprintable() or c.isspace() else " " for c in text)
    return " ".join(kept.split())[:limit]


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
            # The clock's own counter, which only goes up: a number is never reused.
            cur = await conn.execute(
                "UPDATE game_clocks SET effects_made = GREATEST(effects_made,"
                " %s) + 1 WHERE guild_id = %s AND campaign_id = %s RETURNING effects_made",
                (max((e.number for e in running), default=0), guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            number = int(row["effects_made"])
            # While the party is split a timer counts on the group picked now (#1607).
            ends = min(game.MAX_MINUTE, now.for_group(now.picked) + minutes)
            await conn.execute(
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, target,"
                " minutes, concentration, ends_minute, told, group_position) VALUES"
                " (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s)",
                (
                    guild_id,
                    campaign_id,
                    number,
                    name,
                    target,
                    minutes,
                    concentration,
                    ends,
                    now.picked,
                ),
            )
            return Effect(number, name, target, minutes, concentration, ends, False, 0, now.picked)

    async def claim_due(self, guild_id: int, campaign_id: str, now: int) -> list[Effect]:
        """The effects whose end the clock has passed and that were not yet told about, marked
        as told in the same statement (so each is said once, however presses interleave).
        While the party is split each timer is compared with its own group's time (a timer
        with no group counts on the first), not with `now`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE game_effects e SET told = TRUE WHERE e.guild_id = %s"
                " AND e.campaign_id = %s AND NOT e.told AND e.ends_minute <= COALESCE("
                "(SELECT g.minute FROM game_clock_groups g WHERE g.guild_id = e.guild_id"
                " AND g.campaign_id = e.campaign_id"
                " AND g.position = COALESCE(e.group_position, 1)), %s)"
                f" RETURNING {_COLS}",
                (guild_id, campaign_id, now),
            )
            rows = sorted(await cur.fetchall(), key=lambda r: (r["ends_minute"], r["number"]))
            return [_effect(r) for r in rows]

    async def end(self, guild_id: int, campaign_id: str, user_id: int, number: int) -> Effect:
        async with self._db.guild(guild_id) as conn:
            await self._require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM game_effects WHERE guild_id = %s AND campaign_id = %s"
                f" AND number = %s RETURNING {_COLS}",
                (guild_id, campaign_id, number),
            )
            row = await cur.fetchone()
            if row is None:
                raise CampaignError(GONE)
            return _effect(row)

    async def extend(self, guild_id: int, campaign_id: str, user_id: int, number: int) -> Effect:
        """ "Still going": it runs EXTRA_MINUTES more from where its clock is now (its group's,
        while the party is split), and is told about again when that passes."""
        async with self._db.guild(guild_id) as conn:
            await self._require_dm(conn, guild_id, campaign_id, user_id)
            clock = await self._lock_clock(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "SELECT group_position FROM game_effects WHERE guild_id = %s AND campaign_id = %s"
                " AND number = %s",
                (guild_id, campaign_id, number),
            )
            found = await cur.fetchone()
            if found is None:
                raise CampaignError(GONE)
            now = clock.for_group(found["group_position"])
            cur = await conn.execute(
                "UPDATE game_effects SET ends_minute = LEAST(%s, GREATEST(ends_minute, %s) + %s),"
                " extended = extended + %s, told = FALSE WHERE guild_id = %s AND campaign_id = %s"
                f" AND number = %s RETURNING {_COLS}",
                (
                    game.MAX_MINUTE,
                    now,
                    EXTRA_MINUTES,
                    EXTRA_MINUTES,
                    guild_id,
                    campaign_id,
                    number,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise CampaignError(GONE)
            return _effect(row)

    async def untell(self, guild_id: int, campaign_id: str, number: int) -> None:
        """The "has likely ended" line could not be posted: say it next time."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE game_effects SET told = FALSE WHERE guild_id = %s AND campaign_id = %s"
                " AND number = %s",
                (guild_id, campaign_id, number),
            )

    @staticmethod
    async def _require_dm(conn: Conn, guild_id: int, campaign_id: str, user_id: int) -> None:
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, user_id),
        )
        if await cur.fetchone() is None:
            raise CampaignError(NOT_A_DM)

    @staticmethod
    async def _lock_clock(conn: Conn, guild_id: int, campaign_id: str) -> ClockNow:
        """The game minute now, with the clock row locked so numbering and counting are one
        at a time per campaign. While the party is split, also each group's minute."""
        cur = await conn.execute(
            "SELECT minute, picked_group FROM game_clocks"
            " WHERE guild_id = %s AND campaign_id = %s FOR UPDATE",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CampaignError(NO_CLOCK)
        groups: dict[int, int] = {}
        if row["picked_group"] is not None:
            cur = await conn.execute(
                "SELECT position, minute FROM game_clock_groups"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            groups = {int(r["position"]): int(r["minute"]) for r in await cur.fetchall()}
        picked = row["picked_group"] if row["picked_group"] in groups else None
        return ClockNow(int(row["minute"]), picked, groups if picked else {})

    @staticmethod
    async def _list(conn: Conn, guild_id: int, campaign_id: str) -> list[Effect]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM game_effects WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY ends_minute, number",
            (guild_id, campaign_id),
        )
        return [_effect(r) for r in await cur.fetchall()]


@dataclass(frozen=True, slots=True)
class ClockNow:
    """The game minute a timer counts on: the one clock's, or (split, #1607) its group's."""

    main: int
    picked: int | None = None  # the picked group while split
    groups: Mapping[int, int] = field(default_factory=dict)

    def for_group(self, position: int | None) -> int:
        """The minute for a group; a timer with no group counts on the first while split, and
        everything counts on the one clock when not."""
        if not self.groups:
            return self.main
        return self.groups.get(position or 1, self.main)


def lines(
    effects: Sequence[Effect],
    now: int,
    groups: Mapping[int, tuple[str, int]] | None = None,
) -> list[str]:
    """The clock message's lines: at most SHOWN, the soonest first, and how many more. While
    the party is split `groups` maps a position to (name, minute): each timer is judged on its
    own group's time and says which group it is for."""

    def one(e: Effect) -> str:
        if not groups:
            return e.line(now)
        name, minute = groups.get(e.group or 1, (None, now))
        return e.line(minute, name)

    shown = [one(e) for e in effects[:SHOWN]]
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
    # Added with #1112: backups made before it don't have it, and mean none.
    # (and #1607: "group", the split-party group a timer counts on, or None).
    _OPTIONAL: ClassVar[frozenset[str]] = frozenset({"extended", "group"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        return [
            {
                "name": e.name,
                "target": e.target,
                "minutes": e.minutes,
                "concentration": e.concentration,
                "ends": e.ends,
                "told": e.told,
                "extended": e.extended,
                "group": e.group,
            }
            for e in await EffectStore._list(conn, guild_id, campaign_id)
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if len(rows) > MAX_RUNNING:
            raise CampaignError(DAMAGED)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) - self._OPTIONAL != set(self._KEYS):
                raise CampaignError(DAMAGED)
            extended = row.get("extended", 0)
            if type(extended) is not int or not 0 <= extended <= game.MAX_MINUTE:
                raise CampaignError(DAMAGED)
            group = row.get("group")
            if group is not None and (type(group) is not int or not 1 <= group <= 3):
                raise CampaignError(DAMAGED)
            name, target = row["name"], row["target"]
            if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX:
                raise CampaignError(DAMAGED)
            name = clean(name, NAME_MAX)
            if not name:
                raise CampaignError(DAMAGED)
            if target is not None and (not isinstance(target, str) or len(target) > TARGET_MAX):
                raise CampaignError(DAMAGED)
            target = clean(target, TARGET_MAX) or None if target else None
            for key in ("minutes", "ends"):
                if type(row[key]) is not int or not 0 <= row[key] <= game.MAX_MINUTE:
                    raise CampaignError(DAMAGED)
            if row["minutes"] < 1 or type(row["concentration"]) is not bool:
                raise CampaignError(DAMAGED)
            if type(row["told"]) is not bool:
                raise CampaignError(DAMAGED)
            out.append(
                {**row, "name": name, "target": target, "extended": extended, "group": group}
            )
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = self.check(list(rows)) if rows else []  # the store checked once; this is safe
        for number, row in enumerate(checked, start=1):
            await conn.execute(
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, target, minutes,"
                " concentration, ends_minute, told, extended, group_position)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    row["extended"],
                    row["group"],
                ),
            )

        if checked:
            await conn.execute(
                "UPDATE game_clocks SET effects_made = GREATEST(effects_made, %s)"
                " WHERE guild_id = %s AND campaign_id = %s",
                (len(checked), guild_id, campaign_id),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM game_effects WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
