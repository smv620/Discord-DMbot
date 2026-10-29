"""Homebrew packs (#1527; docs/PLAN.md, "Shared rulesets"): a paid campaign's DMs publish the
campaign's own homebrew as a named pack, and another campaign's DM may follow it, in the same
server (a list) or from another with a share code. The same tables, share codes and door as house
rules sets (`RuleSetStore`), with `kind = 'homebrew'`, so there is one sharing feature with two
kinds of content.

Differences from a house-rules set, all deliberate:
- A campaign publishes one pack, but follows several; the DM puts them in order (`position`), and
  the order is the precedence ("followed packs in order", after the campaign's own homebrew).
- Entries loaded from a confirmed book are never published (`pack_content`).
- A follower never changes a pack, and its own entry with the same name wins: the readers look at
  the campaign's own homebrew first and only then at its followed packs.

Only entries travel: no story memory, names, notes, transcripts or sheets. Every query filters on
the server and the campaign and runs inside `Database.guild`.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.rules import pack_content
from dmbot.rules.homebrew import Homebrew, HomebrewStore, name_key
from dmbot.rules.homebrew_sources import SourceStore
from dmbot.rules.house import require_dm
from dmbot.rules.pack_content import PACKS_MAX
from dmbot.rules.rule_sets import (
    _INSERT_FOLLOW,
    _LATEST,
    NAME_MAX,
    Follow,
    Offer,
    RuleSetError,
    RuleSetStore,
    SharedSet,
    _follow,
    _follow_values,
    _followed,
    _json,
    _offer,
    new_code,
    valid_content,
)

log = logging.getLogger(__name__)

HOMEBREW = "homebrew"

NO_ENTRIES = (
    "This campaign has no homebrew to share yet. Add some first (entries from a book stay here)."
)
NEED_NAME = "Give the pack a name (up to 60 characters)."
ALREADY_SHARED = "This campaign already shares its homebrew."
NOT_SHARED = "This campaign isn't sharing its homebrew."
NOT_A_PACK = "That isn't a homebrew pack."
OWN_PACK = "That pack is this campaign's own."
ALREADY_FOLLOWING = "This campaign already follows that pack."
TOO_MANY_PACKS = f"This campaign already follows {PACKS_MAX} homebrew packs. Unfollow one first."
NOT_FOLLOWING = "This campaign isn't following that pack."
NO_CHANGES = "Nothing is different from the last version, so no new version was made."


@dataclass(frozen=True, slots=True)
class Published:
    """A pack just published or changed, and the entries kept back because they came from a book."""

    pack: SharedSet
    left_out: tuple[str, ...]


class HomebrewPackStore(RuleSetStore):
    """Packs on the shared-set tables. Reading a pack by its share code, and the wrong-guess
    limit, are `RuleSetStore.by_code`; turning one code off is `turn_off_code`."""

    # ---- publishing -----------------------------------------------------------------

    async def published_pack(self, guild_id: int, campaign_id: str) -> SharedSet | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.id, s.name, s.status, max(v.version) AS version FROM rule_sets s"
                " JOIN rule_set_versions v ON v.guild_id = s.guild_id"
                " AND v.campaign_id = s.campaign_id AND v.set_id = s.id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.kind = %s"
                " GROUP BY s.id, s.name, s.status",
                (guild_id, campaign_id, HOMEBREW),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SharedSet(
            str(row["id"]), str(row["name"]), row["status"] == "shared", int(row["version"])
        )

    async def pack_content(
        self, guild_id: int, campaign_id: str, version: int | None = None
    ) -> dict[str, Any] | None:
        """What the campaign's own pack holds (its newest version, or `version`)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT v.content FROM rule_set_versions v JOIN rule_sets s"
                " ON s.guild_id = v.guild_id AND s.campaign_id = v.campaign_id AND s.id = v.set_id"
                " WHERE v.guild_id = %s AND v.campaign_id = %s AND s.kind = %s"
                " AND v.version = COALESCE(%s, (SELECT max(m.version) FROM rule_set_versions m"
                " WHERE m.guild_id = s.guild_id AND m.campaign_id = s.campaign_id"
                " AND m.set_id = s.id))",
                (guild_id, campaign_id, HOMEBREW, version),
            )
            row = await cur.fetchone()
        return valid_content(_json(row["content"]), HOMEBREW) if row else None

    async def publish_pack(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        name: str,
        entries: Sequence[Homebrew],
    ) -> Published:
        """Share the campaign's homebrew as a pack, version 1 (or a new version if it was shared
        and stopped before). Only a DM of the campaign (the paid-plan check is the caller's, with
        the owner's plan). Entries from a confirmed book are left out and named in the result."""
        name = " ".join(name.split())
        if not 0 < len(name) <= NAME_MAX:
            raise RuleSetError(NEED_NAME)
        content, left_out = pack_content.content_of(entries)
        if not content["entries"]:
            raise RuleSetError(NO_ENTRIES)
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT id, status FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s",
                (guild_id, campaign_id, HOMEBREW),
            )
            old = await cur.fetchone()
            if old is not None and old["status"] == "shared":
                raise RuleSetError(ALREADY_SHARED)
            if old is not None:
                set_id = str(old["id"])
                version = await _next_version(conn, guild_id, campaign_id, set_id)
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
                    (guild_id, campaign_id, set_id, HOMEBREW, name, user_id, now),
                )
            await _add_version(conn, guild_id, campaign_id, set_id, version, content, user_id, now)
        return Published(SharedSet(set_id, name, True, version), tuple(left_out))

    async def publish_pack_changes(
        self, guild_id: int, campaign_id: str, user_id: int, entries: Sequence[Homebrew]
    ) -> Published:
        """A new version of the shared pack from the campaign's homebrew as it is now. Followers
        take it at their next session start. Nothing new, nothing saved."""
        content, left_out = pack_content.content_of(entries)
        if not content["entries"]:
            raise RuleSetError(NO_ENTRIES)
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT id, name FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s AND status = 'shared'",
                (guild_id, campaign_id, HOMEBREW),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NOT_SHARED)
            set_id = str(row["id"])
            cur = await conn.execute(
                "SELECT content FROM rule_set_versions WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id = %s ORDER BY version DESC LIMIT 1",
                (guild_id, campaign_id, set_id),
            )
            last = await cur.fetchone()
            if last is not None and valid_content(_json(last["content"]), HOMEBREW) == content:
                raise RuleSetError(NO_CHANGES)
            version = await _next_version(conn, guild_id, campaign_id, set_id)
            await _add_version(conn, guild_id, campaign_id, set_id, version, content, user_id, now)
        return Published(SharedSet(set_id, str(row["name"]), True, version), tuple(left_out))

    async def stop_sharing_pack(self, guild_id: int, campaign_id: str, user_id: int) -> bool:
        """Turn sharing off: no new follows (the pack's codes stop working). Followers keep the
        version they have. False if nothing was shared."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE rule_sets SET status = 'unshared' WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s AND status = 'shared'",
                (guild_id, campaign_id, HOMEBREW),
            )
            changed = cur.rowcount > 0
            await conn.execute(
                "UPDATE rule_set_codes SET active = false WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id IN (SELECT id FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s)",
                (guild_id, campaign_id, guild_id, campaign_id, HOMEBREW),
            )
        return bool(changed)

    async def make_pack_code(self, guild_id: int, campaign_id: str, user_id: int) -> str:
        """A new share code for the campaign's shared pack."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT id FROM rule_sets WHERE guild_id = %s AND campaign_id = %s AND kind = %s"
                " AND status = 'shared'",
                (guild_id, campaign_id, HOMEBREW),
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

    async def pack_codes(self, guild_id: int, campaign_id: str) -> list[tuple[str, bool]]:
        return await self.codes(guild_id, campaign_id, HOMEBREW)

    # ---- finding a pack to follow ---------------------------------------------------

    async def packs_here(self, guild_id: int, campaign_id: str) -> list[Offer]:
        """The packs other campaigns in this server share. Read under this server's own
        isolation, so no other server's pack can appear."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                _LATEST + " WHERE s.status = 'shared' AND s.kind = %s"
                " AND NOT (s.guild_id = %s AND s.campaign_id = %s)"
                " ORDER BY lower(s.name), s.id",
                (HOMEBREW, guild_id, campaign_id),
            )
            return [_offer(r) for r in await cur.fetchall()]

    # ---- following ------------------------------------------------------------------

    async def followed_packs(self, guild_id: int, campaign_id: str) -> list[Follow]:
        """The packs the campaign follows, in the DM's order."""
        return [f for f in await self.following(guild_id, campaign_id) if f.kind == HOMEBREW]

    async def pack_copies(self, guild_id: int, campaign_id: str) -> list[Follow]:
        """The followed packs in order, for the readers: only what they use (no previous copy,
        no house-rules follow), so a read on a hot path stays light."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT set_id, kind, set_name, publisher, version, content, updating,"
                " followed_at FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s ORDER BY position, followed_at, set_id",
                (guild_id, campaign_id, HOMEBREW),
            )
            return [_follow(r) for r in await cur.fetchall()]

    async def follow_pack(
        self, guild_id: int, campaign_id: str, user_id: int, offer: Offer
    ) -> Follow:
        """Follow the pack in `offer` (from `packs_here` or `by_code`, which the DM has just
        confirmed): the campaign takes its own copy, last in its order. The pack is read again in
        this transaction, so one turned off or changed since the DM looked is not copied from the
        old offer. Only a DM of the following campaign."""
        if offer.kind != HOMEBREW:
            raise RuleSetError(NOT_A_PACK)
        now = self._now()
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            fresh = await self._reread(conn, offer)
            if fresh.kind != HOMEBREW:
                raise RuleSetError(NOT_A_PACK)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (guild_id, campaign_id, fresh.set_id),
            )
            if await cur.fetchone() is not None:
                raise RuleSetError(OWN_PACK)
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            if any(f.set_id == fresh.set_id for f in mine):
                raise RuleSetError(ALREADY_FOLLOWING)
            if len(mine) >= PACKS_MAX:
                raise RuleSetError(TOO_MANY_PACKS)
            await conn.execute(
                _INSERT_FOLLOW,
                _follow_values(guild_id, campaign_id, user_id, now, fresh, len(mine)),
            )
        return Follow(
            fresh.set_id, fresh.kind, fresh.name, fresh.publisher, fresh.version,
            fresh.content, True, now, fresh.code,
        )  # fmt: skip

    async def unfollow_pack(
        self, guild_id: int, campaign_id: str, user_id: int, set_id: str
    ) -> tuple[Follow, int]:
        """Stop following one pack. Returns what was removed and the place it had in the order,
        so the caller's Undo can `restore_pack` it."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            at = next((i for i, f in enumerate(mine) if f.set_id == set_id), None)
            if at is None:
                raise RuleSetError(NOT_FOLLOWING)
            await conn.execute(
                "DELETE FROM rule_set_follows"
                " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s",
                (guild_id, campaign_id, set_id),
            )
            await _renumber(conn, guild_id, campaign_id, [f.set_id for f in mine if f != mine[at]])
        return mine[at], at

    async def restore_pack(
        self, guild_id: int, campaign_id: str, user_id: int, follow: Follow, at: int
    ) -> None:
        """Undo an unfollow: the copy comes back as it was, in its old place (or last)."""
        offer = Offer(
            follow.set_id, follow.kind, follow.name, follow.publisher, follow.version,
            follow.content, follow.code,
        )  # fmt: skip
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            if any(f.set_id == follow.set_id for f in mine):
                raise RuleSetError(ALREADY_FOLLOWING)
            if len(mine) >= PACKS_MAX:
                raise RuleSetError(TOO_MANY_PACKS)
            await conn.execute(
                _INSERT_FOLLOW,
                _follow_values(
                    guild_id, campaign_id, user_id, follow.followed_at, offer, len(mine),
                    follow.updating,
                ),
            )  # fmt: skip
            ids = [f.set_id for f in mine]
            ids.insert(min(max(at, 0), len(ids)), follow.set_id)
            await _renumber(conn, guild_id, campaign_id, ids)

    async def move_pack(
        self, guild_id: int, campaign_id: str, user_id: int, set_id: str, step: int
    ) -> list[Follow]:
        """Move a followed pack up (-1) or down (+1) the order; the first and last stay put.
        Returns the order now."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            ids = [f.set_id for f in mine]
            if set_id not in ids:
                raise RuleSetError(NOT_FOLLOWING)
            i = ids.index(set_id)
            j = min(max(i + step, 0), len(ids) - 1)
            ids.insert(j, ids.pop(i))
            await _renumber(conn, guild_id, campaign_id, ids)
            by_id = {f.set_id: f for f in mine}
        return [by_id[i] for i in ids]


async def _next_version(conn: Any, guild_id: int, campaign_id: str, set_id: str) -> int:
    cur = await conn.execute(
        "SELECT max(version) AS v FROM rule_set_versions WHERE guild_id = %s"
        " AND campaign_id = %s AND set_id = %s",
        (guild_id, campaign_id, set_id),
    )
    return int((await cur.fetchone() or {"v": 0})["v"] or 0) + 1


async def _add_version(
    conn: Any, guild_id: int, campaign_id: str, set_id: str, version: int,
    content: dict[str, Any], user_id: int, now: int,
) -> None:  # fmt: skip
    await conn.execute(
        "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version, content,"
        " created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (guild_id, campaign_id, set_id, version, Jsonb(content), user_id, now),
    )


async def _renumber(conn: Any, guild_id: int, campaign_id: str, ids: Sequence[str]) -> None:
    """The followed packs' places, 0, 1, 2… in this order (so the places stay a clean run)."""
    for place, set_id in enumerate(ids):
        await conn.execute(
            "UPDATE rule_set_follows SET position = %s WHERE guild_id = %s AND campaign_id = %s"
            " AND set_id = %s",
            (place, guild_id, campaign_id, set_id),
        )


def cite(name: str, version: int) -> str:
    return f"{name} homebrew, v{version}"


def pointer_urls(follows: Sequence[Follow]) -> set[str]:
    """The page addresses the followed packs point to (#1635)."""
    urls: set[str] = set()
    for follow in follows:
        try:
            entries = pack_content.valid_content(follow.content)["entries"]
        except pack_content.BadPack:
            continue
        urls.update(e["page"] for e in entries if "page" in e)
    return urls


def from_follow(
    follow: Follow,
    campaign_id: str,
    place: int,
    pages: Mapping[str, Any] | None = None,
    approved: Collection[str] = (),
) -> list[Homebrew]:
    """The followed pack's entries as read-only homebrew, in the pack's order. `place`: the pack's
    place in the campaign's order, which keeps the (negative) ids apart. The content was checked
    on the way in; an entry that still doesn't fit is skipped rather than failing the answer.
    An entry from a site (#1635) is used only when the campaign has approved the site
    (`approved`); one that points to a page takes its words from `pages` (the kept pages, by
    address), and is skipped when the page isn't there."""
    try:
        entries = pack_content.valid_content(follow.content)["entries"]
    except pack_content.BadPack:
        return []
    out: list[Homebrew] = []
    for i, e in enumerate(entries):
        domain = e.get("domain")
        if domain is not None and domain not in approved:
            continue  # waiting for the DM to say they have the right to use that site's entries
        kind, name = e["kind"], e["name"]
        text, summary, page_url = e["text"], e["summary"], None
        if "page" in e:
            page = (pages or {}).get(e["page"])
            if page is None:
                continue
            # The page says what it is: a publisher can't call a page "Fireball" (#1635 review).
            kind, name, text, summary, page_url = (
                page.kind,
                page.name,
                page.text,
                page.summary,
                e["page"],
            )
        out.append(
            Homebrew(
                id=-(place * 1000 + i + 1),
                campaign_id=campaign_id,
                kind=kind,
                name=name,
                text=text,
                summary=summary,
                created_by=0,
                created_at=follow.followed_at,
                updated_at=follow.followed_at,
                version=follow.version,
                source=cite(follow.name, follow.version),
                source_kind="link" if page_url else "typed",
                credit_domain=domain,
                page_url=page_url,
            )
        )
    return out


def waiting_by_site(
    follows: Sequence[Follow],
    approved: Collection[str],
    pages: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """How many entries of the followed packs wait for a site's approval, by site (#1635). An
    entry pointing to a page that isn't kept here is not waiting: approving the site would not
    turn it on (#1646). `pages` None counts every entry (no page lookup)."""
    waiting: dict[str, int] = {}
    for follow in follows:
        try:
            entries = pack_content.valid_content(follow.content)["entries"]
        except pack_content.BadPack:
            continue
        for e in entries:
            domain = e.get("domain")
            if pages is not None and "page" in e and e["page"] not in pages:
                continue
            if domain is not None and domain not in approved:
                waiting[domain] = waiting.get(domain, 0) + 1
    return waiting


class HomebrewReader:
    """The homebrew a campaign reads: its own entries first, then its followed packs in the DM's
    order (precedence, PLAN: house rules, followed set, own homebrew, followed packs, target,
    fallback). A name is taken once, by the first that has it, so the campaign's own entry always
    beats a pack's and an earlier pack beats a later one. `list` and `find` match
    `HomebrewStore`, so the readers (`/dmbot rule`, rules cards, the sidebar) take either. A pack
    that can't be read costs only its entries, never the campaign's own."""

    def __init__(
        self,
        store: HomebrewStore,
        packs: HomebrewPackStore | None,
        sources: SourceStore | None = None,
    ) -> None:
        self._store = store
        self._packs = packs
        self._sources = sources  # which sites the campaign approved, and the kept pages (#1635)

    async def _approved(self, guild_id: int, campaign_id: str) -> set[str]:
        if self._sources is None:
            return set()
        return {a.domain for a in await self._sources.approvals(guild_id, campaign_id)}

    async def _from_packs(self, guild_id: int, campaign_id: str) -> list[Homebrew]:
        if self._packs is None:
            return []
        try:
            follows = await self._packs.pack_copies(guild_id, campaign_id)
            approved = await self._approved(guild_id, campaign_id)
            urls = pointer_urls(follows)
            pages = await self._sources.pages_by_url(urls) if self._sources and urls else {}
        except Exception:
            log.exception("Couldn't read the followed homebrew packs")
            return []
        return [
            e
            for place, f in enumerate(follows)
            for e in from_follow(f, campaign_id, place, pages, approved)
        ]

    async def waiting(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        """The entries of the followed packs that wait for a site's approval, by site: not used
        until the DM says they have the right to use that site's entries (#1635)."""
        if self._packs is None:
            return {}
        try:
            follows = await self._packs.pack_copies(guild_id, campaign_id)
            urls = pointer_urls(follows)
            pages = await self._sources.pages_by_url(urls) if self._sources and urls else {}
            return waiting_by_site(follows, await self._approved(guild_id, campaign_id), pages)
        except Exception:
            log.exception("Couldn't work out which followed entries are waiting")
            return {}

    async def list(self, guild_id: int, campaign_id: str) -> list[Homebrew]:
        # Only entries the DM has not turned off (#1629): this is what DMbot *uses* at the table.
        # (A followed pack's entry of the same name is a different entry and still answers.)
        own = await self._store.list(guild_id, campaign_id, usable_only=True)
        packs = await self._from_packs(guild_id, campaign_id)
        # A shared book's entry ranks below a followed pack's (#1563): a pack's entry of the same
        # name takes its place, the book's is kept behind it in the free rules' order.
        packed = {name_key(e.name) for e in packs}
        out = [e for e in own if not (e.from_book and name_key(e.name) in packed)]
        taken = {name_key(e.name) for e in out}
        for entry in packs:
            if name_key(entry.name) not in taken:
                taken.add(name_key(entry.name))
                out.append(entry)
        return out

    async def find(self, guild_id: int, campaign_id: str, name: str) -> Homebrew | None:
        """The campaign's own entry with this name, else the first followed pack's. An entry the
        DM loaded from a book ranks below a followed pack's (#1563): the pack's answers."""
        own = await self._store.find(guild_id, campaign_id, name, usable_only=True)
        if (own is not None and not own.from_book) or self._packs is None:
            return own
        key = name_key(name)
        pack = next(
            (e for e in await self._from_packs(guild_id, campaign_id) if name_key(e.name) == key),
            None,
        )
        return pack if pack is not None else own
