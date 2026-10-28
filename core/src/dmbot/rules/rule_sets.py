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

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.memory.definition_content import valid_definitions
from dmbot.rules import pack_content, set_diff
from dmbot.rules.changes import ChangeHook, notifies
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
NO_SUCH_RULE = "That house rule isn't here any more."
SUGGEST_FOLLOWER_MAX = 5  # a following campaign's suggestions waiting for an answer
SUGGEST_SET_MAX = 20  # open suggestions on one set, from every table that follows it
SUGGEST_WAIT_S = 30 * 86400  # after this long with no answer, the sender is told and may resend
TOO_MANY_SUGGESTIONS = (
    f"Your campaign already has {SUGGEST_FOLLOWER_MAX} suggestions waiting for an answer. "
    "Try again after you hear back."
)
SET_HAS_ENOUGH = "That set has plenty of suggestions waiting. Try again next week."
ALREADY_SUGGESTED = "This campaign already sent that rule. The answer comes at your next session."
RULE_CHANGED = "That rule was changed since you looked, so nothing was sent. Look at it again."
NOT_YOURS_TO_SEND = "Only rules your campaign wrote can be suggested. This one came from the set."
ALREADY_IN_SET = "The set already has that rule, so nothing was added. The suggestion is closed."
SUGGESTION_GONE = "Someone already answered that suggestion."
SET_FULL = "The set already has {n} rules, so there is no room for another."
NO_CHANGES = "Nothing is different from the last version, so no new version was made."
NOT_EDITOR = "Only this campaign's DMs can change the set."
NOT_PAID = "Changing a shared set needs the campaign to be on a paid plan."
NOTHING_NEW = "There is nothing new to take."
NOTHING_TO_UNDO = "There is nothing to undo."
CHANGED_AGAIN = "The set changed again while you were looking. Look at the new changes first."
WRONG_KIND = "That is a homebrew pack, not house rules. Use the 📦 Homebrew packs button."
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
    def_changes: tuple[set_diff.DefChange, ...] = ()  # the relationship definitions (#1456)

    @property
    def link_changed(self) -> bool:
        """The rules document link differs (it is not a rule, so `changes` doesn't show it)."""
        return self.follow.content.get("link") != self.offer.content.get("link")


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
    link_written: str | None = None  # the set's document link, made the campaign's own on a merge
    # On a merge, the relationship definitions the campaign keeps as its own (#1456), for the
    # caller to settle in memory; none otherwise.
    kept_definitions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A rule a table that follows the set suggests. Holds the words and nothing about who sent
    them: only rules cross between campaigns."""

    id: str
    set_id: str
    rule: str
    instead: str | None
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class Sent:
    """A suggestion this campaign sent, and the answer once the publisher has given one."""

    id: str
    set_name: str
    rule: str
    status: str  # 'open', 'accepted', 'declined', 'present', 'gone' or 'expired'
    version: int | None


@dataclass(frozen=True, slots=True)
class VersionLog:
    version: int
    by: int
    at: int
    source: str


def content_of(
    rules: Sequence[HouseRule],
    link: str | None,
    definitions: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """What a house-rules set carries: each rule's number, text and "instead of", oldest
    first, and the DM's document link. Built field by field, so nothing else of a rule (its
    scenario, who made it, its session) can travel. `definitions`: the relationship definitions
    the DM ticked to travel with it (#1456), cleaned the same way a set's content always is."""
    return {
        "rules": [
            {"number": r.number, "rule": r.rule, "instead": r.supersedes}
            for r in sorted(rules, key=lambda r: r.number)
            if not r.from_set  # rules merged in from another set are not this campaign's to share
        ],
        "link": link,
        "definitions": valid_definitions(list(definitions)),
    }


def valid_content(content: Any, kind: str = HOUSE_RULES) -> dict[str, Any]:
    """A set's content as a copy that is only what a set may hold: a list of rules (number,
    text, "instead of") and a link, each within the limits. Anything else is dropped; a shape
    that isn't a set's raises. A homebrew pack (`kind` "homebrew", #1527) holds entries instead,
    checked by `pack_content`; a pack can't pass as house rules or the other way round."""
    if kind == "homebrew":
        try:
            return pack_content.valid_content(content)
        except pack_content.BadPack:
            raise RuleSetError(NO_SUCH_SET) from None
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
    return {
        "rules": rules,
        "link": link,
        "definitions": valid_definitions(content.get("definitions")),
    }


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
        int(row["version"]), valid_content(_json(row["content"]), str(row["kind"])), code,
    )  # fmt: skip


def _follow(row: dict[str, Any]) -> Follow:
    return Follow(
        str(row["set_id"]), str(row["kind"]), str(row["set_name"]), str(row["publisher"]),
        int(row["version"]), _json(row["content"]), bool(row["updating"]),
        int(row["followed_at"]), row.get("code"), row.get("previous_version"),
        _json(row["previous_content"]) if row.get("previous_content") else None,
        row.get("declined_version"),
    )  # fmt: skip


def same_words(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


def replaced_rule(rules: Sequence[dict[str, Any]], instead: str | None) -> dict[str, Any] | None:
    """The set's own rule a suggestion says it is instead of, if it names one."""
    return next((r for r in rules if instead and same_words(r["rule"], instead)), None)


def suggested_content(content: dict[str, Any], rule: str, instead: str | None) -> dict[str, Any]:
    """The set's content with a suggested rule in it. A suggestion that says it is instead of
    one of the set's own rules replaces that rule (keeping its number and what IT replaces);
    any other is added as the next number. Raises if the set already holds those words or has
    no room."""
    rules = [dict(r) for r in content["rules"]]
    if any(same_words(r["rule"], rule) for r in rules):
        raise RuleSetError(NO_CHANGES)
    target = replaced_rule(rules, instead)
    if target is not None:
        target["rule"] = rule
    else:
        if len(rules) >= HOUSE_RULES_MAX:
            raise RuleSetError(SET_FULL.format(n=HOUSE_RULES_MAX))
        rules.append({"number": max((r["number"] for r in rules), default=0) + 1,
                      "rule": rule, "instead": instead})  # fmt: skip
    return {
        "rules": rules,
        "link": content.get("link"),
        "definitions": content.get("definitions", []),
    }


class RuleSetStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock
        self._wrong: dict[tuple[int, int], deque[float]] = {}  # (server, person) -> guesses
        # Told after a change to what a campaign follows or merged in (the rules channel, #1752).
        self.on_change: ChangeHook | None = None

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

    async def current_content(
        self, guild_id: int, campaign_id: str
    ) -> tuple[SharedSet, dict[str, Any]] | None:
        """The campaign's shared set and what its newest version holds (None: not shared)."""
        mine = await self.published(guild_id, campaign_id)
        if mine is None or not mine.shared:
            return None
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT content FROM rule_set_versions WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id = %s AND version = %s",
                (guild_id, campaign_id, mine.id, mine.version),
            )
            row = await cur.fetchone()
        return (mine, valid_content(_json(row["content"]))) if row else None

    async def content_at(
        self, guild_id: int, campaign_id: str, version: int
    ) -> dict[str, Any] | None:
        """What one version of the campaign's own shared set held (None: no such version)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT v.content FROM rule_set_versions v JOIN rule_sets s"
                " ON s.guild_id = v.guild_id AND s.campaign_id = v.campaign_id AND s.id = v.set_id"
                " WHERE v.guild_id = %s AND v.campaign_id = %s AND s.kind = %s AND v.version = %s",
                (guild_id, campaign_id, HOUSE_RULES, version),
            )
            row = await cur.fetchone()
        return valid_content(_json(row["content"])) if row else None

    async def publish(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        name: str,
        rules: Sequence[HouseRule],
        link: str | None,
        definitions: Sequence[dict[str, Any]] = (),
    ) -> SharedSet:
        """Share the campaign's house rules as a set, version 1. Only a DM of the campaign
        (the paid-plan check is the caller's, with the owner's plan). `rules` and `link` are
        what the DM sees now; only their rule text and "instead of" text are copied. `definitions`:
        the relationship definitions the DM ticked to travel with them (#1456)."""
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
            content = Jsonb(content_of(rules, link, definitions))
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
            await conn.execute(  # this kind's codes only: a pack's codes are its own (#1527)
                "UPDATE rule_set_codes SET active = false WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id IN (SELECT id FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s)",
                (guild_id, campaign_id, guild_id, campaign_id, HOUSE_RULES),
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

    async def codes(
        self, guild_id: int, campaign_id: str, kind: str = HOUSE_RULES
    ) -> list[tuple[str, bool]]:
        """The campaign's share codes for one kind of set and whether each works, newest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT c.code, c.active FROM rule_set_codes c JOIN rule_sets s"
                " ON s.guild_id = c.guild_id AND s.campaign_id = c.campaign_id AND s.id = c.set_id"
                " WHERE c.guild_id = %s AND c.campaign_id = %s AND s.kind = %s"
                " ORDER BY c.created_at DESC, c.code",
                (guild_id, campaign_id, kind),
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

    @notifies
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
            if fresh.kind != HOUSE_RULES:  # a pack is followed with `follow_pack` (#1527)
                raise RuleSetError(WRONG_KIND)
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

    @notifies
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

    @notifies
    async def restore(self, guild_id: int, campaign_id: str, user_id: int, follow: Follow) -> None:
        """Undo an unfollow: the copy comes back as it was (unless the campaign follows
        another set by now)."""
        if follow.kind != HOUSE_RULES:
            raise RuleSetError(WRONG_KIND)
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

    # ---- the publisher's side: new versions, the log ------------------------------

    async def _may_edit(
        self, conn: Conn, guild_id: int, campaign_id: str, user_id: int, set_id: str
    ) -> None:
        """Only the publishing campaign's current DMs change the set (owner decision: no
        separate co-editor list; adding a DM to the campaign is how someone gets to edit)."""
        try:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
        except HouseRuleError as exc:
            raise RuleSetError(NOT_EDITOR) from exc

    async def save_version(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        content: Any,
        source: Literal["publisher", "suggestion", "document"] = "publisher",
        *,
        plan_ok: bool,
        expected_version: int | None = None,
    ) -> SharedSet:
        return await self._save(
            guild_id, campaign_id, user_id, content, source,
            plan_ok=plan_ok, expected_version=expected_version,
        )  # fmt: skip

    async def _save(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        content: Any,
        source: Literal["publisher", "suggestion", "document"],
        *,
        plan_ok: bool,
        expected_version: int | None,
        suggestion: str | None = None,
    ) -> SharedSet:
        """Make `content` the set's next version. Only the publisher's DMs; the set
        must be shared; nothing is made if nothing differs. Followers
        see it at their next session start, never sooner. `plan_ok`: the caller has checked the
        publishing campaign is still on a paid plan (the owner's plan, which this store can't
        read); without it nothing is saved."""
        if not plan_ok:
            raise RuleSetError(NOT_PAID)
        wanted = valid_content(content) if suggestion is None else {}
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            # The campaign row is locked first, in the same
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
            if expected_version is not None and int(last["version"]) != expected_version:
                raise RuleSetError(CHANGED_AGAIN)  # someone published since the DM looked
            if suggestion is not None:
                # An accepted suggestion is added to the version that is newest under the lock,
                # and marked answered in the same transaction, so it can't be added twice.
                cur = await conn.execute(
                    "SELECT rule, instead FROM rule_set_suggestions WHERE guild_id = %s"
                    " AND campaign_id = %s AND id = %s AND set_id = %s AND status = 'open'"
                    " AND created_at > %s FOR UPDATE",  # past the wait, the sender was told
                    (guild_id, campaign_id, suggestion, set_id, now - SUGGEST_WAIT_S),
                )
                asked = await cur.fetchone()
                if asked is None:
                    raise RuleSetError(SUGGESTION_GONE)
                wanted = suggested_content(
                    valid_content(_json(last["content"])), asked["rule"], asked["instead"]
                )
            if valid_content(_json(last["content"])) == wanted:
                raise RuleSetError(NO_CHANGES)
            version = int(last["version"]) + 1
            await conn.execute(
                "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version,"
                " content, created_by, created_at, source)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, set_id, version, Jsonb(wanted), user_id, now, source),
            )
            if suggestion is not None:
                await conn.execute(
                    "UPDATE rule_set_suggestions SET status = 'accepted', resolved_at = %s,"
                    " resolved_by = %s, version = %s WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = %s",
                    (now, user_id, version, guild_id, campaign_id, suggestion),
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
        expected_version: int | None = None,
        definitions: Sequence[dict[str, Any]] = (),
    ) -> SharedSet:
        """The publisher's current house rules (and the relationship definitions it carries) as
        the set's next version. `plan_ok`: see
        `save_version`. `expected_version`: the version the DM was looking at; if another has
        been published since, nothing is made."""
        return await self.save_version(
            guild_id,
            campaign_id,
            user_id,
            content_of(rules, link, definitions),
            "publisher",
            plan_ok=plan_ok,
            expected_version=expected_version,
        )

    # ---- suggestions: a follower's rule sent to the publisher (#1259) ------------------

    async def suggest(
        self, guild_id: int, campaign_id: str, user_id: int, number: int, shown_version: int
    ) -> tuple[str, int, str]:
        """A DM of a following campaign sends one of the campaign's own house rules to the set's
        publisher. Returns (the suggestion's id, the publisher's server, its campaign): the
        last two are for DMbot to post the note and are never shown. A rule that came from a set
        is not the campaign's to send. `shown_version`: the rule's version as the DM was shown it;
        if it was edited since, nothing is sent (what goes out is what they confirmed).
        Limits: `SUGGEST_FOLLOWER_MAX` waiting from this campaign, `SUGGEST_SET_MAX` open on
        the set."""
        now = self._now()
        sid = secrets.token_hex(16)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            follows = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOUSE_RULES and f.updating
            ]
            if not follows:
                raise RuleSetError(NOT_FOLLOWING)
            follow = follows[0]
            cur = await conn.execute(
                "SELECT rule, supersedes, from_set, version FROM house_rules WHERE guild_id = %s"
                " AND campaign_id = %s AND number = %s",
                (guild_id, campaign_id, number),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NO_SUCH_RULE)
            if row["from_set"]:
                raise RuleSetError(NOT_YOURS_TO_SEND)
            if row["version"] != shown_version:
                raise RuleSetError(RULE_CHANGED)
            cur = await conn.execute(
                "SELECT count(*) AS n, count(*) FILTER (WHERE rule = %s) AS same"
                " FROM rule_set_suggestions_sent WHERE guild_id = %s AND campaign_id = %s"
                " AND told_at IS NULL",
                (row["rule"], guild_id, campaign_id),
            )
            counts = await cur.fetchone()
            assert counts is not None
            if counts["same"]:
                raise RuleSetError(ALREADY_SUGGESTED)
            if counts["n"] >= SUGGEST_FOLLOWER_MAX:
                raise RuleSetError(TOO_MANY_SUGGESTIONS)
            cur = await conn.execute(
                "SELECT outcome, pub_guild, pub_campaign FROM dmbot_suggest_rule("
                "%s, %s, %s, %s, %s, %s, %s, %s)",
                (sid, follow.set_id, campaign_id, row["rule"], row["supersedes"], now,
                 SUGGEST_SET_MAX, SUGGEST_WAIT_S),
            )  # fmt: skip
            door = await cur.fetchone()
            assert door is not None
            if door["outcome"] == "full":
                raise RuleSetError(SET_HAS_ENOUGH)
            if door["outcome"] != "ok":
                raise RuleSetError(NO_SUCH_SET)
            await conn.execute(
                "INSERT INTO rule_set_suggestions_sent (guild_id, campaign_id, id, set_id,"
                " set_name, rule, sent_by, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, sid, follow.set_id, follow.name, row["rule"],
                 user_id, now),
            )  # fmt: skip
        return sid, int(door["pub_guild"]), str(door["pub_campaign"])

    async def suggestion(
        self, guild_id: int, campaign_id: str, suggestion_id: str
    ) -> Suggestion | None:
        """One suggestion on the campaign's own set (the publisher's side), or None."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, set_id, rule, instead, status, created_at FROM rule_set_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, suggestion_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        status = str(row["status"])
        if status == "open" and self._now() - int(row["created_at"]) > SUGGEST_WAIT_S:
            status = "expired"  # the sender was told they may send it again; it can't be added
        return Suggestion(
            str(row["id"]), str(row["set_id"]), str(row["rule"]), row["instead"],
            status, int(row["created_at"]),
        )  # fmt: skip

    async def unnoted_suggestions(
        self, guild_id: int, campaign_id: str
    ) -> list[tuple[Suggestion, str]]:
        """Open suggestions whose note never reached the DM screen (no channel, or Discord
        refused), each with the set's name, oldest first: DMbot posts them at a session's start."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT q.id, q.set_id, q.rule, q.instead, q.status, q.created_at, s.name"
                " FROM rule_set_suggestions q JOIN rule_sets s ON s.guild_id = q.guild_id"
                " AND s.campaign_id = q.campaign_id AND s.id = q.set_id"
                " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'open'"
                " AND q.noted_at IS NULL AND s.status = 'shared' AND q.created_at > %s"
                " ORDER BY q.created_at",
                (guild_id, campaign_id, self._now() - SUGGEST_WAIT_S),
            )
            rows = await cur.fetchall()
        return [
            (
                Suggestion(
                    str(r["id"]), str(r["set_id"]), str(r["rule"]), r["instead"],
                    str(r["status"]), int(r["created_at"]),
                ),
                str(r["name"]),
            )
            for r in rows
        ]  # fmt: skip

    async def mark_noted(self, guild_id: int, campaign_id: str, suggestion_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE rule_set_suggestions SET noted_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (self._now(), guild_id, campaign_id, suggestion_id),
            )

    async def accept_suggestion(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        suggestion_id: str,
        *,
        plan_ok: bool,
        expected_version: int | None = None,
    ) -> SharedSet:
        """The publisher's DMs make the suggested rule part of the set as its next version
        (the same checks as any change: paid plan, a DM, the version they saw)."""
        try:
            return await self._save(
                guild_id, campaign_id, user_id, None, "suggestion",
                plan_ok=plan_ok, expected_version=expected_version, suggestion=suggestion_id,
            )  # fmt: skip
        except RuleSetError as exc:
            if str(exc) == NO_CHANGES:
                # The set already holds those words (so the DM was already a DM here, checked
                # before this was raised): close it, so no dead button or slot is left behind.
                async with self._db.guild(guild_id) as conn:
                    await conn.execute(
                        "UPDATE rule_set_suggestions SET status = 'present', resolved_at = %s,"
                        " resolved_by = %s WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                        " AND status = 'open'",
                        (self._now(), user_id, guild_id, campaign_id, suggestion_id),
                    )
                raise RuleSetError(ALREADY_IN_SET) from exc
            raise

    async def decline_suggestion(
        self, guild_id: int, campaign_id: str, user_id: int, suggestion_id: str
    ) -> bool:
        """No thanks: closes the suggestion and changes nothing else. False: it was already
        answered."""
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE rule_set_suggestions SET status = 'declined', resolved_at = %s,"
                " resolved_by = %s WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND status = 'open'",
                (now, user_id, guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)

    async def sent_answers(self, guild_id: int, campaign_id: str) -> list[Sent]:
        """The suggestions this campaign sent that its DM has not been told the end of, with the
        answers the publisher has given (asked by their random ids; nothing else is reachable)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, set_name, rule, sent_at FROM rule_set_suggestions_sent"
                " WHERE guild_id = %s AND campaign_id = %s AND told_at IS NULL ORDER BY sent_at",
                (guild_id, campaign_id),
            )
            sent = await cur.fetchall()
            if not sent:
                return []
            cur = await conn.execute(
                "SELECT id, status, version FROM dmbot_suggestion_answers(%s)",
                (",".join(str(r["id"]) for r in sent),),
            )
            answers = {str(r["id"]): r for r in await cur.fetchall()}
        out = []
        for r in sent:
            a = answers.get(str(r["id"]))
            # No row back: the publisher's set is gone, so there will be no answer. Told once
            # ("gone"), which frees the slot.
            status = str(a["status"]) if a else "gone"
            if status == "open" and self._now() - int(r["sent_at"]) > SUGGEST_WAIT_S:
                status = "expired"  # told once, so the slot is freed
            out.append(
                Sent(
                    str(r["id"]), str(r["set_name"]), str(r["rule"]), status,
                    int(a["version"]) if a and a["version"] is not None else None,
                )
            )  # fmt: skip
        return out

    async def mark_told(self, guild_id: int, campaign_id: str, ids: Sequence[str]) -> None:
        """The DM has been told these were answered: they stop counting toward the limit."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE rule_set_suggestions_sent SET told_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = ANY(%s)",
                (self._now(), guild_id, campaign_id, list(ids)),
            )

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
        True): the publisher stopped sharing, or the set is deleted (its campaign was). A follow
        with no code was picked from the list in its own server, where a set that is not there
        any more is deleted. (A restored backup's follow has no code and does not update, so it
        is never asked.) A follow made from ANOTHER server before share codes were kept has no
        code either and could not update anyway, so it too reads as gone once, harmlessly (none
        exist yet: nothing with share codes is deployed)."""
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
            return None, True  # picked from this server's list, and no longer in it: deleted
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
        return Update(
            mine[0],
            fresh,
            tuple(set_diff.diff(mine[0].content, fresh.content)),
            tuple(set_diff.diff_definitions(mine[0].content, fresh.content)),
        )

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

    @notifies
    async def take_update(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int | None,
        accepted: Collection[int] | None = None,
        expected_version: int | None = None,
        accepted_defs: Collection[str] | None = None,
    ) -> Taken:
        """Take the set's newer version. `accepted` None, or every changed rule (and, the same
        way, `accepted_defs` for the relationship definitions): Follow, the
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
            def_changes = set_diff.diff_definitions(follow.content, fresh.content)
            keys = {c.key for c in def_changes}
            everything = (accepted is None or numbers <= set(accepted)) and (
                accepted_defs is None or keys <= set(accepted_defs)
            )
            if everything:
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
            kept = numbers if accepted is None else numbers & set(accepted)
            kept_defs = keys if accepted_defs is None else keys & set(accepted_defs)
            result = set_diff.apply(
                follow.content,
                changes,
                kept,
                fresh.content if (kept or kept_defs) else None,
                def_changes,
                kept_defs,
            )
            merged = set_diff.rules_of(result)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM house_rules WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            if int((await cur.fetchone() or {"n": 0})["n"]) + len(merged) > HOUSE_RULES_MAX:
                raise RuleSetError(NO_ROOM.format(n=HOUSE_RULES_MAX))
            version = fresh.version if (kept or kept_defs) else follow.version
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
            link = await self._adopt_link(conn, guild_id, campaign_id, user_id, result, follow, now)
            return Taken(
                True, follow, version, tuple(added), marker, fresh.version, link,
                tuple(result.get("definitions", ())),
            )  # fmt: skip

    async def _adopt_link(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        result: dict[str, Any],
        follow: Follow,
        now: int,
    ) -> str | None:
        """Pick changes with the set's document link changed: the link comes with the rules,
        as the campaign's own file link. A link the campaign already has is never replaced
        (its own win). Returns the link written, for Undo."""
        link = result.get("link")
        if not link or link == follow.content.get("link"):
            return None
        cur = await conn.execute(
            "INSERT INTO house_rules_file (guild_id, campaign_id, link, ignored, set_by, set_at)"
            " VALUES (%s, %s, %s, NULL, %s, %s) ON CONFLICT (guild_id, campaign_id) DO NOTHING",
            (guild_id, campaign_id, link, user_id, now),
        )
        return str(link) if cur.rowcount else None

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

    @notifies
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
                if taken.link_written:  # the link the merge made the campaign's own goes too
                    await conn.execute(
                        "DELETE FROM house_rules_file WHERE guild_id = %s AND campaign_id = %s"
                        " AND link = %s",
                        (guild_id, campaign_id, taken.link_written),
                    )
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


FOLLOWS_MAX = 100  # dump and check agree, so a backup that downloads also restores
FOLLOWS_DAMAGED = "This backup file is damaged (bad shared house rules)."
_FOLLOW_KEYS = frozenset(
    {"set_id", "kind", "name", "publisher", "version", "content", "position", "by", "at"}
)


class FollowedSetsSection:
    """The sets a campaign follows, in its backup (an `ExportSection`): the name, the version it
    uses and that version's rules, so the campaign never loses them ("Never lost", PLAN). NEVER
    the share code, which is a key to the publisher's set and must not travel in a file anyone in
    the server may download. A restored campaign has no code, so each follow comes back marked
    **no longer updated**: it keeps its copy and its rules keep working, and it can follow the
    set again with a code. A forged file could name any set id here; that is harmless because a
    restored follow has no code and does not update, and nothing authorises by a set id alone
    (a suggestion needs a follow that is still updating)."""

    name = "rule_set_follows"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT set_id, kind, set_name, publisher, version, content, position, followed_by,"
            " followed_at FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY position, set_id LIMIT %s",
            (guild_id, campaign_id, FOLLOWS_MAX),
        )
        return [
            {
                "set_id": str(r["set_id"]),
                "kind": str(r["kind"]),
                "name": str(r["set_name"]),
                "publisher": str(r["publisher"]),
                "version": int(r["version"]),
                "content": _json(r["content"]),
                "position": int(r["position"]),
                "by": str(r["followed_by"]),
                "at": int(r["followed_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Every row validated; nothing is written until all are right."""
        if len(rows) > FOLLOWS_MAX:
            raise CampaignError(FOLLOWS_DAMAGED)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        houses = 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != _FOLLOW_KEYS:
                raise CampaignError(FOLLOWS_DAMAGED)
            set_id, kind = row["set_id"], row["kind"]
            name, publisher = row["name"], row["publisher"]
            by = row["by"]
            if (
                not isinstance(set_id, str)
                or len(set_id) != 32
                or set_id.strip("0123456789abcdef")
                or set_id in seen
                or kind not in (HOUSE_RULES, "homebrew")
                or not isinstance(name, str)
                or not 1 <= len(name) <= NAME_MAX
                or not isinstance(publisher, str)
                or not 1 <= len(publisher) <= 100
                or not _plain_int(row["version"], 1)
                or not _plain_int(row["position"], 0)
                or not _plain_int(row["at"], 0, 2**63 - 1)
                or not (isinstance(by, str) and by.isascii() and by.isdigit() and int(by) > 0)
                or int(by) > 2**63 - 1
            ):
                raise CampaignError(FOLLOWS_DAMAGED)
            seen.add(set_id)
            houses += kind == HOUSE_RULES
            try:
                content = valid_content(row["content"], kind)
            except RuleSetError:
                raise CampaignError(FOLLOWS_DAMAGED) from None
            out.append(row | {"content": content})
        if len(out) - houses > pack_content.PACKS_MAX:  # and at most a few homebrew packs
            raise CampaignError(FOLLOWS_DAMAGED)
        if houses > 1:  # a campaign follows one house-rules set
            raise CampaignError(FOLLOWS_DAMAGED)
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for row in self.check(rows):  # the store has checked them; this is cheap and idempotent
            await conn.execute(
                _INSERT_FOLLOW,
                (
                    guild_id, campaign_id, row["set_id"], row["kind"], row["name"],
                    row["publisher"], row["version"], Jsonb(row["content"]), row["position"],
                    False,  # no code, so no updates: "no longer updated"
                    int(row["by"]), row["at"], None,
                ),
            )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


def _plain_int(value: object, low: int, high: int = 2**31 - 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


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
