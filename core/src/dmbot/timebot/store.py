"""Where a campaign's game clock is kept (#965): one row per campaign, in that campaign's
own server scope, changed only by a DM of the campaign (checked in the same transaction as
the change), carried in backups and deleted with the campaign. See `timebot.clock` for the
arithmetic and docs/PLAN.md, "TimeBot".

While the party is split (#1607) the campaign also has two or three rows in
`game_clock_groups`; `Stored.clock` is then the **picked group's** clock, so everything that
reads or changes "the clock" (the buttons, Undo, combat, the recap) acts on the group the DM
picked without knowing about groups. The row in `game_clocks` keeps the clock from before the
split, which is the clock again at Rejoin.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.timebot import clock as game
from dmbot.timebot import split
from dmbot.timebot.clock import Clock
from dmbot.timebot.split import Group

NOT_A_DM = "Only this campaign's DMs can use the clock."
DAMAGED = "That backup file's game clock is damaged, so nothing was restored."
NO_CLOCK = "Set the game clock first (⚙️ Manage DMbot, then Game clock)."
GONE = "That group is gone. The party may have rejoined, so check the clock message."
MOVED_ON = (
    "The clocks have moved on since, so there is nothing to undo. "
    "Use **Rejoin the party** to bring them together."
)


@dataclass(frozen=True, slots=True)
class Stored:
    """A campaign's clock and where its pinned message is (None until it is posted)."""

    clock: Clock
    channel_id: int | None
    message_id: int | None
    # The players' pinned clock in the old game-time channel (#1116). Nothing writes them since
    # #2877 (the clock is a small line in the chat channel); a later migration drops the columns.
    players_channel_id: int | None = None
    players_message_id: int | None = None
    # Split party (#1607): the groups (empty when not split), the position of the one the
    # buttons act on (its clock is `clock`), and whether the two once-per-split lines were said.
    groups: tuple[Group, ...] = ()
    picked: int | None = None
    phrase_told: bool = False
    drift_told: bool = False

    @property
    def split(self) -> bool:
        return bool(self.groups)

    @property
    def picked_group(self) -> Group | None:
        return next((g for g in self.groups if g.position == self.picked), None)


@dataclass(frozen=True, slots=True)
class Rejoined:
    """What Rejoin did: the groups it joined, the clock it left, and Undo's token."""

    groups: tuple[Group, ...]
    kept: Group
    clock: Clock
    token: str
    dropped_fights: tuple[str, ...] = ()  # names of other groups that were in a fight


@dataclass(frozen=True, slots=True)
class Restored:
    """A checked backup's clock: the one clock, and the groups if the party was split."""

    clock: Clock
    groups: tuple[Group, ...] = ()
    picked: int | None = None
    # the clock's log (#3091): (position or None, real time, game minute), oldest first
    log: tuple[tuple[int | None, int, int], ...] = ()


def _pack(clock: Clock) -> list[int | None]:
    return [
        clock.minute,
        clock.last_long_rest,
        clock.tired_told_for,
        clock.combat_round,
        clock.combat_secs,
    ]


def _unpack(values: list[Any]) -> Clock:
    minute, rest, tired, round_, secs = values
    return Clock(int(minute), int(rest), tired, round_, int(secs))


def _group(row: dict[str, Any]) -> Group:
    tired = row["tired_told_for"]
    return Group(
        int(row["position"]),
        row["name"],
        Clock(
            int(row["minute"]),
            int(row["last_long_rest"]),
            None if tired is None else int(tired),
            None if row["combat_round"] is None else int(row["combat_round"]),
            int(row["combat_secs"]),
        ),
    )


_GROUP_COLS = "position, name, minute, last_long_rest, tired_told_for, combat_round, combat_secs"


def _stored(row: dict[str, Any]) -> Stored:
    tired = row["tired_told_for"]
    return Stored(
        Clock(
            int(row["minute"]),
            int(row["last_long_rest"]),
            None if tired is None else int(tired),
            None if row["combat_round"] is None else int(row["combat_round"]),
            int(row["combat_secs"]),
        ),
        None if row["channel_id"] is None else int(row["channel_id"]),
        None if row["message_id"] is None else int(row["message_id"]),
        None if row["players_channel_id"] is None else int(row["players_channel_id"]),
        None if row["players_message_id"] is None else int(row["players_message_id"]),
    )


class ClockStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def get(self, guild_id: int, campaign_id: str) -> Stored | None:
        async with self._db.guild(guild_id) as conn:
            return await self._read(conn, guild_id, campaign_id, lock=False)

    async def change(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        fn: Callable[[Clock | None], Clock | None],
    ) -> tuple[Clock | None, Clock | None]:
        """`change_named` without the group's name: (before, after)."""
        before, after, _ = await self.change_named(guild_id, campaign_id, user_id, fn)
        return before, after

    async def change_named(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        fn: Callable[[Clock | None], Clock | None],
        *,
        group: int | None = None,
        ungrouped: bool = False,
        refuse_if_split: bool = False,
    ) -> tuple[Clock | None, Clock | None, Group | None]:
        """Apply `fn` to the campaign's clock (None if not set yet); it returns the new
        clock, or None to change nothing. Only a DM of the campaign may; the row is locked
        for the change, so two presses never lose one. While the party is split the clock is
        the picked group's (#1607), or `group`'s when one is given (an Undo made for one group).
        A `group` that isn't there (the party rejoined since) is a clock that isn't there: `fn`
        gets None. Returns (before, after, the group it acted on or None); the group is read in
        the same transaction as the change, so it is the one that moved. `ungrouped` is for an
        Undo made while the party was one clock: if it is split now there is no such clock to
        change. `refuse_if_split` is for spoken phrases, which move nothing while split; it is
        checked here under the row lock, so a split just made cannot be raced."""
        async with self._db.guild(guild_id) as conn:
            if not await self._is_dm(conn, guild_id, campaign_id, user_id):
                raise CampaignError(NOT_A_DM)
            current = await self._read(conn, guild_id, campaign_id, lock=True)
            acting = current.picked_group if current is not None else None
            if current is not None and current.split and refuse_if_split:
                return current.clock, current.clock, acting
            if ungrouped and current is not None and current.split:
                current, acting = None, None
            if group is not None and current is not None:
                acting = next((g for g in current.groups if g.position == group), None)
                if acting is None:
                    current = None  # the group is gone: there is no such clock to change
            before = None if current is None else (acting.clock if acting else current.clock)
            after = fn(before)
            if after is None:
                return before, before, acting
            if current is not None and acting is not None:
                await self._write_group(conn, guild_id, campaign_id, acting.position, after)
                await conn.execute(
                    "UPDATE game_clocks SET updated_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s",
                    (int(self._now()), guild_id, campaign_id),
                )
            else:
                await self._write_main(conn, guild_id, campaign_id, after)
            if before is not None and after.minute < before.minute:
                # The clock went back (Undo, or the time set earlier): a timer whose end is
                # ahead again will be said again when the clock passes it. While split, only
                # the picked group's timers (a timer with no group belongs to the first).
                picked = acting.position if acting is not None else None
                await conn.execute(
                    "UPDATE game_effects SET told = FALSE WHERE guild_id = %s AND campaign_id = %s"
                    " AND ends_minute > %s AND told"
                    + ("" if picked is None else " AND COALESCE(group_position, 1) = %s"),
                    (guild_id, campaign_id, after.minute, *(() if picked is None else (picked,))),
                )
            return before, after, acting

    async def _write_main(self, conn: Conn, guild_id: int, campaign_id: str, after: Clock) -> None:
        await conn.execute(
            "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
            " tired_told_for, combat_round, combat_secs, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET minute = EXCLUDED.minute,"
            " last_long_rest = EXCLUDED.last_long_rest,"
            " tired_told_for = EXCLUDED.tired_told_for,"
            " combat_round = EXCLUDED.combat_round, combat_secs = EXCLUDED.combat_secs,"
            " updated_at = EXCLUDED.updated_at",
            (
                guild_id,
                campaign_id,
                after.minute,
                after.last_long_rest,
                after.tired_told_for,
                after.combat_round,
                after.combat_secs,
                int(self._now()),
            ),
        )

    @staticmethod
    async def _write_group(
        conn: Conn, guild_id: int, campaign_id: str, position: int | None, after: Clock
    ) -> None:
        await conn.execute(
            "UPDATE game_clock_groups SET minute = %s, last_long_rest = %s, tired_told_for = %s,"
            " combat_round = %s, combat_secs = %s"
            " WHERE guild_id = %s AND campaign_id = %s AND position = %s",
            (
                after.minute,
                after.last_long_rest,
                after.tired_told_for,
                after.combat_round,
                after.combat_secs,
                guild_id,
                campaign_id,
                position,
            ),
        )

    async def in_combat(self, guild_id: int, campaign_id: str) -> bool:
        """Is this campaign's table in combat? The one question other parts ask (#1127): the
        15-minute sheet reads pause in combat, and FightBot runs only in combat."""
        stored = await self.get(guild_id, campaign_id)
        if stored is None:
            return False
        # (#1607) split: a fight in any group is a fight at the table
        return game.in_combat(stored.clock) or any(game.in_combat(g.clock) for g in stored.groups)

    async def set_message(
        self, guild_id: int, campaign_id: str, channel_id: int, message_id: int
    ) -> None:
        """Remember the pinned clock message so it can be edited in place."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE game_clocks SET channel_id = %s, message_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s",
                (channel_id, message_id, guild_id, campaign_id),
            )

    # ---- the split party (#1607) ---------------------------------------------------------

    async def split(
        self, guild_id: int, campaign_id: str, user_id: int, names: list[str]
    ) -> tuple[Stored, str]:
        """Split the party into the named groups (already checked by `split.check_names`), each
        at the clock's time and last long rest. Returns the new state and Undo's token. Not
        while a fight is on, and not when it is split already."""
        token = secrets.token_hex(4)
        async with self._db.guild(guild_id) as conn:
            current = await self._locked_dm_state(conn, guild_id, campaign_id, user_id)
            if current is None:
                raise CampaignError(NO_CLOCK)
            if current.split:
                raise CampaignError(split.ALREADY_SPLIT)
            if game.in_combat(current.clock):
                raise CampaignError(split.IN_COMBAT)
            groups = split.start(current.clock, names)
            for g in groups:
                await conn.execute(
                    "INSERT INTO game_clock_groups (guild_id, campaign_id, position, name,"
                    " minute, last_long_rest, tired_told_for, combat_round, combat_secs)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 0)",
                    (
                        guild_id,
                        campaign_id,
                        g.position,
                        g.name,
                        g.clock.minute,
                        g.clock.last_long_rest,
                        g.clock.tired_told_for,
                    ),
                )
            await conn.execute(
                "UPDATE game_clocks SET picked_group = 1, split_phrase_told = FALSE,"
                " split_drift_told = FALSE, split_undo = %s, updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s",
                (Jsonb({"kind": "split", "token": token}), int(self._now()), guild_id, campaign_id),
            )
            stored = await self._read(conn, guild_id, campaign_id, lock=False)
        assert stored is not None
        return stored, token

    async def pick(self, guild_id: int, campaign_id: str, user_id: int, position: int) -> Stored:
        """The buttons act on this group from now on (remembered across restarts)."""
        async with self._db.guild(guild_id) as conn:
            current = await self._locked_dm_state(conn, guild_id, campaign_id, user_id)
            if current is None or not any(g.position == position for g in current.groups):
                raise CampaignError(GONE)
            await conn.execute(
                "UPDATE game_clocks SET picked_group = %s WHERE guild_id = %s AND campaign_id = %s",
                (position, guild_id, campaign_id),
            )
            stored = await self._read(conn, guild_id, campaign_id, lock=False)
        assert stored is not None
        return stored

    async def rejoin(self, guild_id: int, campaign_id: str, user_id: int, keep: int) -> Rejoined:
        """Back to one clock at group `keep`'s time, with the latest long rest any group had.
        Every timer moves with its group's time to the kept time (the same time left). What
        Undo needs is saved with the clock row."""
        token = secrets.token_hex(4)
        async with self._db.guild(guild_id) as conn:
            current = await self._locked_dm_state(conn, guild_id, campaign_id, user_id)
            if current is None or not current.split:
                raise CampaignError(split.NOT_SPLIT)
            kept = next((g for g in current.groups if g.position == keep), None)
            if kept is None:
                raise CampaignError(GONE)
            clock = split.rejoin(current.groups, keep)
            main = await self._read_main(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "SELECT number, group_position, ends_minute, told FROM game_effects"
                " WHERE guild_id = %s AND campaign_id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            timers = list(await cur.fetchall())
            minutes = {g.position: g.clock.minute for g in current.groups}
            undo_timers: list[list[Any]] = []
            for row in timers:
                at = minutes.get(row["group_position"] or 1, kept.clock.minute)
                ends = split.shifted(int(row["ends_minute"]), at, clock.minute)
                told = bool(row["told"]) and ends <= clock.minute
                undo_timers.append(
                    [row["number"], row["group_position"], int(row["ends_minute"]), row["told"]]
                )
                await conn.execute(
                    "UPDATE game_effects SET ends_minute = %s, told = %s, group_position = NULL"
                    " WHERE guild_id = %s AND campaign_id = %s AND number = %s",
                    (ends, told, guild_id, campaign_id, row["number"]),
                )
            snapshot = {
                "kind": "rejoin",
                "token": token,
                "after": _pack(clock),
                "main": _pack(main),
                "groups": [[g.position, g.name, *_pack(g.clock)] for g in current.groups],
                "picked": current.picked,
                "phrase": current.phrase_told,
                "drift": current.drift_told,
                "timers": undo_timers,
            }
            await conn.execute(
                "DELETE FROM game_clock_groups WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            await self._write_main(conn, guild_id, campaign_id, clock)
            await conn.execute(
                "UPDATE game_clocks SET picked_group = NULL, split_phrase_told = FALSE,"
                " split_drift_told = FALSE, split_undo = %s WHERE guild_id = %s"
                " AND campaign_id = %s",
                (Jsonb(snapshot), guild_id, campaign_id),
            )
        fights = tuple(
            g.name for g in current.groups if g.position != keep and game.in_combat(g.clock)
        )
        return Rejoined(current.groups, kept, clock, token, fights)

    async def undo_split(self, guild_id: int, campaign_id: str, user_id: int, token: str) -> bool:
        """Take a split back, if it is the last split or rejoin and no group's clock has moved
        since. False when it can't be undone (said in plain words by the caller)."""
        async with self._db.guild(guild_id) as conn:
            current = await self._locked_dm_state(conn, guild_id, campaign_id, user_id)
            if current is None or not current.split:
                return False
            saved = await self._saved_undo(conn, guild_id, campaign_id)
            if saved is None or saved.get("kind") != "split" or saved.get("token") != token:
                return False
            main = await self._read_main(conn, guild_id, campaign_id)
            if any(
                g.clock.minute != main.minute
                or g.clock.last_long_rest != main.last_long_rest
                or game.in_combat(g.clock)  # (a fight would be lost)
                for g in current.groups
            ):
                return False  # a group moved on: Undo would lose that
            await conn.execute(
                "DELETE FROM game_clock_groups WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            await conn.execute(
                "UPDATE game_effects SET group_position = NULL"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            await conn.execute(
                "UPDATE game_clocks SET picked_group = NULL, split_phrase_told = FALSE,"
                " split_drift_told = FALSE, split_undo = NULL WHERE guild_id = %s"
                " AND campaign_id = %s",
                (guild_id, campaign_id),
            )
        return True

    async def undo_rejoin(self, guild_id: int, campaign_id: str, user_id: int, token: str) -> bool:
        """Take a rejoin back, if it is the last split or rejoin and the clock is still where
        it left it: the groups, their times and their timers come back."""
        async with self._db.guild(guild_id) as conn:
            current = await self._locked_dm_state(conn, guild_id, campaign_id, user_id)
            if current is None or current.split:
                return False
            saved = await self._saved_undo(conn, guild_id, campaign_id)
            if saved is None or saved.get("kind") != "rejoin" or saved.get("token") != token:
                return False
            if _pack(current.clock) != saved["after"]:
                return False  # the clock moved since
            for position, name, *clock in saved["groups"]:
                group = Group(int(position), name, _unpack(clock))
                await conn.execute(
                    "INSERT INTO game_clock_groups (guild_id, campaign_id, position, name,"
                    " minute, last_long_rest, tired_told_for, combat_round, combat_secs)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id,
                        campaign_id,
                        group.position,
                        group.name,
                        group.clock.minute,
                        group.clock.last_long_rest,
                        group.clock.tired_told_for,
                        group.clock.combat_round,
                        group.clock.combat_secs,
                    ),
                )
            # A timer started after the rejoin counts on the first group now; keep its time left.
            first = next((g for g in saved["groups"] if g[0] == 1), None)
            known = {row[0] for row in saved["timers"]}
            if first is not None:
                shift = int(first[2]) - current.clock.minute
                await conn.execute(
                    "UPDATE game_effects SET ends_minute = LEAST(%s, GREATEST(0, ends_minute + %s))"
                    " WHERE guild_id = %s AND campaign_id = %s AND NOT (number = ANY(%s))",
                    (game.MAX_MINUTE, shift, guild_id, campaign_id, list(known)),
                )
            for number, group_position, ends, told in saved["timers"]:
                await conn.execute(
                    "UPDATE game_effects SET group_position = %s, ends_minute = %s, told = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND number = %s",
                    (group_position, ends, told, guild_id, campaign_id, number),
                )
            await self._write_main(conn, guild_id, campaign_id, _unpack(saved["main"]))
            await conn.execute(
                "UPDATE game_clocks SET picked_group = %s, split_phrase_told = %s,"
                " split_drift_told = %s, split_undo = NULL WHERE guild_id = %s"
                " AND campaign_id = %s",
                (
                    saved["picked"],
                    bool(saved["phrase"]),
                    bool(saved["drift"]),
                    guild_id,
                    campaign_id,
                ),
            )
        return True

    async def claim_phrase_notice(self, guild_id: int, campaign_id: str) -> bool:
        """True once per split: the party is split and the "I didn't move the clock" line has
        not been said yet. Marked in the same statement, so two phrases say it once."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE game_clocks SET split_phrase_told = TRUE WHERE guild_id = %s"
                " AND campaign_id = %s AND picked_group IS NOT NULL AND NOT split_phrase_told"
                " RETURNING 1 AS ok",
                (guild_id, campaign_id),
            )
            return await cur.fetchone() is not None

    async def claim_drift(self, guild_id: int, campaign_id: str) -> str | None:
        """The "Tower is 9 hours ahead of Camp." line, once per split, when one group is more
        than 8 game hours ahead of another; None otherwise. A reminder, never a block."""
        async with self._db.guild(guild_id) as conn:
            current = await self._read(conn, guild_id, campaign_id, lock=True)
            if current is None or not current.split or current.drift_told:
                return None
            found = split.drift(current.groups)
            if found is None:
                return None
            await conn.execute(
                "UPDATE game_clocks SET split_drift_told = TRUE"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return split.drift_line(found)

    async def _locked_dm_state(
        self, conn: Conn, guild_id: int, campaign_id: str, user_id: int
    ) -> Stored | None:
        """The state with the clock row locked, after checking the person is a DM."""
        if not await self._is_dm(conn, guild_id, campaign_id, user_id):
            raise CampaignError(NOT_A_DM)
        return await self._read(conn, guild_id, campaign_id, lock=True)

    @staticmethod
    async def _saved_undo(conn: Conn, guild_id: int, campaign_id: str) -> dict[str, Any] | None:
        cur = await conn.execute(
            "SELECT split_undo FROM game_clocks WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        saved = None if row is None else row["split_undo"]
        return saved if isinstance(saved, dict) else None

    @staticmethod
    async def _is_dm(conn: Conn, guild_id: int, campaign_id: str, user_id: int) -> bool:
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, user_id),
        )
        return await cur.fetchone() is not None

    @staticmethod
    async def _read_main(conn: Conn, guild_id: int, campaign_id: str) -> Clock:
        """The clock row itself: the clock from before a split (the one buttons act on is
        `Stored.clock`, which is a group's while split)."""
        cur = await conn.execute(
            "SELECT minute, last_long_rest, tired_told_for, combat_round, combat_secs"
            " FROM game_clocks WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        assert row is not None
        tired = row["tired_told_for"]
        return Clock(
            int(row["minute"]),
            int(row["last_long_rest"]),
            None if tired is None else int(tired),
            None if row["combat_round"] is None else int(row["combat_round"]),
            int(row["combat_secs"]),
        )

    @staticmethod
    async def _read(conn: Conn, guild_id: int, campaign_id: str, *, lock: bool) -> Stored | None:
        cur = await conn.execute(
            "SELECT minute, last_long_rest, tired_told_for, combat_round, combat_secs,"
            " channel_id, message_id, players_channel_id, players_message_id,"
            " picked_group, split_phrase_told, split_drift_told"
            " FROM game_clocks WHERE guild_id = %s AND campaign_id = %s"
            + (" FOR UPDATE" if lock else ""),
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        stored = _stored(row)
        if row["picked_group"] is None:
            return stored
        cur = await conn.execute(
            f"SELECT {_GROUP_COLS} FROM game_clock_groups"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY position",
            (guild_id, campaign_id),
        )
        groups = tuple(_group(r) for r in await cur.fetchall())
        picked = next((g for g in groups if g.position == row["picked_group"]), None)
        if picked is None:  # (a picked group that isn't there: treat the party as not split)
            return stored
        return replace(
            stored,
            clock=picked.clock,
            groups=groups,
            picked=picked.position,
            phrase_told=bool(row["split_phrase_told"]),
            drift_told=bool(row["split_drift_told"]),
        )


class ClockSection:
    """The game clock in campaign backups (an `ExportSection`). No message ids: they mean
    nothing in another server. A backup from before the clock existed has no rows. While the
    party is split (#1607) the backup also carries the groups (position, name, time, last
    long rest) and which one was picked; a backup from before that has none and means one
    clock."""

    name = "game_clock"
    _KEYS = frozenset({"minute", "last_long_rest"})
    _OPTIONAL = frozenset({"groups", "picked", "log"})
    _LOG_KEYS = frozenset({"position", "at", "minute"})
    # A clock changes a few hundred times a session, so this is far above any real log. DMbot
    # writes its backups without a cap, so a longer one is loaded too: only the newest rows are
    # kept, and the oldest moments then read as "not known" (never a refused backup).
    _LOG_MAX = 200_000
    _GROUP_KEYS = frozenset({"position", "name", "minute", "last_long_rest"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        stored = await ClockStore._read(conn, guild_id, campaign_id, lock=False)
        if stored is None:
            return []
        main = await ClockStore._read_main(conn, guild_id, campaign_id)
        row: dict[str, Any] = {"minute": main.minute, "last_long_rest": main.last_long_rest}
        if stored.split:
            row["groups"] = [
                {
                    "position": g.position,
                    "name": g.name,
                    "minute": g.clock.minute,
                    "last_long_rest": g.clock.last_long_rest,
                }
                for g in stored.groups
            ]
            row["picked"] = stored.picked
        cur = await conn.execute(
            "SELECT position, at, minute FROM game_clock_log"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY seq",
            (guild_id, campaign_id),
        )
        log = [
            {"position": r["position"], "at": int(r["at"]), "minute": int(r["minute"])}
            for r in await cur.fetchall()
        ]
        if log:
            row["log"] = log
        return [row]

    def check(self, rows: list[Any]) -> Restored | None:
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise CampaignError(DAMAGED)
        row = rows[0]
        if not set(row) >= self._KEYS or set(row) - self._KEYS - self._OPTIONAL:
            raise CampaignError(DAMAGED)
        clock = self._clock(row)
        groups = self._groups(row.get("groups", []))
        picked = row.get("picked")
        if groups:
            if type(picked) is not int or picked not in {g.position for g in groups}:
                raise CampaignError(DAMAGED)
        elif "picked" in row:
            raise CampaignError(DAMAGED)
        return Restored(
            Clock(clock[0], clock[1], None), groups, picked, self._log(row.get("log", []))
        )

    @staticmethod
    def _clock(row: dict[str, Any]) -> tuple[int, int]:
        minute, rest = row["minute"], row["last_long_rest"]
        for value in (minute, rest):
            if type(value) is not int or not 0 <= value <= game.MAX_MINUTE:
                raise CampaignError(DAMAGED)
        if rest > minute:  # a rest can't be in the future
            raise CampaignError(DAMAGED)
        return minute, rest

    def _log(self, raw: Any) -> tuple[tuple[int | None, int, int], ...]:
        """The clock's log from a file: numbers only, each in range, in order."""
        if not isinstance(raw, list):
            raise CampaignError(DAMAGED)
        out: list[tuple[int | None, int, int]] = []
        for item in raw:
            if not isinstance(item, dict) or set(item) != self._LOG_KEYS:
                raise CampaignError(DAMAGED)
            position, at, minute = item["position"], item["at"], item["minute"]
            if position is not None and (
                type(position) is not int or not 1 <= position <= split.MAX_GROUPS
            ):
                raise CampaignError(DAMAGED)
            if type(at) is not int or not 0 < at <= 4_102_444_800:  # (before the year 2100)
                raise CampaignError(DAMAGED)
            if type(minute) is not int or not 0 <= minute <= game.MAX_MINUTE:
                raise CampaignError(DAMAGED)
            out.append((position, at, minute))
        return tuple(out[-self._LOG_MAX :])

    def _groups(self, raw: Any) -> tuple[Group, ...]:
        if not isinstance(raw, list) or len(raw) == 1 or len(raw) > split.MAX_GROUPS:
            raise CampaignError(DAMAGED)
        out: list[Group] = []
        for item in raw:
            if not isinstance(item, dict) or set(item) != self._GROUP_KEYS:
                raise CampaignError(DAMAGED)
            position, name = item["position"], item["name"]
            if type(position) is not int or not 1 <= position <= split.MAX_GROUPS:
                raise CampaignError(DAMAGED)
            if not isinstance(name, str) or not name or split.clean_name(name) != name:
                raise CampaignError(DAMAGED)
            minute, rest = self._clock(item)
            out.append(Group(position, name, Clock(minute, rest, None)))
        if len({g.position for g in out}) != len(out):
            raise CampaignError(DAMAGED)
        if len({g.name.casefold() for g in out}) != len(out):
            raise CampaignError(DAMAGED)
        return tuple(sorted(out, key=lambda g: g.position))

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        restored = rows if isinstance(rows, Restored) or rows is None else self.check(rows)
        if restored is None:
            return
        clock = restored.clock
        await conn.execute(
            "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
            " tired_told_for, updated_at) VALUES (%s, %s, %s, %s, NULL, %s)"
            " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET minute = EXCLUDED.minute,"
            " last_long_rest = EXCLUDED.last_long_rest, tired_told_for = NULL,"
            " combat_round = NULL, combat_secs = 0,"  # a restored campaign starts out of combat
            " channel_id = NULL, message_id = NULL, players_channel_id = NULL,"
            " players_message_id = NULL, picked_group = NULL, split_phrase_told = FALSE,"
            " split_drift_told = FALSE, split_undo = NULL, updated_at = EXCLUDED.updated_at",
            (guild_id, campaign_id, clock.minute, clock.last_long_rest, int(time.time())),
        )
        await conn.execute(
            "DELETE FROM game_clock_groups WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        for g in restored.groups:
            await conn.execute(
                "INSERT INTO game_clock_groups (guild_id, campaign_id, position, name, minute,"
                " last_long_rest, tired_told_for, combat_round, combat_secs)"
                " VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, 0)",
                (
                    guild_id,
                    campaign_id,
                    g.position,
                    g.name,
                    g.clock.minute,
                    g.clock.last_long_rest,
                ),
            )
        if restored.groups:
            await conn.execute(
                "UPDATE game_clocks SET picked_group = %s WHERE guild_id = %s AND campaign_id = %s",
                (restored.picked, guild_id, campaign_id),
            )
        # The inserts above logged "now" for each clock (the log is a trigger's); a restored
        # campaign keeps the file's own log, or none from a file that had none.
        await conn.execute(
            "DELETE FROM game_clock_log WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        if restored.log:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    [(guild_id, campaign_id, p, at, m) for p, at, m in restored.log],
                )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        # (The groups go with the campaign anyway; cleared here so a replace starts clean.)
        await conn.execute(
            "DELETE FROM game_clock_log WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute(
            "DELETE FROM game_clock_groups WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute(
            "DELETE FROM game_clocks WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
