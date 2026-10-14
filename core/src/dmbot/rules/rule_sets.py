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
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from psycopg.types.json import Jsonb

from dmbot.db import Conn, Database
from dmbot.rules import set_diff
from dmbot.rules.house import (
    HOUSE_RULES_MAX,
    RULE_MAX,
    HouseRule,
    HouseRuleError,
    insert_rule,
    require_dm,
)

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
NO_CHANGES = "Nothing is different from the last version, so no new version was made."
NOT_EDITOR = "Only this set's DMs and the people they named can change it."
EDITOR_NOT_DM = "A co-editor has to be a DM of the campaign that shares the set."
NOT_PAID = "Changing a shared set needs the campaign to be on a paid plan."
NOTHING_NEW = "There is nothing new to take."
NOTHING_TO_UNDO = "There is nothing to undo."
CHANGED_AGAIN = "The set changed again while you were looking. Look at the new changes first."
NO_ROOM = "Your own house rules and this set together are more than {n} rules. Remove some first."


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
    code: str | None = None  # the share code it was followed by, to ask for updates
    previous_version: int | None = None  # the copy before the last update, for Undo
    previous_content: dict[str, Any] | None = None
    declined_version: int | None = None  # an Undo sent this version back: not taken again


@dataclass(frozen=True, slots=True)
class Update:
    """A newer version of a followed set, and what differs from the follower's copy."""

    follow: Follow
    offer: Offer
    changes: tuple[set_diff.Change, ...]


@dataclass(frozen=True, slots=True)
class Taken:
    """What taking an update did, so the DM's Undo can put it back. `merged`: the campaign
    stopped following and the set's rules became its own (numbers in `added`)."""

    merged: bool
    before: Follow
    after_version: int
    added: tuple[int, ...] = ()
    marker: str = ""
    offered: int = 0  # the version of the set that was on offer (an Undo declines it)


@dataclass(frozen=True, slots=True)
class VersionLog:
    version: int
    by: int
    at: int
    source: str


def content_of(rules: Sequence[HouseRule], link: str | None) -> dict[str, Any]:
    """What a house-rules set carries: each rule's number, text and "instead of", oldest
    first, and the DM's document link. Built field by field, so nothing else of a rule (its
    scenario, who made it, its session) can travel."""
    return {
        "rules": [
            {"number": r.number, "rule": r.rule, "instead": r.supersedes}
            for r in sorted(rules, key=lambda r: r.number)
            if not r.from_set  # rules merged in from another set are not this campaign's to share
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
        int(row["followed_at"]), row.get("code"), row.get("previous_version"),
        _json(row["previous_content"]) if row.get("previous_content") else None,
        row.get("declined_version"),
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
        """Stop one code working for NEW followers (it can't be turned back on: make another).
        Tables that followed before keep getting updates until the publisher stops sharing
        (owner decision): updates are asked for through `dmbot_set_update_by_code`, which
        still answers a code turned off after the follow."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE rule_set_codes SET active = false, deactivated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND code = %s AND active",
                (self._now(), guild_id, campaign_id, clean_code(code)),
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
            fresh.content, True, now, fresh.code,
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
            follow.code,
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

    # ---- the publisher's side: new versions, co-editors, the log --------------------

    async def _may_edit(
        self, conn: Conn, guild_id: int, campaign_id: str, user_id: int, set_id: str
    ) -> None:
        """A DM of the publishing campaign, or someone its DMs named, may change the set."""
        try:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return
        except HouseRuleError:
            pass
        cur = await conn.execute(
            "SELECT 1 AS ok FROM rule_set_editors WHERE guild_id = %s AND campaign_id = %s"
            " AND set_id = %s AND user_id = %s",
            (guild_id, campaign_id, set_id, user_id),
        )
        if await cur.fetchone() is None:
            raise RuleSetError(NOT_EDITOR)

    async def save_version(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        content: Any,
        source: Literal["publisher", "suggestion", "document"] = "publisher",
        *,
        plan_ok: bool,
    ) -> SharedSet:
        """Make `content` the set's next version. Only the publisher's DMs and its named
        co-editors; the set must be shared; nothing is made if nothing differs. Followers
        see it at their next session start, never sooner. `plan_ok`: the caller has checked the
        publishing campaign is still on a paid plan (the owner's plan, which this store can't
        read); without it nothing is saved."""
        if not plan_ok:
            raise RuleSetError(NOT_PAID)
        wanted = valid_content(content)
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            # The campaign row is locked first, for DMs and co-editors alike and in the same
            # order as every other writer, so two saves take turns and never both make N+1.
            await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            cur = await conn.execute(
                "SELECT id, name FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s AND status = 'shared'",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NOT_SHARED)
            set_id = str(row["id"])
            await self._may_edit(conn, guild_id, campaign_id, user_id, set_id)
            cur = await conn.execute(
                "SELECT version, content FROM rule_set_versions WHERE guild_id = %s"
                " AND campaign_id = %s AND set_id = %s ORDER BY version DESC LIMIT 1",
                (guild_id, campaign_id, set_id),
            )
            last = await cur.fetchone()
            assert last is not None  # a set always has version 1
            if valid_content(_json(last["content"])) == wanted:
                raise RuleSetError(NO_CHANGES)
            version = int(last["version"]) + 1
            await conn.execute(
                "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version,"
                " content, created_by, created_at, source)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, set_id, version, Jsonb(wanted), user_id, now, source),
            )
        return SharedSet(set_id, str(row["name"]), True, version)

    async def publish_changes(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        rules: Sequence[HouseRule],
        link: str | None,
        *,
        plan_ok: bool,
    ) -> SharedSet:
        """The publisher's current house rules as the set's next version. `plan_ok`: see
        `save_version`."""
        return await self.save_version(
            guild_id, campaign_id, user_id, content_of(rules, link), "publisher", plan_ok=plan_ok
        )

    async def add_editor(
        self, guild_id: int, campaign_id: str, user_id: int, editor_id: int
    ) -> None:
        """Name a co-editor. A publisher action: only the publishing campaign's DMs."""
        if editor_id <= 0:
            raise RuleSetError(NOT_EDITOR)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s",
                (guild_id, campaign_id, editor_id),
            )
            if await cur.fetchone() is None:  # co-editors are DMs of the publishing campaign
                raise RuleSetError(EDITOR_NOT_DM)
            cur = await conn.execute(
                "SELECT id FROM rule_sets WHERE guild_id = %s AND campaign_id = %s AND kind = %s",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NOT_SHARED)
            await conn.execute(
                "INSERT INTO rule_set_editors (guild_id, campaign_id, set_id, user_id, added_by,"
                " added_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, row["id"], editor_id, user_id, self._now()),
            )

    async def remove_editor(
        self, guild_id: int, campaign_id: str, user_id: int, editor_id: int
    ) -> bool:
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "DELETE FROM rule_set_editors WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s",
                (guild_id, campaign_id, editor_id),
            )
            return bool(cur.rowcount > 0)

    async def editors(self, guild_id: int, campaign_id: str) -> list[int]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT user_id FROM rule_set_editors WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY added_at, user_id",
                (guild_id, campaign_id),
            )
            return [int(r["user_id"]) for r in await cur.fetchall()]

    async def version_log(self, guild_id: int, campaign_id: str) -> list[VersionLog]:
        """Each version of the campaign's set: who made it, when and how, newest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT v.version, v.created_by, v.created_at, v.source FROM rule_set_versions v"
                " JOIN rule_sets s ON s.guild_id = v.guild_id AND s.campaign_id = v.campaign_id"
                " AND s.id = v.set_id WHERE v.guild_id = %s AND v.campaign_id = %s AND s.kind = %s"
                " ORDER BY v.version DESC",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            return [
                VersionLog(
                    int(r["version"]), int(r["created_by"]), int(r["created_at"]), str(r["source"])
                )
                for r in await cur.fetchall()
            ]

    # ---- the follower's side: updates at session start --------------------------------

    async def _latest_of(self, conn: Conn, follow: Follow) -> tuple[Offer | None, bool]:
        """The set's newest version as this follower can see it, and whether the set is known
        to be gone. Through the share code it followed by (the update door: a code turned off
        AFTER the follow still answers), or by the set's id when it is in this server. (None,
        True): the publisher stopped sharing. (None, False): not known, never "gone": a follow
        from another server with no code, say, can't be told apart from a set we can't see."""
        if follow.code is not None:
            cur = await conn.execute(
                "SELECT * FROM dmbot_set_update_by_code(%s, %s)", (follow.code, follow.followed_at)
            )
            row = await cur.fetchone()
            if row is None:
                return None, True
            fresh = _offer(
                {k: row[k] for k in ("set_id", "kind", "set_name", "publisher", "version")}
                | {"content": row["content"]},
                follow.code,
            )
            return (fresh, False) if fresh.set_id == follow.set_id else (None, True)
        cur = await conn.execute(_LATEST_WITH_STATUS + " WHERE s.id = %s", (follow.set_id,))
        row = await cur.fetchone()
        if row is None:
            return None, False  # not in this server: unknown
        if row["status"] != "shared":
            return None, True
        return _offer(row), False

    async def pending_update(
        self, guild_id: int, campaign_id: str, *, for_dmbot: bool = False
    ) -> Update | None:
        """A newer version of the house-rules set this campaign follows, if there is one.
        Read only. Called at a session's start and never during one, so a table's rules
        never change mid-game. `for_dmbot`: DMbot is asking whether to take it, so a version
        an Undo sent back is skipped (only a newer one is taken); the DM, choosing with Pick
        changes or See changes, still sees it."""
        async with self._db.guild(guild_id) as conn:
            mine = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOUSE_RULES and f.updating
            ]
            if not mine:
                return None
            fresh, _ = await self._latest_of(conn, mine[0])
        if fresh is None or fresh.version <= mine[0].version:
            return None
        if for_dmbot and fresh.version <= (mine[0].declined_version or 0):
            return None
        return Update(mine[0], fresh, tuple(set_diff.diff(mine[0].content, fresh.content)))

    async def is_gone(self, guild_id: int, campaign_id: str) -> bool:
        """The publisher stopped sharing the followed set. False when that is not known."""
        async with self._db.guild(guild_id) as conn:
            mine = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOUSE_RULES and f.updating
            ]
            if not mine:
                return False
            _, gone = await self._latest_of(conn, mine[0])
            return gone

    async def stop_updating(self, guild_id: int, campaign_id: str) -> int:
        """Mark the followed house-rules set "no longer updated" (the campaign keeps its
        copy). Done by the bot when a set it follows has gone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE rule_set_follows SET updating = false WHERE guild_id = %s"
                " AND campaign_id = %s AND kind = %s AND updating",
                (guild_id, campaign_id, HOUSE_RULES),
            )
            return int(cur.rowcount)

    async def take_update(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int | None,
        accepted: Collection[int] | None = None,
        expected_version: int | None = None,
    ) -> Taken:
        """Take the set's newer version. `accepted` None, or every changed rule: Follow, the
        campaign's copy becomes the new version and it keeps following. Otherwise Cherry
        pick with something rejected: the set as the campaign had it plus the accepted
        changes becomes the campaign's own house rules (each marked "from <set> house rules,
        v<n>"), and the follow ends. All in one transaction, so it is one Undo. Only a DM
        of the following campaign. The version is read again here: what is taken is what is
        shared now. `user_id` None: DMbot itself, at a session's start (Follow is the default
        when the DM does nothing); it is credited to DMbot, not to a DM."""
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            if user_id is not None:
                await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            else:  # DMbot: no person to check, but the same lock as everyone else
                cur = await conn.execute(
                    "SELECT 1 AS ok FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                    (guild_id, campaign_id),
                )
                if await cur.fetchone() is None:
                    raise RuleSetError(NOT_FOLLOWING)
            mine = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOUSE_RULES and f.updating
            ]
            if not mine:
                raise RuleSetError(NOT_FOLLOWING)
            follow = mine[0]
            fresh, _gone = await self._latest_of(conn, follow)
            if fresh is None:
                raise RuleSetError(NO_SUCH_SET)
            if fresh.version <= follow.version:
                raise RuleSetError(NOTHING_NEW)
            if user_id is None and fresh.version <= (follow.declined_version or 0):
                raise RuleSetError(NOTHING_NEW)  # Undone: DMbot waits for a newer version
            if expected_version is not None and fresh.version != expected_version:
                raise RuleSetError(CHANGED_AGAIN)  # the choices were made on older changes
            changes = set_diff.diff(follow.content, fresh.content)
            numbers = {c.number for c in changes}
            if accepted is None or numbers <= set(accepted):
                await conn.execute(
                    "UPDATE rule_set_follows SET version = %s, content = %s, set_name = %s,"
                    " publisher = %s, previous_version = %s, previous_content = %s,"
                    " taken_by = %s, taken_source = %s, declined_version = NULL"
                    " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s",
                    (
                        fresh.version,
                        Jsonb(fresh.content),
                        fresh.name,
                        fresh.publisher,
                        follow.version,
                        Jsonb(follow.content),
                        user_id,
                        "auto" if user_id is None else "dm",
                        guild_id,
                        campaign_id,
                        follow.set_id,
                    ),
                )
                return Taken(False, follow, fresh.version, offered=fresh.version)
            if user_id is None:  # only a DM's press merges rules into the campaign's own
                raise RuleSetError(NOT_FOLLOWING)
            author: int = user_id
            kept = numbers & set(accepted)
            merged = set_diff.rules_of(set_diff.apply(follow.content, changes, kept))
            cur = await conn.execute(
                "SELECT count(*) AS n FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            if int((await cur.fetchone() or {"n": 0})["n"]) + len(merged) > HOUSE_RULES_MAX:
                raise RuleSetError(NO_ROOM.format(n=HOUSE_RULES_MAX))
            version = fresh.version if kept else follow.version
            marker = f"{fresh.name} house rules, v{version}"[:200]
            added: list[int] = []
            for number in sorted(merged):
                rule = merged[number]
                saved = await insert_rule(
                    conn, guild_id, campaign_id, author, rule["rule"],
                    rule["instead"], None, None, None, now, marker,
                )  # fmt: skip
                added.append(saved.number)
            await conn.execute(
                "DELETE FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id = %s",
                (guild_id, campaign_id, follow.set_id),
            )
            return Taken(True, follow, version, tuple(added), marker, fresh.version)

    async def undo_last_take(
        self, guild_id: int, campaign_id: str, user_id: int, expected_version: int
    ) -> Follow:
        """Undo the last Follow (the automatic one at a session's start, or a press): the copy
        before it comes back. Kept in the follow's row, so this needs nothing in memory and
        works after a restart. Only while the campaign is still on `expected_version`, so an
        old Undo button can't undo a newer update. Returns the follow as it was."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [
                f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOUSE_RULES
            ]
            if not mine or mine[0].previous_version is None or mine[0].version != expected_version:
                raise RuleSetError(NOTHING_TO_UNDO)
            cur = await conn.execute(
                "UPDATE rule_set_follows SET version = previous_version,"
                " content = previous_content, previous_version = NULL,"
                " previous_content = NULL, taken_by = NULL, taken_source = NULL,"
                " declined_version = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s"
                " AND version = %s AND previous_version IS NOT NULL RETURNING version",
                (expected_version, guild_id, campaign_id, mine[0].set_id, expected_version),
            )
            if await cur.fetchone() is None:
                raise RuleSetError(NOTHING_TO_UNDO)
            return mine[0]

    async def undo_take(self, guild_id: int, campaign_id: str, user_id: int, taken: Taken) -> int:
        """Put back what `take_update` did: the old copy returns (and, after a merge, the
        rules it added are removed; their numbers are not used again). Returns how many
        merged rules were kept because the DM has edited them since."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            before = taken.before
            if taken.merged:
                if any(f.kind == HOUSE_RULES for f in await _followed(conn, guild_id, campaign_id)):
                    raise RuleSetError(ALREADY_FOLLOWING)
                cur = await conn.execute(
                    "DELETE FROM house_rules WHERE guild_id = %s AND campaign_id = %s"
                    " AND number = ANY(%s) AND from_set = %s AND version = 1",  # never edited
                    (guild_id, campaign_id, list(taken.added), taken.marker),
                )
                kept = len(taken.added) - int(cur.rowcount)
                offer = Offer(
                    before.set_id, before.kind, before.name, before.publisher, before.version,
                    before.content, before.code,
                )  # fmt: skip
                await conn.execute(
                    _INSERT_FOLLOW,
                    _follow_values(
                        guild_id, campaign_id, user_id, before.followed_at, offer, 0,
                        before.updating,
                    ),
                )  # fmt: skip
                # Back on the earlier copy: DMbot must not take the rejected version again at
                # the next session start (only a newer one), as for a plain Undo.
                await conn.execute(
                    "UPDATE rule_set_follows SET declined_version = %s WHERE guild_id = %s"
                    " AND campaign_id = %s AND set_id = %s",
                    (taken.offered, guild_id, campaign_id, before.set_id),
                )
            else:
                cur = await conn.execute(
                    "UPDATE rule_set_follows SET version = %s, content = %s, set_name = %s,"
                    " publisher = %s, previous_version = NULL, previous_content = NULL,"
                    " taken_by = NULL, taken_source = NULL, declined_version = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s AND version = %s",
                    (
                        before.version,
                        Jsonb(before.content),
                        before.name,
                        before.publisher,
                        taken.offered or taken.after_version,
                        guild_id,
                        campaign_id,
                        before.set_id,
                        taken.after_version,
                    ),
                )
                if cur.rowcount == 0:
                    raise RuleSetError(NOT_FOLLOWING)
                return 0
            return kept


_LATEST_FROM = (
    " FROM rule_sets s"
    " JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id"
    " JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id"
    " AND v.set_id = s.id AND v.version = (SELECT max(version) FROM rule_set_versions m"
    "  WHERE m.guild_id = s.guild_id AND m.campaign_id = s.campaign_id AND m.set_id = s.id)"
)
_LATEST = (
    "SELECT s.id AS set_id, s.kind, s.name AS set_name, c.name AS publisher, v.version,"
    " v.content" + _LATEST_FROM
)
# The same, with the set's status, for a follower that wants to know whether it is still shared.
_LATEST_WITH_STATUS = (
    "SELECT s.id AS set_id, s.kind, s.name AS set_name, c.name AS publisher, v.version,"
    " v.content, s.status" + _LATEST_FROM
)
_INSERT_FOLLOW = (
    "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name, publisher,"
    " version, content, position, updating, followed_by, followed_at, code)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _follow_values(
    guild_id: int, campaign_id: str, user_id: int, at: int, offer: Offer, position: int,
    updating: bool = True,
) -> tuple[Any, ...]:  # fmt: skip
    return (
        guild_id, campaign_id, offer.set_id, offer.kind, offer.name, offer.publisher,
        offer.version, Jsonb(offer.content), position, updating, user_id, at, offer.code,
    )  # fmt: skip


async def _followed(conn: Conn, guild_id: int, campaign_id: str) -> list[Follow]:
    cur = await conn.execute(
        "SELECT set_id, kind, set_name, publisher, version, content, updating, followed_at, code,"
        " previous_version, previous_content, declined_version FROM rule_set_follows"
        " WHERE guild_id = %s AND campaign_id = %s"
        " ORDER BY position, followed_at, set_id",
        (guild_id, campaign_id),
    )
    return [_follow(r) for r in await cur.fetchall()]
