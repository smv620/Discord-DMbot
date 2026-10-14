"""Shared rulesets, part 1: publish, share codes, follow, unfollow (#1190; docs/PLAN.md,
"Shared rulesets"). The one place rules cross from one campaign to another.

A DM of a paid campaign publishes its house rules as a **set**; another campaign's DM may
**follow** it, in the same server (pick from a list) or from another server (enter a share
code). Only rules cross: a set's content is each rule's text and the "instead of" text, plus
the DM's document link. Never the "what happened" scenario, who made a rule, a session, a
name, a transcript, a note or a sheet; and nothing from a shared rulebook or adventure, which
house rules cannot hold (a rule is plain text the DM wrote, with no source to point at one).

A follower keeps **its own copy** of the version it follows (`rule_set_follows.content`), so
its rules keep working if the set is turned off or deleted, and its backup carries them. Every
table here is isolated to its own server and campaign. The one door across servers is the
database function `dmbot_set_by_code`: a share code is the capability to read one shared set,
so codes are random, long, and wrong guesses are limited.
"""

from __future__ import annotations

import json
import secrets
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from psycopg.types.json import Jsonb

from dmbot.db import Conn, Database
from dmbot.rules.house import HOUSE_RULES_MAX, RULE_MAX, HouseRule, HouseRuleError, require_dm

HOUSE_RULES = "house_rules"
NAME_MAX = 60
CODE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # no 0/1/8/9: easy to read out and type
CODE_LENGTH = 10  # 32**10 is about 10**15: not guessable
# Wrong codes tried from one server: a few, then a wait (a code is the key to a set).
LINK_MAX = 2000
GUESSES = 5
GUESS_WINDOW_S = 600

NO_RULES = "This campaign has no house rules to share yet. Add some first."
NEED_NAME = "Give the set a name (up to 60 characters)."
ALREADY_SHARED = "This campaign already shares its house rules."
NOT_SHARED = "This campaign isn't sharing its house rules."
NO_SUCH_SET = "That set isn't here, or it isn't shared any more."
BAD_CODE = "That code doesn't match a shared set. Check it and try again."
TOO_MANY = "Too many tries. Wait a few minutes, then try again."
OWN_SET = "That set is this campaign's own."
ALREADY_FOLLOWING = "This campaign already follows a set of house rules. Unfollow it first."
NOT_FOLLOWING = "This campaign isn't following a set of house rules."


class RuleSetError(HouseRuleError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class SharedSet:
    """A set as its publisher sees it."""

    id: str
    name: str
    shared: bool
    version: int


@dataclass(frozen=True, slots=True)
class Offer:
    """A shared set as someone thinking of following it sees it: enough to decide, and the
    content it would copy."""

    set_id: str
    kind: str
    name: str
    publisher: str  # the publishing campaign's name
    version: int
    content: dict[str, Any]
    code: str | None = None  # the share code it was found by, so a follow can ask again


@dataclass(frozen=True, slots=True)
class Follow:
    set_id: str
    kind: str
    name: str
    publisher: str
    version: int
    content: dict[str, Any]
    updating: bool  # False: "no longer updated"
    followed_at: int


def content_of(rules: Sequence[HouseRule], link: str | None) -> dict[str, Any]:
    """What a house-rules set carries: each rule's number, text and "instead of", oldest
    first, and the DM's document link. Built field by field, so nothing else of a rule (its
    scenario, who made it, its session) can travel."""
    return {
        "rules": [
            {"number": r.number, "rule": r.rule, "instead": r.supersedes}
            for r in sorted(rules, key=lambda r: r.number)
        ],
        "link": link,
    }


def valid_content(content: Any) -> dict[str, Any]:
    """A set's content as a copy that is only what a set may hold: a list of rules (number,
    text, "instead of") and a link, each within the limits. Anything else is dropped; a shape
    that isn't a set's raises."""
    if not isinstance(content, dict) or not isinstance(content.get("rules"), list):
        raise RuleSetError(NO_SUCH_SET)
    rules = []
    for entry in content["rules"][:HOUSE_RULES_MAX]:
        if not isinstance(entry, dict):
            raise RuleSetError(NO_SUCH_SET)
        number, text, instead = entry.get("number"), entry.get("rule"), entry.get("instead")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not isinstance(text, str)
            or not 0 < len(text) <= RULE_MAX
            or not (instead is None or (isinstance(instead, str) and len(instead) <= RULE_MAX))
        ):
            raise RuleSetError(NO_SUCH_SET)
        rules.append({"number": number, "rule": text, "instead": instead})
    link = content.get("link")
    if link is not None and (not isinstance(link, str) or len(link) > LINK_MAX):
        raise RuleSetError(NO_SUCH_SET)
    return {"rules": rules, "link": link}


def new_code() -> str:
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))


def clean_code(text: str) -> str:
    """A code as typed: any case, spaces or dashes."""
    return "".join(c for c in text.upper() if c.isalnum())


def _json(content: Any) -> dict[str, Any]:
    return cast(dict[str, Any], content if isinstance(content, dict) else json.loads(content))


def _offer(row: dict[str, Any], code: str | None = None) -> Offer:
    return Offer(
        str(row["set_id"]), str(row["kind"]), str(row["set_name"]), str(row["publisher"]),
        int(row["version"]), valid_content(_json(row["content"])), code,
    )  # fmt: skip


def _follow(row: dict[str, Any]) -> Follow:
    return Follow(
        str(row["set_id"]), str(row["kind"]), str(row["set_name"]), str(row["publisher"]),
        int(row["version"]), _json(row["content"]), bool(row["updating"]),
        int(row["followed_at"]),
    )  # fmt: skip


class RuleSetStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock
        self._wrong: dict[tuple[int, int], deque[float]] = {}  # (server, person) -> guesses

    def _now(self) -> int:
        return int(self._clock())

    # ---- publishing -----------------------------------------------------------------

    async def published(self, guild_id: int, campaign_id: str) -> SharedSet | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.id, s.name, s.status, max(v.version) AS version FROM rule_sets s"
                " JOIN rule_set_versions v ON v.guild_id = s.guild_id"
                " AND v.campaign_id = s.campaign_id AND v.set_id = s.id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.kind = %s"
                " GROUP BY s.id, s.name, s.status",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SharedSet(
            str(row["id"]), str(row["name"]), row["status"] == "shared", int(row["version"])
        )

    async def publish(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        name: str,
        rules: Sequence[HouseRule],
        link: str | None,
    ) -> SharedSet:
        """Share the campaign's house rules as a set, version 1. Only a DM of the campaign
        (the paid-plan check is the caller's, with the owner's plan). `rules` and `link` are
        what the DM sees now; only their rule text and "instead of" text are copied."""
        name = " ".join(name.split())
        if not 0 < len(name) <= NAME_MAX:
            raise RuleSetError(NEED_NAME)
        if not rules:
            raise RuleSetError(NO_RULES)
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT id, status FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            old = await cur.fetchone()
            if old is not None and old["status"] == "shared":
                raise RuleSetError(ALREADY_SHARED)
            content = Jsonb(content_of(rules, link))
            if old is not None:  # shared before and stopped: shared again, a new version
                set_id = str(old["id"])
                cur = await conn.execute(
                    "SELECT max(version) AS v FROM rule_set_versions WHERE guild_id = %s"
                    " AND campaign_id = %s AND set_id = %s",
                    (guild_id, campaign_id, set_id),
                )
                version = int((await cur.fetchone() or {"v": 0})["v"] or 0) + 1
                await conn.execute(
                    "UPDATE rule_sets SET status = 'shared', name = %s WHERE guild_id = %s"
                    " AND campaign_id = %s AND id = %s",
                    (name, guild_id, campaign_id, set_id),
                )
            else:
                set_id, version = secrets.token_hex(16), 1
                await conn.execute(
                    "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status,"
                    " created_by, created_at) VALUES (%s, %s, %s, %s, %s, 'shared', %s, %s)",
                    (guild_id, campaign_id, set_id, HOUSE_RULES, name, user_id, now),
                )
            await conn.execute(
                "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version,"
                " content, created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, set_id, version, content, user_id, now),
            )
        return SharedSet(set_id, name, True, version)

    async def stop_sharing(self, guild_id: int, campaign_id: str, user_id: int) -> bool:
        """Turn sharing off: no new follows (every code stops working). Followers keep the
        version they have. False if nothing was shared."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE rule_sets SET status = 'unshared' WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s AND status = 'shared'",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            changed = cur.rowcount > 0
            await conn.execute(
                "UPDATE rule_set_codes SET active = false WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
        return bool(changed)

    async def make_code(self, guild_id: int, campaign_id: str, user_id: int) -> str:
        """A new share code for the campaign's shared set."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT id FROM rule_sets WHERE guild_id = %s AND campaign_id = %s AND kind = %s"
                " AND status = 'shared'",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NOT_SHARED)
            code = new_code()
            await conn.execute(
                "INSERT INTO rule_set_codes (guild_id, campaign_id, code, set_id, active,"
                " created_at) VALUES (%s, %s, %s, %s, true, %s)",
                (guild_id, campaign_id, code, row["id"], self._now()),
            )
        return code

    async def codes(self, guild_id: int, campaign_id: str) -> list[tuple[str, bool]]:
        """The campaign's share codes and whether each works, newest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT code, active FROM rule_set_codes WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY created_at DESC, code",
                (guild_id, campaign_id),
            )
            return [(str(r["code"]), bool(r["active"])) for r in await cur.fetchall()]

    async def turn_off_code(self, guild_id: int, campaign_id: str, user_id: int, code: str) -> bool:
        """Stop one code working (it can't be turned back on: make another). Followers who
        used it keep following."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE rule_set_codes SET active = false WHERE guild_id = %s AND campaign_id = %s"
                " AND code = %s AND active",
                (guild_id, campaign_id, clean_code(code)),
            )
            return bool(cur.rowcount > 0)

    # ---- finding a set to follow ---------------------------------------------------

    async def shared_here(self, guild_id: int, campaign_id: str) -> list[Offer]:
        """The sets other campaigns in this server share, for the "pick from a list" step.
        Read under this server's own isolation, so no other server's set can appear."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                _LATEST + " WHERE s.status = 'shared' AND s.kind = %s"
                " AND NOT (s.guild_id = %s AND s.campaign_id = %s)"
                " ORDER BY lower(s.name), s.id",
                (HOUSE_RULES, guild_id, campaign_id),
            )
            return [_offer(r) for r in await cur.fetchall()]

    async def _door(self, conn: Conn, code: str) -> Offer | None:
        cur = await conn.execute("SELECT * FROM dmbot_set_by_code(%s)", (code,))
        row = await cur.fetchone()
        if row is None:
            return None
        return _offer(
            {"set_id": row["set_id"], "kind": row["kind"], "set_name": row["set_name"],
             "publisher": row["publisher"], "version": row["version"], "content": row["content"]},
            code,
        )  # fmt: skip

    async def by_code(self, guild_id: int, user_id: int, code: str) -> Offer:
        """The shared set a code opens, from any server: its name, its publisher campaign's
        name and the content it would copy. For a DM who is about to confirm following it:
        the caller checks that (this returns the full content). A wrong code counts toward a
        short limit for that person in that server (kept in memory, so it resets on a restart;
        a code is 10 letters from 32, about 10**15, so the limit only slows a determined
        guesser)."""
        key = (guild_id, user_id)
        now = self._clock()
        tries = self._wrong.setdefault(key, deque())
        while tries and now - tries[0] > GUESS_WINDOW_S:
            tries.popleft()
        if len(tries) >= GUESSES:
            raise RuleSetError(TOO_MANY)
        tries.append(now)  # reserved before the database is asked, so parallel guesses count
        wanted = clean_code(code)
        offer = None
        if len(wanted) == CODE_LENGTH:
            async with self._db.guild(guild_id) as conn:
                offer = await self._door(conn, wanted)
        if offer is None:
            raise RuleSetError(BAD_CODE)
        tries.pop()  # a right code isn't a wrong guess
        if not tries:
            del self._wrong[key]
        return offer

    # ---- following ------------------------------------------------------------------

    async def following(self, guild_id: int, campaign_id: str) -> list[Follow]:
        async with self._db.guild(guild_id) as conn:
            return await _followed(conn, guild_id, campaign_id)

    async def follow(self, guild_id: int, campaign_id: str, user_id: int, offer: Offer) -> Follow:
        """Follow the set in `offer` (from `shared_here` or `by_code`, which the DM has just
        confirmed): the campaign takes its own copy. The set is read again here, in the same
        transaction, so a set turned off or changed since the DM looked is not copied from
        the old offer; the DM follows what is shared now. Only a DM of the following
        campaign; one house-rules set at a time."""
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            fresh = await self._reread(conn, offer)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM rule_sets"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, fresh.set_id),
            )
            if await cur.fetchone() is not None:
                raise RuleSetError(OWN_SET)
            if fresh.kind == HOUSE_RULES and any(
                f.kind == HOUSE_RULES for f in await _followed(conn, guild_id, campaign_id)
            ):
                raise RuleSetError(ALREADY_FOLLOWING)
            await conn.execute(
                _INSERT_FOLLOW, _follow_values(guild_id, campaign_id, user_id, now, fresh, 0)
            )
        return Follow(
            fresh.set_id, fresh.kind, fresh.name, fresh.publisher, fresh.version,
            fresh.content, True, now,
        )  # fmt: skip

    async def _reread(self, conn: Conn, offer: Offer) -> Offer:
        """The offer as the set is now, from the same door that made it (a code) or from
        this server's own rows (a pick from the list)."""
        if offer.code is not None:
            fresh = await self._door(conn, offer.code)
        else:
            cur = await conn.execute(
                _LATEST + " WHERE s.id = %s AND s.status = 'shared'", (offer.set_id,)
            )
            row = await cur.fetchone()
            fresh = _offer(row) if row is not None else None
        if fresh is None or fresh.set_id != offer.set_id:
            raise RuleSetError(NO_SUCH_SET)
        return fresh

    async def unfollow(self, guild_id: int, campaign_id: str, user_id: int) -> Follow:
        """Stop following the house-rules set. Returns what was removed, so the caller's Undo
        can `restore` it."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [
                f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOUSE_RULES
            ]
            if not mine:
                raise RuleSetError(NOT_FOLLOWING)
            await conn.execute(
                "DELETE FROM rule_set_follows"
                " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s",
                (guild_id, campaign_id, mine[0].set_id),
            )
        return mine[0]

    async def restore(self, guild_id: int, campaign_id: str, user_id: int, follow: Follow) -> None:
        """Undo an unfollow: the copy comes back as it was (unless the campaign follows
        another set by now)."""
        offer = Offer(
            follow.set_id,
            follow.kind,
            follow.name,
            follow.publisher,
            follow.version,
            follow.content,
        )
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            if any(f.kind == follow.kind for f in await _followed(conn, guild_id, campaign_id)):
                raise RuleSetError(ALREADY_FOLLOWING)
            await conn.execute(
                _INSERT_FOLLOW,
                _follow_values(
                    guild_id, campaign_id, user_id, follow.followed_at, offer, 0, follow.updating
                ),
            )


_LATEST = (
    "SELECT s.id AS set_id, s.kind, s.name AS set_name, c.name AS publisher, v.version,"
    " v.content FROM rule_sets s"
    " JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id"
    " JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id"
    " AND v.set_id = s.id AND v.version = (SELECT max(version) FROM rule_set_versions m"
    "  WHERE m.guild_id = s.guild_id AND m.campaign_id = s.campaign_id AND m.set_id = s.id)"
)
_INSERT_FOLLOW = (
    "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name, publisher,"
    " version, content, position, updating, followed_by, followed_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _follow_values(
    guild_id: int, campaign_id: str, user_id: int, at: int, offer: Offer, position: int,
    updating: bool = True,
) -> tuple[Any, ...]:  # fmt: skip
    return (
        guild_id, campaign_id, offer.set_id, offer.kind, offer.name, offer.publisher,
        offer.version, Jsonb(offer.content), position, updating, user_id, at,
    )  # fmt: skip


async def _followed(conn: Conn, guild_id: int, campaign_id: str) -> list[Follow]:
    cur = await conn.execute(
        "SELECT set_id, kind, set_name, publisher, version, content, updating, followed_at"
        " FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s"
        " ORDER BY position, followed_at, set_id",
        (guild_id, campaign_id),
    )
    return [_follow(r) for r in await cur.fetchall()]
