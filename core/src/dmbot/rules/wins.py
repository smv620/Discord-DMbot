"""Which of two house rules that disagree wins (#2284), as the DM decided.

When a saved rule disagrees with another (`dmbot.rules.conflicts`) the DM keeps both and says which
one wins, or the sidebar meets two disagreeing rules and asks. The answer is stored here, once for
the pair, so it is never asked again. A side is a rule of the campaign's own or a rule of a set it
follows (the set's name and the rule's number in it): a followed set's rule can share a number with
the campaign's, so the set is part of the key. Only the DM's answer is ever stored; DMbot never
picks. Precedence between sources (house rules, homebrew, the books) is unchanged.

Backups carry the pairs (`WinsSection`), validated field by field, as the file is untrusted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

MAX_PAIRS = 2000
SET_MAX = 120
DAMAGED = "This backup file is damaged (bad house-rule choices)."
INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True, order=True)
class RuleRef:
    """One side of a pair: `set` is "" for the campaign's own rule."""

    set: str
    number: int


@dataclass(frozen=True, slots=True)
class Win:
    winner: RuleRef
    loser: RuleRef
    decided_by: int
    decided_at: int


def _sides(first: RuleRef, second: RuleRef) -> tuple[RuleRef, RuleRef]:
    return (first, second) if first < second else (second, first)


class WinsStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def record(
        self, guild_id: int, campaign_id: str, winner: RuleRef, loser: RuleRef, by: int
    ) -> None:
        """The DM's answer for this pair. A second answer replaces the first."""
        if winner == loser:
            raise ValueError("A rule cannot win over itself")
        a, b = _sides(winner, loser)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO house_rule_wins (guild_id, campaign_id, a_set, a_number, b_set,"
                " b_number, winner, decided_by, decided_at) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                " %s, %s) ON CONFLICT (guild_id, campaign_id, a_set, a_number, b_set, b_number)"
                " DO UPDATE SET winner = EXCLUDED.winner, decided_by = EXCLUDED.decided_by,"
                " decided_at = EXCLUDED.decided_at",
                (
                    guild_id,
                    campaign_id,
                    a.set,
                    a.number,
                    b.set,
                    b.number,
                    "a" if winner == a else "b",
                    by,
                    int(self._clock()),
                ),
            )

    async def all(self, guild_id: int, campaign_id: str) -> list[Win]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT a_set, a_number, b_set, b_number, winner, decided_by, decided_at"
                " FROM house_rule_wins WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY a_set, a_number, b_set, b_number",
                (guild_id, campaign_id),
            )
            return [_win(r) for r in await cur.fetchall()]

    async def winner_of(
        self, guild_id: int, campaign_id: str, first: RuleRef, second: RuleRef
    ) -> RuleRef | None:
        """Which of the two the DM said wins, or None if they have not said."""
        a, b = _sides(first, second)
        for win in await self.all(guild_id, campaign_id):
            if (win.winner, win.loser) in ((a, b), (b, a)):
                return win.winner
        return None

    async def forget_own_rule(self, guild_id: int, campaign_id: str, number: int) -> int:
        """A rule of the campaign's own was removed or changed: what was decided about it no
        longer holds. Returns how many pairs went."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM house_rule_wins WHERE guild_id = %s AND campaign_id = %s AND"
                " ((a_set = '' AND a_number = %s) OR (b_set = '' AND b_number = %s))",
                (guild_id, campaign_id, number, number),
            )
            return int(cur.rowcount)


def _win(row: dict[str, Any]) -> Win:
    a = RuleRef(row["a_set"], int(row["a_number"]))
    b = RuleRef(row["b_set"], int(row["b_number"]))
    winner, loser = (a, b) if row["winner"] == "a" else (b, a)
    return Win(winner, loser, int(row["decided_by"]), int(row["decided_at"]))


_KEYS = frozenset({"winner", "loser", "by", "at"})
_SIDE_KEYS = frozenset({"set", "number"})


def _side(raw: object) -> RuleRef:
    if not isinstance(raw, dict) or set(raw) != _SIDE_KEYS:
        raise CampaignError(DAMAGED)
    name, number = raw["set"], raw["number"]
    if (
        not isinstance(name, str)
        or len(name) > SET_MAX
        or isinstance(number, bool)
        or not isinstance(number, int)
        or not 0 < number <= INT32_MAX
    ):
        raise CampaignError(DAMAGED)
    return RuleRef(name, number)


class WinsSection:
    """The pairs in campaign backups (an `ExportSection`)."""

    name = "house_rule_wins"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT a_set, a_number, b_set, b_number, winner, decided_by, decided_at"
            " FROM house_rule_wins WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY a_set, a_number, b_set, b_number",
            (guild_id, campaign_id),
        )
        out: list[Any] = []
        for r in await cur.fetchall():
            win = _win(r)
            out.append(
                {
                    "winner": {"set": win.winner.set, "number": win.winner.number},
                    "loser": {"set": win.loser.set, "number": win.loser.number},
                    "by": str(win.decided_by),
                    "at": win.decided_at,
                }
            )
        return out

    def check(self, rows: list[Any]) -> list[Win]:
        """Every pair, validated; nothing is written until all of them are right."""
        if not isinstance(rows, list) or len(rows) > MAX_PAIRS:
            raise CampaignError(DAMAGED)
        out: list[Win] = []
        seen: set[tuple[RuleRef, RuleRef]] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != _KEYS:
                raise CampaignError(DAMAGED)
            winner, loser = _side(row["winner"]), _side(row["loser"])
            by, at = row["by"], row["at"]
            digits = isinstance(by, str) and by.isascii() and by.isdigit()
            if (
                winner == loser
                or not digits
                or not 0 < int(str(by)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
                or _sides(winner, loser) in seen
            ):
                raise CampaignError(DAMAGED)
            seen.add(_sides(winner, loser))
            out.append(Win(winner, loser, int(str(by)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        wins = rows if _is_checked(rows) else self.check(rows)
        for win in wins:
            a, b = _sides(win.winner, win.loser)
            await conn.execute(
                "INSERT INTO house_rule_wins (guild_id, campaign_id, a_set, a_number, b_set,"
                " b_number, winner, decided_by, decided_at) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                " %s, %s)",
                (
                    guild_id,
                    campaign_id,
                    a.set,
                    a.number,
                    b.set,
                    b.number,
                    "a" if win.winner == a else "b",
                    win.decided_by,
                    win.decided_at,
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM house_rule_wins WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, Win) for r in rows)
