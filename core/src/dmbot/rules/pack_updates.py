"""A followed homebrew pack changed (#1527 part C): taking a newer version, Undo, and a pack that
went away. The same rules as house-rules sets (#1191), on the pack's own content:

- Asked at a session's start only, never during play, so a table's homebrew doesn't change mid-game.
- **Follow** (the default when the DM does nothing) makes the campaign's copy the new version, keeps
  the copy before it in the follow's row (so Undo works after a restart), and is credited to DMbot.
- **Pick changes** with anything left out copies the pack (as the campaign had it plus the accepted
  changes) into the campaign's **own homebrew** and ends the follow, in one transaction so it is one
  Undo. The campaign's own entries win: an entry whose name the campaign already has is not
  overwritten. A pack holds nothing from a book, so these copies are ordinary entries.
- An Undo makes DMbot skip that same version at the next start (`declined_version`), not newer ones.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from dmbot.rules import pack_diff
from dmbot.rules.homebrew import HomebrewError, insert_entry
from dmbot.rules.homebrew_packs import (
    HOMEBREW,
    TOO_MANY_PACKS,
    HomebrewPackStore,
    _renumber,
)
from dmbot.rules.house import require_dm
from dmbot.rules.pack_content import PACKS_MAX, BadPack, valid_content
from dmbot.rules.rule_sets import (
    _INSERT_FOLLOW,
    ALREADY_FOLLOWING,
    CHANGED_AGAIN,
    NO_SUCH_SET,
    NOT_FOLLOWING,
    NOTHING_NEW,
    NOTHING_TO_UNDO,
    Follow,
    Offer,
    RuleSetError,
    _follow_values,
    _followed,
)


@dataclass(frozen=True, slots=True)
class PackUpdate:
    """A newer version of a followed pack, and what differs from the follower's copy."""

    follow: Follow
    offer: Offer
    changes: tuple[pack_diff.Change, ...]


@dataclass(frozen=True, slots=True)
class PackTaken:
    """What taking an update did, so the DM's Undo can put it back. `merged`: the campaign stopped
    following and the pack's entries became its own (ids in `added`)."""

    merged: bool
    before: Follow
    place: int  # its place in the order, for Undo of a merge
    after_version: int
    added: tuple[int, ...] = ()
    offered: int = 0  # the version that was on offer (an Undo declines it)
    skipped: int = 0  # entries not copied because the campaign already has that name


class PackUpdateStore(HomebrewPackStore):
    async def pending_pack_updates(
        self, guild_id: int, campaign_id: str, *, for_dmbot: bool = False
    ) -> list[PackUpdate]:
        """Newer versions of the packs this campaign follows. Read only. `for_dmbot`: DMbot is
        asking whether to take them, so a version an Undo sent back is skipped."""
        out: list[PackUpdate] = []
        async with self._db.guild(guild_id) as conn:
            for follow in await _followed(conn, guild_id, campaign_id):
                if follow.kind != HOMEBREW or not follow.updating:
                    continue
                try:
                    valid_content(follow.content)
                except BadPack:
                    continue  # a damaged copy is not offered an update
                fresh, _gone = await self._latest_of(conn, follow)
                if fresh is None or fresh.version <= follow.version:
                    continue
                if for_dmbot and fresh.version <= (follow.declined_version or 0):
                    continue
                out.append(
                    PackUpdate(follow, fresh, tuple(pack_diff.diff(follow.content, fresh.content)))
                )
        return out

    async def pending_pack_update(
        self, guild_id: int, campaign_id: str, set_id: str
    ) -> PackUpdate | None:
        return next(
            (
                u
                for u in await self.pending_pack_updates(guild_id, campaign_id)
                if u.follow.set_id == set_id
            ),
            None,
        )

    async def gone_packs(self, guild_id: int, campaign_id: str) -> list[Follow]:
        """Followed packs whose publisher stopped sharing (or is deleted) and that were still
        marked as updating."""
        out: list[Follow] = []
        async with self._db.guild(guild_id) as conn:
            for follow in await _followed(conn, guild_id, campaign_id):
                if follow.kind == HOMEBREW and follow.updating:
                    _, gone = await self._latest_of(conn, follow)
                    if gone:
                        out.append(follow)
        return out

    async def stop_updating_pack(self, guild_id: int, campaign_id: str, set_id: str) -> bool:
        """Mark one followed pack "no longer updated" (the campaign keeps its copy). True if it
        was changed now, so the note is said once."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE rule_set_follows SET updating = false WHERE guild_id = %s"
                " AND campaign_id = %s AND set_id = %s AND kind = %s AND updating",
                (guild_id, campaign_id, set_id, HOMEBREW),
            )
            return bool(cur.rowcount)

    async def take_pack_update(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int | None,
        set_id: str,
        accepted: Collection[str] | None = None,
        expected_version: int | None = None,
    ) -> PackTaken:
        """Take the pack's newer version. `accepted` None, or every changed entry: Follow. Otherwise
        Pick changes with something left out: the pack as the campaign had it plus the accepted
        changes becomes the campaign's own homebrew and the follow ends. Only a DM of the following
        campaign (`user_id` None: DMbot, at a session's start, only for Follow). The version is read
        again here; `expected_version` is the one the DM was shown."""
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
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            at = next((i for i, f in enumerate(mine) if f.set_id == set_id and f.updating), None)
            if at is None:
                raise RuleSetError(NOT_FOLLOWING)
            follow = mine[at]
            try:
                valid_content(follow.content)
            except BadPack:
                raise RuleSetError(NO_SUCH_SET) from None
            fresh, _gone = await self._latest_of(conn, follow)
            if fresh is None:
                raise RuleSetError(NO_SUCH_SET)
            if fresh.version <= follow.version:
                raise RuleSetError(NOTHING_NEW)
            if user_id is None and fresh.version <= (follow.declined_version or 0):
                raise RuleSetError(NOTHING_NEW)  # Undone: DMbot waits for a newer version
            if expected_version is not None and fresh.version != expected_version:
                raise RuleSetError(CHANGED_AGAIN)  # the choices were made on older changes
            changes = pack_diff.diff(follow.content, fresh.content)
            keys = {c.key for c in changes}
            if accepted is None or keys <= set(accepted):
                await conn.execute(
                    "UPDATE rule_set_follows SET version = %s, content = %s, set_name = %s,"
                    " publisher = %s, previous_version = %s, previous_content = %s,"
                    " taken_by = %s, taken_source = %s, declined_version = NULL"
                    " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s",
                    (
                        fresh.version, Jsonb(fresh.content), fresh.name, fresh.publisher,
                        follow.version, Jsonb(follow.content), user_id,
                        "auto" if user_id is None else "dm", guild_id, campaign_id, set_id,
                    ),
                )  # fmt: skip
                return PackTaken(False, follow, at, fresh.version, offered=fresh.version)
            if user_id is None:  # only a DM's press merges entries into the campaign's own
                raise RuleSetError(NOT_FOLLOWING)
            kept = keys & set(accepted)
            result = pack_diff.apply(follow.content, changes, kept)
            cur = await conn.execute(
                "SELECT name_key FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            have = {str(r["name_key"]) for r in await cur.fetchall()}
            added: list[int] = []
            skipped = 0
            try:
                for key, entry in pack_diff.entries_of(result).items():
                    if key in have:  # the campaign's own entry of that name wins
                        skipped += 1
                        continue
                    if "page" in entry:  # an imported entry: the same page, as the campaign's own
                        cur = await conn.execute(
                            "SELECT id FROM homebrew_pages WHERE url = %s"
                            " AND fetch_status <> 'restored'",
                            (entry["page"],),
                        )
                        page = await cur.fetchone()
                        if page is None:  # the page isn't kept here: nothing to point to
                            skipped += 1
                            continue
                        saved = await insert_entry(
                            conn, guild_id, campaign_id, user_id, entry["kind"], entry["name"],
                            None, None, now, now, source_kind="link", page_id=int(page["id"]),
                            credit_domain=entry["domain"],
                        )  # fmt: skip
                    elif entry.get("domain"):  # an upload credited to a website stays credited
                        saved = await insert_entry(
                            conn, guild_id, campaign_id, user_id, entry["kind"], entry["name"],
                            entry["text"], entry["summary"], now, now, source_kind="upload",
                            credit_kind="website", credit_domain=entry["domain"],
                        )  # fmt: skip
                    else:
                        saved = await insert_entry(
                            conn, guild_id, campaign_id, user_id, entry["kind"], entry["name"],
                            entry["text"], entry["summary"], now, now,
                        )  # fmt: skip
                    added.append(saved.id)
            except HomebrewError as exc:
                raise RuleSetError(str(exc)) from None
            await conn.execute(
                "DELETE FROM rule_set_follows WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id = %s",
                (guild_id, campaign_id, set_id),
            )
            rest = [f.set_id for f in mine if f.set_id != set_id]
            await _renumber(conn, guild_id, campaign_id, rest)
            version = fresh.version if kept else follow.version
            return PackTaken(True, follow, at, version, tuple(added), fresh.version, skipped)

    async def undo_last_pack_take(
        self, guild_id: int, campaign_id: str, user_id: int, set_id: str, expected_version: int
    ) -> Follow:
        """Undo the last Follow (the automatic one at a session's start, or a press): the copy
        before it comes back, from the follow's own row, so it works after a restart. Only while
        the campaign is still on `expected_version`, so an old button can't undo a newer update."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            mine = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOMEBREW and f.set_id == set_id
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
                (expected_version, guild_id, campaign_id, set_id, expected_version),
            )
            if await cur.fetchone() is None:
                raise RuleSetError(NOTHING_TO_UNDO)
            return mine[0]

    async def undo_pack_take(
        self, guild_id: int, campaign_id: str, user_id: int, taken: PackTaken
    ) -> int:
        """Put back what `take_pack_update` did. After a merge the entries it added are removed
        (those the DM has not edited since; edited ones stay) and the follow comes back in its old
        place. Returns how many merged entries stayed because they were edited."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            before = taken.before
            if not taken.merged:
                cur = await conn.execute(
                    "UPDATE rule_set_follows SET version = %s, content = %s, set_name = %s,"
                    " publisher = %s, previous_version = NULL, previous_content = NULL,"
                    " taken_by = NULL, taken_source = NULL, declined_version = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s AND version = %s",
                    (
                        before.version, Jsonb(before.content), before.name, before.publisher,
                        taken.offered or taken.after_version, guild_id, campaign_id,
                        before.set_id, taken.after_version,
                    ),
                )  # fmt: skip
                if cur.rowcount == 0:
                    raise RuleSetError(NOT_FOLLOWING)
                return 0
            mine = [f for f in await _followed(conn, guild_id, campaign_id) if f.kind == HOMEBREW]
            if any(f.set_id == before.set_id for f in mine):
                raise RuleSetError(ALREADY_FOLLOWING)
            if len(mine) >= PACKS_MAX:  # the merge freed a slot that another pack has taken
                raise RuleSetError(TOO_MANY_PACKS)
            cur = await conn.execute(
                "DELETE FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s) AND version = 1",  # never edited
                (guild_id, campaign_id, list(taken.added)),
            )
            kept = len(taken.added) - int(cur.rowcount)
            offer = Offer(
                before.set_id, before.kind, before.name, before.publisher, before.version,
                before.content, before.code,
            )  # fmt: skip
            await conn.execute(
                _INSERT_FOLLOW,
                _follow_values(
                    guild_id, campaign_id, user_id, before.followed_at, offer, len(mine),
                    before.updating,
                ),
            )  # fmt: skip
            await conn.execute(  # DMbot must not take the rejected version again (only a newer)
                "UPDATE rule_set_follows SET declined_version = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND set_id = %s",
                (taken.offered, guild_id, campaign_id, before.set_id),
            )
            ids = [f.set_id for f in mine]
            ids.insert(min(max(taken.place, 0), len(ids)), before.set_id)
            await _renumber(conn, guild_id, campaign_id, ids)
            return kept
