"""Suggest to <pack> (#1537): a following campaign's DM sends one of its own homebrew entries to
the pack's publisher, as #1259 does for house-rules sets.

Only the entry's words cross (kind, name, summary, text), never who
sent it: the publisher's table holds no sender, server or campaign. An entry that came from a
book (`from_book`, #1524) is never sent: book text stays in its campaign (CLAUDE.md, IP rule).
The publisher's DM answers with Add to pack or No thanks; adding writes the entry into the
publishing campaign's own homebrew (a new name, or in place of its own entry of that name) and
makes the pack's next version through the same checks as Publish my changes (a DM, a paid plan,
nothing from a book). The sender hears the outcome in one line at its next session start.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from dmbot.rules import pack_content
from dmbot.rules.homebrew import (
    _COLUMNS,
    _FROM,
    KINDS,
    HomebrewError,
    _entry,
    insert_entry,
    name_key,
)
from dmbot.rules.homebrew_packs import HOMEBREW, _add_version, _next_version
from dmbot.rules.house import require_dm
from dmbot.rules.pack_updates import PackUpdateStore
from dmbot.rules.rule_sets import (
    NOT_FOLLOWING,
    NOT_PAID,
    SUGGEST_FOLLOWER_MAX,
    SUGGEST_SET_MAX,
    SUGGEST_WAIT_S,
    SUGGESTION_GONE,
    RuleSetError,
    _followed,
    _json,
    valid_content,
)

NO_SUCH_ENTRY = "That homebrew entry isn't here any more."
FROM_A_BOOK = "That entry came from a book you shared, so it stays in this campaign."
FROM_A_LINK = "That entry comes from a link, so it can't be sent to a pack yet."
LINK_NAME = (
    "You have an entry of that name from a link, which stays as it is, so it wasn't replaced."
)
ENTRY_CHANGED = "That entry was changed since you looked, so nothing was sent. Look at it again."
ALREADY_SENT = "This campaign already sent that entry. The answer comes at your next session."
PICK_A_PACK = "This campaign follows more than one pack. Pick which one to send it to."
NOT_THAT_PACK = "This campaign doesn't follow that pack any more. Open the entry again."
PACK_HAS_ENOUGH = "That pack has plenty of suggestions waiting. Try again next week."
PACK_HAS_THAT = "That pack already has a suggestion for that name waiting. Try again later."
TOO_MANY_SENT = (
    f"Your campaign already has {SUGGEST_FOLLOWER_MAX} suggestions waiting for an answer. "
    "Try again after you hear back."
)
NOT_SHARED_ANY_MORE = "That pack isn't shared any more."
EXPIRED = "That suggestion is too old to add. Its sender can send it again."
ALREADY_IN_PACK = "The pack already has that entry as written, so nothing was added. It is closed."
BOOK_NAME = (
    "You have an entry of that name from a book, which stays as it is, so it wasn't replaced."
)
NO_ROOM = "Your homebrew is full, so the entry wasn't added. Remove one first."


@dataclass(frozen=True, slots=True)
class PackSuggestion:
    """An entry a table that follows the pack suggests: its words and nothing about the sender."""

    id: str
    set_id: str
    kind: str
    name: str
    summary: str | None
    text: str
    known: bool
    status: str  # 'open', 'accepted', 'declined', 'present' or (read-time) 'expired'
    created_at: int


@dataclass(frozen=True, slots=True)
class PackSent:
    """A suggestion this campaign sent, and the answer once the publisher has given one."""

    id: str
    set_name: str
    name: str
    status: str  # 'open', 'accepted', 'declined', 'present', 'gone' or 'expired'
    version: int | None


def _suggestion(row: dict[str, Any], status: str | None = None) -> PackSuggestion:
    return PackSuggestion(
        str(row["id"]), str(row["set_id"]), str(row["kind"]), str(row["name"]), row["summary"],
        str(row["text"]), bool(row["known"]), status or str(row["status"]), int(row["created_at"]),
    )  # fmt: skip


_COLS = "id, set_id, kind, name, summary, text, known, status, created_at"


class PackSuggestionStore(PackUpdateStore):
    # ---- the sender's side ----------------------------------------------------------

    async def suggest_entry(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        shown_version: int,
        set_id: str | None = None,
    ) -> tuple[str, int, str, str]:
        """A DM of a following campaign sends one of the campaign's own homebrew entries to a
        pack's publisher. Returns (the suggestion's id, the publisher's server, its campaign, the
        pack's name): the middle two are for DMbot to post the note and are never shown. `set_id`
        picks the pack when the campaign follows several. `shown_version`: the entry's version as
        the DM was shown it; if it was edited since, nothing is sent. Limits:
        `SUGGEST_FOLLOWER_MAX` waiting from this campaign, `SUGGEST_SET_MAX` open on the pack."""
        now = self._now()
        sid = secrets.token_hex(16)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            follows = [
                f
                for f in await _followed(conn, guild_id, campaign_id)
                if f.kind == HOMEBREW and f.updating
            ]
            if not follows:
                raise RuleSetError(NOT_FOLLOWING)
            if set_id is None:
                if len(follows) > 1:
                    raise RuleSetError(PICK_A_PACK)
                follow = follows[0]
            else:
                found = next((f for f in follows if f.set_id == set_id), None)
                if found is None:
                    raise RuleSetError(NOT_THAT_PACK)
                follow = found
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s AND h.campaign_id = %s"
                " AND h.id = %s",
                (guild_id, campaign_id, entry_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuleSetError(NO_SUCH_ENTRY)
            entry = _entry(row)
            if entry.from_book:
                raise RuleSetError(FROM_A_BOOK)
            if entry.source_kind == "link" or entry.credit_domain:  # (by reference, #1635)
                raise RuleSetError(FROM_A_LINK)
            if entry.version != shown_version:
                raise RuleSetError(ENTRY_CHANGED)
            cur = await conn.execute(
                "SELECT count(*) AS n, count(*) FILTER (WHERE lower(name) = lower(%s)"
                " AND set_id = %s) AS same FROM pack_suggestions_sent"
                " WHERE guild_id = %s AND campaign_id = %s AND told_at IS NULL",
                (entry.name, follow.set_id, guild_id, campaign_id),
            )
            counts = await cur.fetchone()
            assert counts is not None
            if counts["same"]:
                raise RuleSetError(ALREADY_SENT)
            if counts["n"] >= SUGGEST_FOLLOWER_MAX:
                raise RuleSetError(TOO_MANY_SENT)
            # (`known` is always true: nothing is hidden from players any more, part 2. The column
            # stays so suggestions already stored and the door keep their shape.)
            cur = await conn.execute(
                "SELECT outcome, pub_guild, pub_campaign FROM dmbot_suggest_entry("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (sid, follow.set_id, campaign_id, entry.kind, entry.name, entry.summary,
                 entry.text, True, now, SUGGEST_SET_MAX, SUGGEST_WAIT_S),
            )  # fmt: skip
            door = await cur.fetchone()
            assert door is not None
            if door["outcome"] == "full":
                raise RuleSetError(PACK_HAS_ENOUGH)
            if door["outcome"] == "dup":
                raise RuleSetError(PACK_HAS_THAT)
            if door["outcome"] != "ok":
                raise RuleSetError(NOT_SHARED_ANY_MORE)
            await conn.execute(
                "INSERT INTO pack_suggestions_sent (guild_id, campaign_id, id, set_id, set_name,"
                " name, sent_by, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, sid, follow.set_id, follow.name, entry.name, user_id, now),
            )
        return sid, int(door["pub_guild"]), str(door["pub_campaign"]), follow.name

    async def pack_sent_answers(self, guild_id: int, campaign_id: str) -> list[PackSent]:
        """The suggestions this campaign sent that its DM has not been told the end of, with the
        answers the publisher has given (asked by their random ids; nothing else is reachable)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, set_name, name, sent_at FROM pack_suggestions_sent"
                " WHERE guild_id = %s AND campaign_id = %s AND told_at IS NULL ORDER BY sent_at",
                (guild_id, campaign_id),
            )
            sent = await cur.fetchall()
            if not sent:
                return []
            cur = await conn.execute(
                "SELECT id, status, version FROM dmbot_pack_suggestion_answers(%s)",
                (",".join(str(r["id"]) for r in sent),),
            )
            answers = {str(r["id"]): r for r in await cur.fetchall()}
        out = []
        for r in sent:
            a = answers.get(str(r["id"]))
            # No row back: the publisher's pack is gone, so there will be no answer. Told once
            # ("gone"), which frees the slot.
            status = str(a["status"]) if a else "gone"
            if status == "open" and self._now() - int(r["sent_at"]) > SUGGEST_WAIT_S:
                status = "expired"  # told once, so the slot is freed
            out.append(
                PackSent(
                    str(r["id"]), str(r["set_name"]), str(r["name"]), status,
                    int(a["version"]) if a and a["version"] is not None else None,
                )
            )  # fmt: skip
        return out

    async def mark_pack_told(self, guild_id: int, campaign_id: str, ids: list[str]) -> None:
        """The DM has been told these were answered: they stop counting toward the limit."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE pack_suggestions_sent SET told_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = ANY(%s)",
                (self._now(), guild_id, campaign_id, ids),
            )

    # ---- the publisher's side -------------------------------------------------------

    async def pack_suggestion(
        self, guild_id: int, campaign_id: str, suggestion_id: str
    ) -> PackSuggestion | None:
        """One suggestion on the campaign's own pack, or None. An open one past the wait is
        'expired' (the sender was told it may send it again; it can't be added)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM pack_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, suggestion_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        expired = str(row["status"]) == "open" and (
            self._now() - int(row["created_at"]) > SUGGEST_WAIT_S
        )
        return _suggestion(row, "expired" if expired else None)

    async def unnoted_pack_suggestions(
        self, guild_id: int, campaign_id: str
    ) -> list[tuple[PackSuggestion, str]]:
        """Open suggestions whose note never reached the DM screen (no channel, or Discord
        refused), each with the pack's name, oldest first: DMbot posts them at a session's
        start."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT q.id, q.set_id, q.kind, q.name, q.summary, q.text, q.known, q.status,"
                " q.created_at, s.name AS pack FROM pack_suggestions q JOIN rule_sets s"
                " ON s.guild_id = q.guild_id AND s.campaign_id = q.campaign_id AND s.id = q.set_id"
                " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'open'"
                " AND q.noted_at IS NULL AND s.status = 'shared' AND q.created_at > %s"
                " ORDER BY q.created_at",
                (guild_id, campaign_id, self._now() - SUGGEST_WAIT_S),
            )
            rows = await cur.fetchall()
        return [(_suggestion(r), str(r["pack"])) for r in rows]

    async def mark_pack_noted(self, guild_id: int, campaign_id: str, suggestion_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE pack_suggestions SET noted_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (self._now(), guild_id, campaign_id, suggestion_id),
            )

    async def decline_pack_suggestion(
        self, guild_id: int, campaign_id: str, user_id: int, suggestion_id: str
    ) -> bool:
        """No thanks: closes the suggestion and changes nothing else. False: it was already
        answered."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "UPDATE pack_suggestions SET status = 'declined', resolved_at = %s,"
                " resolved_by = %s WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND status = 'open'",
                (self._now(), user_id, guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)

    async def accept_pack_suggestion(
        self, guild_id: int, campaign_id: str, user_id: int, suggestion_id: str, *, plan_ok: bool
    ) -> tuple[str, int]:
        """The publisher's DMs add the suggested entry to the campaign's own homebrew (a new
        name, or in place of its own entry of that name) and make the pack's next version, in one
        transaction. Same checks as Publish my changes: a DM, a paid plan, and nothing from a
        book (an entry of that name that came from a book is never replaced). Returns (the pack's
        name, the new version)."""
        if not plan_ok:
            raise RuleSetError(NOT_PAID)
        now = self._now()
        present = False
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                f"SELECT {_COLS} FROM pack_suggestions WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s FOR UPDATE",
                (guild_id, campaign_id, suggestion_id),
            )
            row = await cur.fetchone()
            if row is None or row["status"] != "open" or row["kind"] not in KINDS:
                raise RuleSetError(SUGGESTION_GONE)  # (a "rule" suggested before #1628 is not one)
            if now - int(row["created_at"]) > SUGGEST_WAIT_S:
                raise RuleSetError(EXPIRED)
            cur = await conn.execute(
                "SELECT id, name FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND kind = %s AND status = 'shared'",
                (guild_id, campaign_id, row["set_id"], HOMEBREW),
            )
            pack = await cur.fetchone()
            if pack is None:
                raise RuleSetError(NOT_SHARED_ANY_MORE)
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s AND h.campaign_id = %s"
                " AND h.name_key = %s",
                (guild_id, campaign_id, name_key(str(row["name"]))),
            )
            mine = await cur.fetchone()
            if mine is not None and mine["from_book"]:
                raise RuleSetError(BOOK_NAME)
            if mine is not None and mine["source_kind"] == "link":
                raise RuleSetError(LINK_NAME)  # its words are a page's, not the pack's to replace
            unchanged = mine is not None and (
                (mine["kind"], mine["name"], mine["text"], mine["summary"])
                == (row["kind"], row["name"], row["text"], row["summary"])
            )
            try:
                if unchanged:
                    pass  # same as the publisher's own: no needless version bump
                elif mine is not None:
                    await conn.execute(
                        "UPDATE homebrew_entries SET kind = %s, name = %s, name_key = %s,"
                        " text = %s, summary = %s, updated_at = %s,"
                        " version = version + 1 WHERE guild_id = %s AND campaign_id = %s"
                        " AND id = %s",
                        (row["kind"], row["name"], name_key(str(row["name"])), row["text"],
                         row["summary"], now, guild_id, campaign_id, mine["id"]),
                    )  # fmt: skip
                else:
                    await insert_entry(
                        conn, guild_id, campaign_id, user_id, str(row["kind"]), str(row["name"]),
                        str(row["text"]), row["summary"], now, now,
                    )  # fmt: skip
            except HomebrewError as exc:
                raise RuleSetError(str(exc) or NO_ROOM) from None
            after = await self._pack_entries(conn, guild_id, campaign_id)
            content, _ = pack_content.content_of(after)
            cur = await conn.execute(
                "SELECT content FROM rule_set_versions WHERE guild_id = %s AND campaign_id = %s"
                " AND set_id = %s ORDER BY version DESC LIMIT 1",
                (guild_id, campaign_id, pack["id"]),
            )
            last = await cur.fetchone()
            same = last is not None and valid_content(_json(last["content"]), HOMEBREW) == content
            if same:
                present = True  # nothing new for the pack: close it, below
                await conn.execute(
                    "UPDATE pack_suggestions SET status = 'present', resolved_at = %s,"
                    " resolved_by = %s WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                    (now, user_id, guild_id, campaign_id, suggestion_id),
                )
            else:
                version = await _next_version(conn, guild_id, campaign_id, str(pack["id"]))
                await _add_version(
                    conn, guild_id, campaign_id, str(pack["id"]), version, content, user_id, now
                )
                await conn.execute(
                    "UPDATE pack_suggestions SET status = 'accepted', resolved_at = %s,"
                    " resolved_by = %s, version = %s WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = %s",
                    (now, user_id, version, guild_id, campaign_id, suggestion_id),
                )
        if present:
            raise RuleSetError(ALREADY_IN_PACK)
        return str(pack["name"]), version

    async def _pack_entries(self, conn: Any, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s AND h.campaign_id = %s"
            " ORDER BY h.created_at, h.id",
            (guild_id, campaign_id),
        )
        return [_entry(r) for r in await cur.fetchall()]
