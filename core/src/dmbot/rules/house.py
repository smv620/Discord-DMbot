"""House rules: a campaign's own rulings (docs/PLAN.md, "House rules"; #865).

Each campaign keeps its own, in the database (never shared between campaigns or servers).
A rule is a short text, optionally the book rule it replaces ("instead of") and what
happened to make it (the scenario). The first part builds the store and `/dmbot
houserules`; the rules advisor, voice and alerts build on it later.

**A rule's number is its own, for good.** It is the campaign's next number when the rule
is made, it is never used twice (not even after the rule is removed), and backups carry
it, with the count of numbers used, so a copy goes on where the campaign was. So "house
rule 12" means the same rule today and next month, which alerts rely on.

Only the campaign's DMs may change anything: every change checks that in the same
transaction, so a caller that forgets still can't write. Anyone in the server may list a
campaign's house rules. Every query filters on the server and the campaign, and runs
inside `Database.guild`, where row-level security hides every other server's rows too.

Two DMs may look at the same rule. Each rule counts its changes (`version`), and an edit
or a removal says which version the DM was shown: if another DM changed it meanwhile,
nothing is done and the DM is told, rather than one silently undoing the other.

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
INT32_MAX = 2**31 - 1  # numbers are INTEGER
NUMBER_MAX = INT32_MAX - 1  # so that the next number (one more) still fits
INT64_MAX = 2**63 - 1

NO_CAMPAIGN = "That campaign isn't here any more. Use `/dmbot houserules` to see the others."
NOT_DM = (
    "Only this campaign's DMs can change its house rules. You can still read them with "
    "`/dmbot houserules`."
)
GONE = "That house rule isn't here any more."
CHANGED = "Another DM changed that house rule while you were looking."
EMPTY = "The rule box was empty, so nothing was saved."
FULL = (
    f"This campaign already has {HOUSE_RULES_MAX} house rules, so yours wasn't saved. "
    "Remove one first, then add it again."
)
DAMAGED = "This backup file is damaged (bad house rule)."
RULE_BOX = "the rule"
INSTEAD_BOX = "“Instead of”"
HAPPENED_BOX = "what happened"


class HouseRuleError(CampaignError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class HouseRule:
    number: int  # its own, for good (see the module's note)
    campaign_id: str
    rule: str
    supersedes: str | None  # the book rule it replaces, as the DM wrote it
    scenario: str | None  # what happened to make it
    session_id: str | None  # the stored transcript session it came from, if any
    created_by: int
    created_at: int
    updated_at: int
    version: int = 1  # counts changes to this rule, for `unchanged_since`


def clean_rule(raw: str) -> str:
    """The rule, on one line. Raises HouseRuleError if empty or too long."""
    text = " ".join(raw.split())
    if not text:
        raise HouseRuleError(EMPTY)
    if len(text) > RULE_MAX:
        raise HouseRuleError(
            f"That's too long, so nothing was saved. Keep {RULE_BOX} under {RULE_MAX} characters."
        )
    return text


def clean_optional(raw: str | None, box: str) -> str | None:
    """An optional text: None when left empty. Raises HouseRuleError if too long; `box`
    names the box it was typed in, for the message."""
    text = " ".join((raw or "").split())
    if len(text) > RULE_MAX:
        raise HouseRuleError(
            f"That's too long, so nothing was saved. Keep {box} under {RULE_MAX} characters."
        )
    return text or None


def _rule(row: dict[str, Any]) -> HouseRule:
    return HouseRule(
        int(row["number"]),
        row["campaign_id"],
        row["rule"],
        row["supersedes"],
        row["scenario"],
        row["session_id"],
        int(row["created_by"]),
        int(row["created_at"]),
        int(row["updated_at"]),
        int(row["version"]),
    )


_COLUMNS = (
    "number, campaign_id, rule, supersedes, scenario, session_id, created_by, created_at,"
    " updated_at, version"
)


class HouseRuleStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def list(self, guild_id: int, campaign_id: str) -> list[HouseRule]:
        """The campaign's house rules, newest first (the highest number first). Anyone may
        list them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM house_rules WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY number DESC",
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
        wanted: int | None = None,
    ) -> HouseRule:
        """Save a new house rule, with the campaign's next number, or with `wanted` (a
        rule from the house-rules file, #969) if that number was never used: it is above
        every number the campaign has made, and not a leap: within `HOUSE_RULES_MAX` of the
        highest (one stray number in a file must not use up the campaign's numbers). Only the
        campaign's DMs; at most `HOUSE_RULES_MAX`."""
        text = clean_rule(rule)
        instead = clean_optional(supersedes, INSTEAD_BOX)
        happened = clean_optional(scenario, HAPPENED_BOX)
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            # Locking the campaign makes two adds at once take turns: the limit holds, and
            # each gets its own number.
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            if row["n"] >= HOUSE_RULES_MAX:
                raise HouseRuleError(FULL)
            cur = await conn.execute(
                "UPDATE campaigns SET house_rules_made = CASE"
                " WHEN %s::integer IS NOT NULL AND %s::integer > house_rules_made"
                " AND %s::integer <= house_rules_made + %s THEN %s::integer"
                " ELSE house_rules_made + 1 END"
                " WHERE guild_id = %s AND id = %s RETURNING house_rules_made",
                (wanted, wanted, wanted, HOUSE_RULES_MAX, wanted, guild_id, campaign_id),
            )
            made = await cur.fetchone()
            assert made is not None
            cur = await conn.execute(
                "INSERT INTO house_rules (guild_id, campaign_id, number, rule, supersedes,"
                " scenario, session_id, created_by, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (
                    guild_id,
                    campaign_id,
                    made["house_rules_made"],
                    text,
                    instead,
                    happened,
                    session_id,
                    user_id,
                    now,
                    now,
                ),
            )
            saved = await cur.fetchone()
            assert saved is not None
            return _rule(saved)

    async def edit(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        number: int,
        rule: str,
        supersedes: str | None = None,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        """Change a rule's words and what it replaces. Only the campaign's DMs.
        `unchanged_since`: the rule's `version` as the DM was shown it; if another DM
        changed it meanwhile, nothing is saved (`CHANGED`)."""
        text = clean_rule(rule)
        instead = clean_optional(supersedes, INSTEAD_BOX)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "UPDATE house_rules SET rule = %s, supersedes = %s, updated_at = %s,"
                " version = version + 1"
                " WHERE guild_id = %s AND campaign_id = %s AND number = %s"
                " AND (%s::int IS NULL OR version = %s)"
                f" RETURNING {_COLUMNS}",
                (
                    text,
                    instead,
                    int(self._clock()),
                    guild_id,
                    campaign_id,
                    number,
                    unchanged_since,
                    unchanged_since,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, number)
            return _rule(row)

    async def remove(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        number: int,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        """Take a rule away; returns it. Only the campaign's DMs. `unchanged_since`: as for
        `edit`. Its number is not used again."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM house_rules WHERE guild_id = %s AND campaign_id = %s"
                " AND number = %s AND (%s::int IS NULL OR version = %s)"
                f" RETURNING {_COLUMNS}",
                (guild_id, campaign_id, number, unchanged_since, unchanged_since),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, number)
            return _rule(row)


async def _missing(conn: Conn, guild_id: int, campaign_id: str, number: int) -> HouseRuleError:
    """Why a change touched no rule: it's gone, or another DM changed it (it's still here)."""
    cur = await conn.execute(
        "SELECT 1 AS ok FROM house_rules WHERE guild_id = %s AND campaign_id = %s AND number = %s",
        (guild_id, campaign_id, number),
    )
    return HouseRuleError(GONE if await cur.fetchone() is None else CHANGED)


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
        raise HouseRuleError(NO_CAMPAIGN)
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
        " AND user_id = %s",
        (guild_id, campaign_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HouseRuleError(NOT_DM)


# ---- backups ------------------------------------------------------------------------

_KEYS = frozenset({"number", "rule", "instead", "scenario", "by", "created_at", "updated_at"})
_MADE_KEYS = frozenset({"made"})  # the first row, when the campaign has made any rule


def _timestamp(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
        return value
    raise CampaignError(DAMAGED)


def _number(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= NUMBER_MAX:
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

    number: int
    rule: str
    supersedes: str | None
    scenario: str | None
    created_by: int
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class _Loaded:
    """A backup's house rules after `check`: the rules, and how many numbers the campaign
    had used (a backup from before this was recorded has none: the highest will do)."""

    made: int | None
    rules: list[_Checked]


class HouseRulesSection:
    """House rules in campaign backups (an `ExportSection`). Rules keep their numbers, and
    a first row `{"made": N}` says how many numbers the campaign had used, so the copy
    never gives a new rule a number an old one (since removed) had."""

    name = "house_rules"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT house_rules_made FROM campaigns WHERE guild_id = %s AND id = %s",
            (guild_id, campaign_id),
        )
        made = await cur.fetchone()
        cur = await conn.execute(
            "SELECT number, rule, supersedes, scenario, created_by, created_at, updated_at"
            " FROM house_rules WHERE guild_id = %s AND campaign_id = %s ORDER BY number",
            (guild_id, campaign_id),
        )
        rows: list[Any] = [
            {
                "number": int(r["number"]),
                "rule": r["rule"],
                "instead": r["supersedes"],
                "scenario": r["scenario"],
                "by": str(r["created_by"]),
                "created_at": int(r["created_at"]),
                "updated_at": int(r["updated_at"]),
            }
            for r in await cur.fetchall()
        ]
        if made is not None and made["house_rules_made"] > 0:
            rows.insert(0, {"made": int(made["house_rules_made"])})
        return rows

    def check(self, rows: list[Any]) -> _Loaded:
        """Every row, validated; nothing is written until all of them are right."""
        made: int | None = None
        if rows and isinstance(rows[0], dict) and set(rows[0]) == _MADE_KEYS:
            made = _number(rows[0]["made"])
            rows = rows[1:]
        if len(rows) > HOUSE_RULES_MAX:
            raise CampaignError(DAMAGED)
        checked = []
        numbers: set[int] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != _KEYS:
                raise CampaignError(DAMAGED)
            number = _number(row["number"])
            if number in numbers:  # two rules can't share a number
                raise CampaignError(DAMAGED)
            numbers.add(number)
            rule = _text(row["rule"], optional=False)
            assert rule is not None
            created, updated = _timestamp(row["created_at"]), _timestamp(row["updated_at"])
            if updated < created:
                raise CampaignError(DAMAGED)
            checked.append(
                _Checked(
                    number,
                    rule,
                    _text(row["instead"], optional=True),
                    _text(row["scenario"], optional=True),
                    _person(row["by"]),
                    created,
                    updated,
                )
            )
        if made is not None and numbers and made < max(numbers):
            raise CampaignError(DAMAGED)  # it can't have used fewer numbers than it has
        return _Loaded(made, checked)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """`rows` as `check` returned them. The store calls `check` first (off the event
        loop, before its transaction) and passes the result here; a caller that has only
        the file's rows (a test, say) gets them checked here, so nothing unchecked is
        ever written."""
        loaded = rows if isinstance(rows, _Loaded) else self.check(rows)
        if loaded.rules:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO house_rules (guild_id, campaign_id, number, rule, supersedes,"
                    " scenario, created_by, created_at, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            guild_id,
                            campaign_id,
                            r.number,
                            r.rule,
                            r.supersedes,
                            r.scenario,
                            r.created_by,
                            r.created_at,
                            r.updated_at,
                        )
                        for r in loaded.rules
                    ],
                )
        # The next number comes after every one the campaign had used. (Replacing a
        # campaign keeps its own count if that is higher, so a number it once used is not
        # used again.)
        used = loaded.made or max((r.number for r in loaded.rules), default=0)
        if used:
            await conn.execute(
                "UPDATE campaigns SET house_rules_made = GREATEST(house_rules_made, %s)"
                " WHERE guild_id = %s AND id = %s",
                (used, guild_id, campaign_id),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
