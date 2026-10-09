"""House rules: a campaign's own rulings (docs/PLAN.md, "House rules"; #865).

Each campaign keeps its own, in the database (never shared between campaigns or servers).
A rule is a short text, optionally the book rule it replaces ("instead of") and what
happened to make it (the scenario). The first part builds the store and `/dmbot
houserules`; the rules advisor, voice and alerts build on it later.

Only the campaign's DMs may change anything: every change checks that in the same
transaction, so a caller that forgets still can't write. Anyone in the server may list a
campaign's house rules. Every query filters on the server and the campaign, and runs
inside `Database.guild`, where row-level security hides every other server's rows too.

Backups carry them (`HouseRulesSection`): validated field by field, as the file is
untrusted. The stored transcript session a rule came from is never exported: it means
nothing in another server.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

RULE_MAX = 500  # the rule, "instead of" and the scenario each; matches the table's checks
HOUSE_RULES_MAX = 200  # per campaign, so a campaign (and its backup) stays a sensible size
INT64_MAX = 2**63 - 1

NO_CAMPAIGN = "That campaign isn't here any more."
NOT_DM = "Only this campaign's DM can change its house rules."
GONE = "That house rule isn't here any more."
EMPTY = "Please type the rule."
FULL = f"This campaign already has {HOUSE_RULES_MAX} house rules. Remove one first."
DAMAGED = "This backup file is damaged (bad house rule)."


class HouseRuleError(CampaignError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class HouseRule:
    id: int
    campaign_id: str
    rule: str
    supersedes: str | None  # the book rule it replaces, as the DM wrote it
    scenario: str | None  # what happened to make it
    session_id: str | None  # the stored transcript session it came from, if any
    created_by: int
    created_at: int
    updated_at: int


def clean_rule(raw: str) -> str:
    """The rule, on one line. Raises HouseRuleError if empty or too long."""
    text = " ".join(raw.split())
    if not text:
        raise HouseRuleError(EMPTY)
    if len(text) > RULE_MAX:
        raise HouseRuleError(f"That's too long. Keep the rule under {RULE_MAX} characters.")
    return text


def clean_optional(raw: str | None, what: str) -> str | None:
    """An optional text: None when left empty. Raises HouseRuleError if too long."""
    text = " ".join((raw or "").split())
    if len(text) > RULE_MAX:
        raise HouseRuleError(f"That's too long. Keep {what} under {RULE_MAX} characters.")
    return text or None


def _rule(row: dict[str, Any]) -> HouseRule:
    return HouseRule(
        int(row["id"]),
        row["campaign_id"],
        row["rule"],
        row["supersedes"],
        row["scenario"],
        row["session_id"],
        int(row["created_by"]),
        int(row["created_at"]),
        int(row["updated_at"]),
    )


_COLUMNS = (
    "id, campaign_id, rule, supersedes, scenario, session_id, created_by, created_at, updated_at"
)


class HouseRuleStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def list(self, guild_id: int, campaign_id: str) -> list[HouseRule]:
        """The campaign's house rules, newest first. Anyone may list them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM house_rules WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY created_at DESC, id DESC",
                (guild_id, campaign_id),
            )
            return [_rule(r) for r in await cur.fetchall()]

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        rule: str,
        supersedes: str | None = None,
        *,
        scenario: str | None = None,
        session_id: str | None = None,
    ) -> HouseRule:
        """Save a new house rule. Only the campaign's DMs; at most `HOUSE_RULES_MAX`."""
        text = clean_rule(rule)
        instead = clean_optional(supersedes, "that")
        happened = clean_optional(scenario, "that")
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            # Locking the campaign makes two adds at once count each other.
            await _require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            if row["n"] >= HOUSE_RULES_MAX:
                raise HouseRuleError(FULL)
            cur = await conn.execute(
                "INSERT INTO house_rules (guild_id, campaign_id, rule, supersedes, scenario,"
                " session_id, created_by, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (guild_id, campaign_id, text, instead, happened, session_id, user_id, now, now),
            )
            saved = await cur.fetchone()
            assert saved is not None
            return _rule(saved)

    async def edit(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        rule_id: int,
        rule: str,
        supersedes: str | None = None,
    ) -> HouseRule:
        """Change a rule's words and what it replaces. Only the campaign's DMs."""
        text = clean_rule(rule)
        instead = clean_optional(supersedes, "that")
        async with self._db.guild(guild_id) as conn:
            await _require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "UPDATE house_rules SET rule = %s, supersedes = %s, updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                f" RETURNING {_COLUMNS}",
                (text, instead, int(self._clock()), guild_id, campaign_id, rule_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise HouseRuleError(GONE)
            return _rule(row)

    async def remove(
        self, guild_id: int, campaign_id: str, user_id: int, rule_id: int
    ) -> HouseRule:
        """Take a rule away; returns it. Only the campaign's DMs."""
        async with self._db.guild(guild_id) as conn:
            await _require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM house_rules WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                f" RETURNING {_COLUMNS}",
                (guild_id, campaign_id, rule_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise HouseRuleError(GONE)
            return _rule(row)


async def _require_dm(
    conn: Conn, guild_id: int, campaign_id: str, user_id: int, *, lock: bool = False
) -> None:
    """The campaign exists in this server, and this person is one of its DMs."""
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaigns WHERE guild_id = %s AND id = %s"
        + (" FOR UPDATE" if lock else ""),
        (guild_id, campaign_id),
    )
    if await cur.fetchone() is None:
        raise HouseRuleError(NO_CAMPAIGN)
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
        " AND user_id = %s",
        (guild_id, campaign_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HouseRuleError(NOT_DM)


# ---- backups ------------------------------------------------------------------------

_KEYS = frozenset({"rule", "instead", "scenario", "by", "created_at", "updated_at"})


def _timestamp(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
        return value
    raise CampaignError(DAMAGED)


def _text(value: object, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise CampaignError(DAMAGED)
    try:
        cleaned = clean_optional(value, "that") if optional else clean_rule(value)
    except HouseRuleError:
        raise CampaignError(DAMAGED) from None
    if cleaned != value:  # a file made by DMbot is already in its cleaned form
        raise CampaignError(DAMAGED)
    return cleaned


def _person(value: object) -> int:
    digits = isinstance(value, str) and value.isascii() and value.isdigit()
    if not digits or not 0 < int(str(value)) <= INT64_MAX:
        raise CampaignError(DAMAGED)
    return int(str(value))


@dataclass(frozen=True, slots=True)
class _Checked:
    """One backup row after `check`: every field validated."""

    rule: str
    supersedes: str | None
    scenario: str | None
    created_by: int
    created_at: int
    updated_at: int


class HouseRulesSection:
    """House rules in campaign backups (an `ExportSection`)."""

    name = "house_rules"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT rule, supersedes, scenario, created_by, created_at, updated_at"
            " FROM house_rules WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY created_at, id",
            (guild_id, campaign_id),
        )
        return [
            {
                "rule": r["rule"],
                "instead": r["supersedes"],
                "scenario": r["scenario"],
                "by": str(r["created_by"]),
                "created_at": int(r["created_at"]),
                "updated_at": int(r["updated_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        """Every row, validated; nothing is written until all of them are right."""
        if len(rows) > HOUSE_RULES_MAX:
            raise CampaignError(DAMAGED)
        checked = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != _KEYS:
                raise CampaignError(DAMAGED)
            rule = _text(row["rule"], optional=False)
            assert rule is not None
            created, updated = _timestamp(row["created_at"]), _timestamp(row["updated_at"])
            if updated < created:
                raise CampaignError(DAMAGED)
            checked.append(
                _Checked(
                    rule,
                    _text(row["instead"], optional=True),
                    _text(row["scenario"], optional=True),
                    _person(row["by"]),
                    created,
                    updated,
                )
            )
        return checked

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """`rows` as `check` returned them, or straight from a file (checked here)."""
        checked = rows if all(isinstance(r, _Checked) for r in rows) else self.check(rows)
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO house_rules (guild_id, campaign_id, rule, supersedes, scenario,"
                " created_by, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        guild_id,
                        campaign_id,
                        r.rule,
                        r.supersedes,
                        r.scenario,
                        r.created_by,
                        r.created_at,
                        r.updated_at,
                    )
                    for r in checked
                ],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
