"""Homebrew: a campaign's own spells, monsters, items and other game content (docs/PLAN.md,
"Homebrew (part 1)"; #1520). A rule the DM makes up is a house rule, not homebrew (owner,
2026-10-16, #1628): `KINDS` has no "rule".

An entry is the DM's own creation in the DM's own words, never a published book's text (the form
says so; a rulebook goes through "Share a rulebook"). It is **on** or **off** (owner,
2026-10-16, "Homebrew (part 2)"): nothing is hidden from players any more. An entry that is off is
kept, but DMbot uses it nowhere at the table.

Each campaign keeps its own, never shared between campaigns or servers. Only the campaign's DMs
change anything: every change checks that in the same transaction, so a caller that forgets still
can't write. Every query filters on the server and the campaign and runs inside `Database.guild`,
where row-level security hides every other server's rows too.

Two DMs may look at the same entry. Each entry counts its changes (`version`); an edit or removal
says which version the DM was shown, and if another DM changed it meanwhile nothing is done.

Backups carry them (`HomebrewSection`), validated field by field, as the file is untrusted.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.rules.site_urls import normalise_domain, normalise_url

KINDS = (
    "spell", "monster", "item", "feat", "condition", "species", "subclass", "background", "other",
)  # fmt: skip
RULE_KIND = "rule"  # no longer a kind (#1628): kept to recognise and refuse it, or move it
NAME_MAX = 80
TEXT_MAX = 2000
SUMMARY_MAX = 160
HOMEBREW_MAX = 200  # per campaign, so a campaign (and its backup) stays a sensible size
INT64_MAX = 2**63 - 1

NO_CAMPAIGN = "That campaign isn't here any more."
NOT_DM = (
    "Only this campaign's DMs can change its homebrew. You can still ask about it with "
    "`/dmbot rule`."
)
GONE = "That homebrew entry isn't here any more."
FROM_A_LINK = "That entry comes from a link, so its words can't be changed here."
CHANGED = "Another DM changed that entry while you were looking."
SAME_NAME = (
    "This campaign already has homebrew with that name. Pick another name, or edit that one."
)
FULL = (
    f"This campaign already has {HOMEBREW_MAX} homebrew entries, so yours wasn't saved. "
    "Remove one first, then add it again."
)
BAD_KIND = "That isn't one of the kinds. Pick one from the list."
NOT_HOMEBREW = "Rules go in House rules, not Homebrew: ⚙️ Manage DMbot → House rules."
DAMAGED = "This backup file is damaged (bad homebrew entry)."


class HomebrewError(CampaignError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class Homebrew:
    id: int
    campaign_id: str
    kind: str
    name: str
    text: str
    summary: str | None
    created_by: int
    created_at: int
    updated_at: int
    version: int = 1
    # Loaded from a file after the DM said some of it is from a published book and confirmed the
    # right to use it (#1524): it stays in this campaign and can never go into a shared ruleset.
    from_book: bool = False
    # Which rules that book is for (#1563): "2024", "2014" or "other"; None for an entry that is
    # not from a book, and for one loaded before the question was asked (read as "other").
    book_edition: str | None = None
    # Where a followed pack's entry comes from, as an answer cites it: "Frost Pack homebrew, v2"
    # (#1527). None for the campaign's own entry. A pack's entry is read-only here: it has no id
    # of its own in this campaign (`id` is negative) and is never edited, only replaced by the
    # campaign's own entry of the same name.
    source: str | None = None
    # Turned off by the DM (#1629): kept, but DMbot uses it nowhere at the table.
    enabled: bool = True
    # Where the entry came from (#1633): "typed" (added_by is the DM who typed it), "upload" (a
    # file: added_by is the uploader, credited as `credit_kind` says: "user" the uploader, "website"
    # a site named by `credit_domain`, "name" the words in `credit_text`) or "link" (a published
    # page: the words are read from it, `page_url` is its address and `credit_domain` its site).
    source_kind: str = "typed"
    added_by: int = 0
    page_id: int | None = None
    credit_kind: str | None = None
    credit_text: str | None = None
    credit_domain: str | None = None
    page_url: str | None = None

    def shown(self) -> str:
        """The one line alerts and lists show: the summary, else the start of the text."""
        if self.summary:
            return self.summary
        flat = " ".join(self.text.split())
        return flat if len(flat) <= SUMMARY_MAX else flat[: SUMMARY_MAX - 1].rstrip() + "…"


def name_key(name: str) -> str:
    """The name for comparing: case-folded, one space between words."""
    return " ".join(name.casefold().split())


def credit_label(entry: Homebrew) -> str:
    """Who or what an entry is credited to, in plain words, for the card and the entry view
    (#1646): "from dndbeyond.com" (a page), "credited to dndbeyond.com" (a website's upload),
    "by Jo" (an upload credited to a person), or "" (the DM's own). Unescaped: the caller
    escapes it for Discord."""
    if entry.credit_domain:
        return f"{'from' if entry.source_kind == 'link' else 'credited to'} {entry.credit_domain}"
    if entry.credit_kind == "name" and entry.credit_text:
        return f"by {entry.credit_text}"
    return ""


def clean_name(raw: str) -> str:
    """The name on one line. Raises HomebrewError if empty or too long."""
    name = " ".join(_plain(raw).split())
    if not name:
        raise HomebrewError("The name box was empty, so nothing was saved.")
    if len(name) > NAME_MAX:
        raise HomebrewError(
            f"That name is too long, so nothing was saved. Keep it under {NAME_MAX} characters."
        )
    return name


def clean_text(raw: str) -> str:
    """The entry's words as plain text: line breaks kept (at most one blank line in a row),
    control and invisible direction characters removed. Raises HomebrewError if empty or too
    long."""
    lines = [" ".join(line.split()) for line in _plain(raw, keep_breaks=True).split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    if not text:
        raise HomebrewError("The text box was empty, so nothing was saved.")
    if len(text) > TEXT_MAX:
        raise HomebrewError(
            f"That's too long, so nothing was saved. Keep the text under {TEXT_MAX} characters."
        )
    return text


def clean_summary(raw: str | None) -> str | None:
    """The one-line summary: None when left empty. Raises HomebrewError if too long."""
    summary = " ".join(_plain(raw or "").split())
    if len(summary) > SUMMARY_MAX:
        raise HomebrewError(
            f"The summary is too long, so nothing was saved. Keep it under {SUMMARY_MAX} "
            "characters."
        )
    return summary or None


def clean_kind(raw: str) -> str:
    kind = raw.strip().lower()
    if kind == RULE_KIND:
        raise HomebrewError(NOT_HOMEBREW)
    if kind not in KINDS:
        raise HomebrewError(BAD_KIND)
    return kind


def _plain(raw: str, *, keep_breaks: bool = False) -> str:
    """Without control and invisible direction characters (they can flip how a line reads)."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    keep = {"\n", "\t"} if keep_breaks else {"\t"}
    return "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Cc") or c in keep)


BOOK_EDITIONS = ("2024", "2014", "other")  # which rules a shared book is for (#1563)


# An imported entry's words and summary are read from its page (#1633), so every read joins it.
_COLUMNS = (
    "h.id, h.campaign_id, h.kind, h.name, COALESCE(h.text, p.text) AS text,"
    " COALESCE(h.summary, p.summary) AS summary, h.created_by, h.created_at, h.updated_at,"
    " h.version, h.from_book, h.book_edition, h.enabled, h.source_kind, h.added_by, h.page_id,"
    " h.credit_kind, h.credit_text, h.credit_domain, p.url AS page_url"
)
_FROM = "homebrew_entries h LEFT JOIN homebrew_pages p ON p.id = h.page_id"
# What DMbot may use at the table (#1629, #1633), in one place: the DM has not turned it off, and
# if it comes from a site (a link, or an upload credited to a website) the campaign has approved
# that site.
_USABLE = (
    " AND h.enabled AND (h.credit_domain IS NULL OR EXISTS (SELECT 1 FROM site_approvals a"
    " WHERE a.guild_id = h.guild_id AND a.campaign_id = h.campaign_id"
    " AND a.domain = h.credit_domain))"
)


def _joined(changed: str) -> str:
    """The columns of the rows a `WITH changed AS (INSERT/UPDATE/DELETE … RETURNING *)` touched,
    with their pages (a statement's RETURNING can't join)."""
    return f"SELECT {_COLUMNS} FROM {changed} h LEFT JOIN homebrew_pages p ON p.id = h.page_id"


def _entry(row: dict[str, Any]) -> Homebrew:
    return Homebrew(
        int(row["id"]),
        row["campaign_id"],
        row["kind"],
        row["name"],
        row["text"],
        row["summary"],
        int(row["created_by"]),
        int(row["created_at"]),
        int(row["updated_at"]),
        int(row["version"]),
        bool(row["from_book"]),
        row["book_edition"],
        enabled=bool(row["enabled"]),
        source_kind=row["source_kind"],
        added_by=int(row["added_by"]),
        page_id=None if row["page_id"] is None else int(row["page_id"]),
        credit_kind=row["credit_kind"],
        credit_text=row["credit_text"],
        credit_domain=row["credit_domain"],
        page_url=row["page_url"],
    )


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One entry read from a homebrew file (`dmbot.rules.homebrew_file`)."""

    kind: str
    name: str
    text: str
    summary: str | None = None
    enabled: bool | None = None  # None: the file doesn't say (`## Name (kind, off)` says off)


@dataclass(frozen=True, slots=True)
class Loaded:
    """What loading a file did: how many entries were new, changed, or already the same."""

    added: int
    updated: int
    same: int


class HomebrewStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def list(
        self, guild_id: int, campaign_id: str, *, usable_only: bool = False
    ) -> list[Homebrew]:
        """The campaign's homebrew, by name. `usable_only`: not the entries the DM turned
        off (#1629). Everything DMbot uses at the table reads through `HomebrewReader`, which
        asks for this; the DM's own list, the file and the backup see every entry."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s AND h.campaign_id = %s"
                + (_USABLE if usable_only else "")
                + " ORDER BY h.name_key",
                (guild_id, campaign_id),
            )
            return [_entry(r) for r in await cur.fetchall()]

    async def find(
        self, guild_id: int, campaign_id: str, name: str, *, usable_only: bool = False
    ) -> Homebrew | None:
        """The campaign's entry with exactly this name (any case), by the unique index.
        `usable_only`: None for an entry the DM turned off (#1629)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s"
                " AND h.campaign_id = %s AND h.name_key = %s" + (_USABLE if usable_only else ""),
                (guild_id, campaign_id, name_key(name)),
            )
            row = await cur.fetchone()
        return None if row is None else _entry(row)

    async def get(self, guild_id: int, campaign_id: str, entry_id: int) -> Homebrew | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s"
                " AND h.campaign_id = %s AND h.id = %s",
                (guild_id, campaign_id, entry_id),
            )
            row = await cur.fetchone()
        return None if row is None else _entry(row)

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        *,
        kind: str,
        name: str,
        text: str,
        summary: str | None = None,
    ) -> Homebrew:
        """Save a new entry. Only the campaign's DMs; at most `HOMEBREW_MAX`; the name is
        unique in the campaign, whatever its case."""
        clean = (clean_kind(kind), clean_name(name), clean_text(text), clean_summary(summary))
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            # Locking the campaign makes two adds at once take turns: the limit and the name hold.
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return await insert_entry(conn, guild_id, campaign_id, user_id, *clean, now, now)

    async def edit(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        *,
        kind: str,
        name: str,
        text: str,
        summary: str | None = None,
        unchanged_since: int | None = None,
    ) -> Homebrew:
        """Change an entry. Only the campaign's DMs. `unchanged_since`: the entry's `version` as
        the DM was shown it; if another DM changed it meanwhile nothing is saved (`CHANGED`)."""
        kind_, name_, text_, summary_ = (
            clean_kind(kind),
            clean_name(name),
            clean_text(text),
            clean_summary(summary),
        )
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " AND name_key = %s AND id <> %s",
                (guild_id, campaign_id, name_key(name_), entry_id),
            )
            if await cur.fetchone() is not None:
                raise HomebrewError(SAME_NAME)
            cur = await conn.execute(
                "SELECT 1 AS ok FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND source_kind = 'link'",
                (guild_id, campaign_id, entry_id),
            )
            if await cur.fetchone() is not None:  # its words are the page's, not the campaign's
                raise HomebrewError(FROM_A_LINK)
            cur = await conn.execute(
                "WITH changed AS (UPDATE homebrew_entries SET kind = %s, name = %s,"
                " name_key = %s, text = %s, summary = %s, updated_at = %s, version = version + 1"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND (%s::int IS NULL OR version = %s) RETURNING *) " + _joined("changed"),
                (
                    kind_,
                    name_,
                    name_key(name_),
                    text_,
                    summary_,
                    int(self._clock()),
                    guild_id,
                    campaign_id,
                    entry_id,
                    unchanged_since,
                    unchanged_since,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, entry_id)
            return _entry(row)

    async def set_enabled(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        enabled: bool,
        *,
        unchanged_since: int | None = None,
    ) -> Homebrew:
        """Turn an entry on or off for the table (#1629). Only the campaign's DMs. The entry is
        kept either way; its `version` counts the change, so an edit made from an old view is
        told from one made on the current entry. Turning it back on asks nothing."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "WITH changed AS (UPDATE homebrew_entries SET enabled = %s, version = version + 1"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND (%s::int IS NULL OR version = %s) RETURNING *) " + _joined("changed"),
                (enabled, guild_id, campaign_id, entry_id, unchanged_since, unchanged_since),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, entry_id)
            return _entry(row)

    async def remove(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entry_id: int,
        *,
        unchanged_since: int | None = None,
    ) -> Homebrew:
        """Take an entry away; returns it (so it can be put back). Only the campaign's DMs."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "WITH changed AS (DELETE FROM homebrew_entries WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND (%s::int IS NULL OR version = %s)"
                " RETURNING *) " + _joined("changed"),
                (guild_id, campaign_id, entry_id, unchanged_since, unchanged_since),
            )
            row = await cur.fetchone()
            if row is None:
                raise await _missing(conn, guild_id, campaign_id, entry_id)
            return _entry(row)

    async def load_file(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        entries: Sequence[FileEntry],
        *,
        from_book: bool = False,
        book_edition: str | None = None,
        credit: tuple[str, str | None, str | None] = ("user", None, None),
    ) -> Loaded:
        """Load a file's entries in one step, or none (#1524). An entry whose name the campaign
        already has (any case) is updated (version + 1) if anything differs, else left alone; a
        new one is added. A line saying nothing about on or off keeps an old entry's setting
        and makes a new one on. If the campaign would end up over `HOMEBREW_MAX`, nothing is
        loaded and the DM is told the count. `from_book`: the DM confirmed the right to use this
        material (the caller recorded who and when); it marks what is added or changed here so
        it can never go into a shared ruleset. `book_edition`: which rules the book is for
        (2024, 2014 or other), asked of the DM; it sets where the entry ranks. Only the
        campaign's DMs. `credit` (#1633): who the new entries are credited to, as (kind, text,
        domain): ("user", None, None) the person loading, ("website", None, "site.com") a site
        (it needs the campaign's approval to be used), ("name", "a name", None) someone else."""
        credit_kind, credit_text, credit_domain = self._clean_credit(credit)
        if book_edition is not None and (not from_book or book_edition not in BOOK_EDITIONS):
            raise HomebrewError(
                "DMbot doesn't know which rules that book is for. Nothing was loaded."
            )
        cleaned = [
            (
                clean_kind(e.kind),
                clean_name(e.name),
                clean_text(e.text),
                clean_summary(e.summary),
                e.enabled,
            )
            for e in entries
        ]
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM {_FROM} WHERE h.guild_id = %s AND h.campaign_id = %s",
                (guild_id, campaign_id),
            )
            have = {name_key(r["name"]): _entry(r) for r in await cur.fetchall()}
            new = [c for c in cleaned if name_key(c[1]) not in have]
            if len(have) + len(new) > HOMEBREW_MAX:
                raise HomebrewError(
                    f"That would make {len(have) + len(new)} entries, and the most is "
                    f"{HOMEBREW_MAX}. Nothing was loaded. Remove some entries or leave some "
                    "out of the file."
                )
            added = updated = same = 0
            for kind, name, text, summary, switch in cleaned:
                old = have.get(name_key(name))
                if old is None:
                    await insert_entry(
                        conn,
                        guild_id,
                        campaign_id,
                        user_id,
                        kind,
                        name,
                        text,
                        summary,
                        now,
                        now,
                        from_book,
                        book_edition,
                        True if switch is None else switch,
                        source_kind="upload",  # a loaded file, credited as the DM chose
                        added_by=user_id,
                        credit_kind=credit_kind,
                        credit_text=credit_text,
                        credit_domain=credit_domain,
                    )
                    added += 1
                    continue
                if old.source_kind == "link":  # its words are the page's, not a file's
                    same += 1
                    continue
                on = old.enabled if switch is None else switch  # (not said: as it was)
                if (
                    (old.kind, old.name, old.text, old.summary, old.enabled)
                    == (kind, name, text, summary, on)
                    and (old.from_book or not from_book)
                    and (not from_book or old.book_edition == book_edition)
                ):
                    same += 1
                    continue
                await conn.execute(
                    "UPDATE homebrew_entries SET kind = %s, name = %s, text = %s, summary = %s,"
                    " enabled = %s, updated_at = %s, version = version + 1,"
                    " from_book = from_book OR %s,"
                    " book_edition = CASE WHEN %s THEN %s ELSE book_edition END"
                    " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                    (
                        kind,
                        name,
                        text,
                        summary,
                        on,
                        now,
                        from_book,
                        from_book,
                        book_edition,
                        guild_id,
                        campaign_id,
                        old.id,
                    ),
                )
                updated += 1
        return Loaded(added, updated, same)

    @staticmethod
    def _clean_credit(
        credit: tuple[str, str | None, str | None],
    ) -> tuple[str, str | None, str | None]:
        kind, text, domain = credit
        if kind == "user" and text is None and domain is None:
            return credit
        if (
            kind == "website"
            and text is None
            and domain is not None
            and normalise_domain(domain) == domain
        ):
            return credit
        if kind == "name" and domain is None and text is not None:
            return kind, clean_name(text), None
        raise HomebrewError("DMbot couldn't read who made this. Nothing was loaded.")

    async def put_back(
        self, guild_id: int, campaign_id: str, user_id: int, entry: Homebrew
    ) -> Homebrew:
        """Undo a removal: the entry as it was (its dates too), as a new row. Only the campaign's
        DMs; refused like an add if the name is taken again or the campaign is full."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return await insert_entry(
                conn,
                guild_id,
                campaign_id,
                entry.created_by,
                entry.kind,
                entry.name,
                None if entry.source_kind == "link" else entry.text,  # (a link's are the page's)
                None if entry.source_kind == "link" else entry.summary,
                entry.created_at,
                entry.updated_at,
                entry.from_book,
                entry.book_edition,
                entry.enabled,
                source_kind=entry.source_kind,
                added_by=entry.added_by or entry.created_by,
                page_id=entry.page_id,
                credit_kind=entry.credit_kind,
                credit_text=entry.credit_text,
                credit_domain=entry.credit_domain,
            )

    async def add_link(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        *,
        page_id: int,
        kind: str,
        name: str,
        domain: str,
    ) -> Homebrew:
        """Save an entry whose words are a published page's (#1633): the campaign's row keeps its
        name, kind, on/off and source; the words and summary are read from the page. Only the
        campaign's DMs; at most `HOMEBREW_MAX`; the name is unique in the campaign. It can be used
        only once the campaign has approved the page's site (`_USABLE`)."""
        clean = (clean_kind(kind), clean_name(name))
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            return await insert_entry(
                conn, guild_id, campaign_id, user_id, *clean, None, None, now, now,
                source_kind="link", page_id=page_id, credit_domain=domain,
            )  # fmt: skip


async def _missing(conn: Conn, guild_id: int, campaign_id: str, entry_id: int) -> HomebrewError:
    """Why a change touched no entry: it's gone, or another DM changed it (it's still here)."""
    cur = await conn.execute(
        "SELECT 1 AS ok FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s AND id = %s",
        (guild_id, campaign_id, entry_id),
    )
    return HomebrewError(GONE if await cur.fetchone() is None else CHANGED)


async def insert_entry(
    conn: Conn,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    kind: str,
    name: str,
    text: str | None,
    summary: str | None,
    created_at: int,
    updated_at: int,
    from_book: bool = False,
    book_edition: str | None = None,
    enabled: bool = True,
    *,
    source_kind: str = "typed",
    added_by: int | None = None,
    page_id: int | None = None,
    credit_kind: str | None = None,
    credit_text: str | None = None,
    credit_domain: str | None = None,
) -> Homebrew:
    """Insert an already-cleaned entry; the caller holds the campaign's lock. `user_id` is who
    created it; `added_by` (default the same) is who it is recorded as added by (#1633). The
    database refuses a source with the wrong fields (`homebrew_entries_source_check`)."""
    cur = await conn.execute(
        "SELECT count(*) AS n FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    if row is not None and int(row["n"]) >= HOMEBREW_MAX:
        raise HomebrewError(FULL)
    cur = await conn.execute(
        "WITH changed AS (INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name,"
        " name_key, text, summary, created_by, created_at, updated_at, from_book, book_edition,"
        " enabled, source_kind, added_by, page_id, credit_kind, credit_text, credit_domain)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (guild_id, campaign_id, name_key) DO NOTHING RETURNING *) "
        + _joined("changed"),
        (
            guild_id,
            campaign_id,
            kind,
            name,
            name_key(name),
            text,
            summary,
            user_id,
            created_at,
            updated_at,
            from_book,
            book_edition,
            enabled,
            source_kind,
            user_id if added_by is None else added_by,
            page_id,
            credit_kind,
            credit_text,
            credit_domain,
        ),
    )
    saved = await cur.fetchone()
    if saved is None:
        raise HomebrewError(SAME_NAME)
    return _entry(saved)


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
        raise HomebrewError(NO_CAMPAIGN)
    cur = await conn.execute(
        "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
        " AND user_id = %s",
        (guild_id, campaign_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HomebrewError(NOT_DM)


# ---- backups ------------------------------------------------------------------------

# ("players_may_know" is no longer kept (part 2): an old backup that has it is read and the key
# ignored; "enabled" came with #1629: a backup from before has none, meaning on.)
_KEYS = frozenset({"kind", "name", "text", "summary", "by", "created_at", "updated_at"})
# Where an entry came from (#1633): "source" (typed when absent), "added_by" (the "by" when absent),
# "credit" for an upload and "page" (the page's content, so the backup stays complete) for a link.
_OPTIONAL = frozenset({"enabled", "players_may_know", "source", "added_by", "credit", "page"})
SOURCES = ("typed", "upload", "link")
_CREDIT_KEYS = frozenset({"kind", "text", "domain"})
_PAGE_KEYS = frozenset({"url", "domain", "kind", "name", "text", "summary", "fetched_at", "status"})
_KEYS_BOOK = _KEYS | {"from_book"}  # (a backup from before #1524 has no such key: false)
_KEYS_EDITION = _KEYS_BOOK | {"book_edition"}  # (and before #1563, none: read as "other")


def _timestamp(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
        return value
    raise CampaignError(DAMAGED)


def _person(value: object) -> int:
    digits = isinstance(value, str) and value.isascii() and value.isdigit()
    if not digits or not 0 < int(str(value)) <= INT64_MAX:
        raise CampaignError(DAMAGED)
    return int(str(value))


@dataclass(frozen=True, slots=True)
class _Checked:
    """One backup row after `check`: every field validated."""

    kind: str
    name: str
    text: str
    summary: str | None
    created_by: int
    created_at: int
    updated_at: int
    from_book: bool = False
    book_edition: str | None = None
    enabled: bool = True
    source_kind: str = "typed"
    added_by: int = 0
    credit_kind: str | None = None
    credit_text: str | None = None
    credit_domain: str | None = None
    page: dict[str, Any] | None = None  # a link's page, as `_checked_page` returned it


def _checked_page(page: object, row: dict[str, Any]) -> dict[str, Any]:
    """A backup row's page: every field validated, the address in its normal form, and the entry
    agreeing with it (a link entry's name, kind, words and summary are the page's)."""
    if not isinstance(page, dict) or set(page) != _PAGE_KEYS:
        raise CampaignError(DAMAGED)
    url, domain = page["url"], page["domain"]
    if not isinstance(url, str) or not isinstance(domain, str):
        raise CampaignError(DAMAGED)
    found = normalise_url(url)
    if found is None or found != (url, domain):
        raise CampaignError(DAMAGED)
    if page["status"] not in ("ok", "stale", "restored"):
        raise CampaignError(DAMAGED)
    try:
        kind, name = clean_kind(page["kind"]), clean_name(page["name"])
        text, summary = clean_text(page["text"]), clean_summary(page["summary"])
    except (HomebrewError, TypeError, AttributeError):
        raise CampaignError(DAMAGED) from None
    if (kind, name, text, summary) != (page["kind"], page["name"], page["text"], page["summary"]):
        raise CampaignError(DAMAGED)
    # The entry keeps its own name and kind (the DM may have renamed it, and the page may have been
    # fetched again with a new title); only its words are the page's.
    if (text, summary) != (row["text"], row["summary"]):
        raise CampaignError(DAMAGED)
    return {**page, "fetched_at": _timestamp(page["fetched_at"])}


def _checked_credit(source: str, credit: object) -> tuple[str | None, str | None, str | None]:
    """(kind, text, domain) of an upload's credit; all None for the other sources."""
    if source != "upload":
        if credit is not None:
            raise CampaignError(DAMAGED)
        return None, None, None
    if not isinstance(credit, dict) or set(credit) != _CREDIT_KEYS:
        raise CampaignError(DAMAGED)
    kind, text, domain = credit["kind"], credit["text"], credit["domain"]
    if kind == "user" and text is None and domain is None:
        return kind, None, None
    if kind == "website" and text is None and isinstance(domain, str):
        if normalise_domain(domain) != domain:
            raise CampaignError(DAMAGED)
        return kind, None, domain
    if kind == "name" and domain is None and isinstance(text, str):
        try:
            if clean_name(text) != text:
                raise CampaignError(DAMAGED)
        except HomebrewError:
            raise CampaignError(DAMAGED) from None
        return kind, text, None
    raise CampaignError(DAMAGED)


def _dumped(r: dict[str, Any]) -> dict[str, Any]:
    """One entry as a backup row (a joined `_COLUMNS` row plus the page's own columns)."""
    source = r["source_kind"]
    row: dict[str, Any] = {
        "kind": r["kind"],
        "name": r["name"],
        "text": r["text"],
        "summary": r["summary"],
        "by": str(r["created_by"]),
        "created_at": int(r["created_at"]),
        "updated_at": int(r["updated_at"]),
        "source": source,
        "added_by": str(r["added_by"]),
        **({"from_book": True} if r["from_book"] else {}),
        **({"book_edition": r["book_edition"]} if r["book_edition"] else {}),
        **({"enabled": False} if not r["enabled"] else {}),  # (on is the default)
    }
    if source == "upload":
        row["credit"] = {
            "kind": r["credit_kind"],
            "text": r["credit_text"],
            "domain": r["credit_domain"],
        }
    if source == "link":  # the page travels with the entry, so the backup is complete
        row["page"] = {
            "url": r["page_url"],
            "domain": r["page_domain"],
            "kind": r["page_kind"],
            "name": r["page_name"],
            "text": r["page_text"],
            "summary": r["page_summary"],
            "fetched_at": int(r["page_fetched_at"]),
            "status": r["page_status"],
        }
    return row


class HomebrewSection:
    """Homebrew in campaign backups (an `ExportSection`). The ids mean nothing in another
    campaign, so they are not exported."""

    name = "homebrew"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {_COLUMNS}, p.domain AS page_domain, p.kind AS page_kind,"
            " p.name AS page_name, p.text AS page_text, p.summary AS page_summary,"
            f" p.fetched_at AS page_fetched_at, p.fetch_status AS page_status FROM {_FROM}"
            " WHERE h.guild_id = %s AND h.campaign_id = %s ORDER BY h.name_key",
            (guild_id, campaign_id),
        )
        return [_dumped(r) for r in await cur.fetchall()]

    def check(self, rows: list[Any]) -> list[_Checked]:
        """Every row, validated; nothing is written until all of them are right."""
        if len(rows) > HOMEBREW_MAX:
            raise CampaignError(DAMAGED)
        checked: list[_Checked] = []
        keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - _OPTIONAL not in (
                _KEYS,
                _KEYS_BOOK,
                _KEYS_EDITION,
            ):
                raise CampaignError(DAMAGED)
            if not isinstance(row.get("enabled", True), bool):
                raise CampaignError(DAMAGED)
            if not all(isinstance(row[k], str) for k in ("kind", "name", "text")):
                raise CampaignError(DAMAGED)
            if row["summary"] is not None and not isinstance(row["summary"], str):
                raise CampaignError(DAMAGED)
            if not isinstance(row.get("players_may_know", False), bool):
                raise CampaignError(DAMAGED)
            if not isinstance(row.get("from_book", False), bool):
                raise CampaignError(DAMAGED)
            edition = row.get("book_edition")
            if edition is not None and (edition not in BOOK_EDITIONS or not row.get("from_book")):
                raise CampaignError(DAMAGED)
            # A backup made before rules left homebrew (#1628) may hold the kind "rule": it loads as
            # "other", so nothing in the file is lost (the DM can make it a house rule).
            old_rule = row["kind"] == RULE_KIND
            try:
                kind = "other" if old_rule else clean_kind(row["kind"])
                name = clean_name(row["name"])
                text, summary = clean_text(row["text"]), clean_summary(row["summary"])
            except HomebrewError:
                raise CampaignError(DAMAGED) from None
            # A file made by DMbot is already in its cleaned form.
            if (kind, name, text, summary) != (
                "other" if old_rule else row["kind"],
                row["name"],
                row["text"],
                row["summary"],
            ):
                raise CampaignError(DAMAGED)
            if name_key(name) in keys:  # two entries can't share a name
                raise CampaignError(DAMAGED)
            keys.add(name_key(name))
            created, updated = _timestamp(row["created_at"]), _timestamp(row["updated_at"])
            if updated < created:
                raise CampaignError(DAMAGED)
            source = row.get("source", "typed")  # (a backup from before #1633 has none: typed)
            if source not in SOURCES:
                raise CampaignError(DAMAGED)
            added_by = _person(row.get("added_by", row["by"]))
            credit_kind, credit_text, credit_domain = _checked_credit(source, row.get("credit"))
            page = None
            if source == "link":
                if row.get("from_book") or old_rule or "page" not in row:
                    raise CampaignError(DAMAGED)
                page = _checked_page(row["page"], row)
                credit_domain = page["domain"]
            elif "page" in row:
                raise CampaignError(DAMAGED)
            checked.append(
                _Checked(
                    kind,
                    name,
                    text,
                    summary,
                    _person(row["by"]),
                    created,
                    updated,
                    row.get("from_book", False),
                    edition,
                    row.get("enabled", True),
                    source,
                    added_by,
                    credit_kind,
                    credit_text,
                    credit_domain,
                    page,
                )
            )
        return checked

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """`rows` as `check` returned them; a caller with only the file's rows gets them checked
        here, so nothing unchecked is ever written."""
        loaded = rows if self._is_checked(rows) else self.check(rows)
        if loaded:
            pages = {r.page["url"]: await self._page_id(conn, r.page) for r in loaded if r.page}
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, summary, created_by, created_at, updated_at,"
                    " from_book, book_edition, enabled, source_kind, added_by, page_id,"
                    " credit_kind, credit_text, credit_domain)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s)",
                    [
                        (
                            guild_id,
                            campaign_id,
                            r.kind,
                            r.name,
                            name_key(r.name),
                            None if r.page else r.text,  # (a link's words are its page's)
                            None if r.page else r.summary,
                            r.created_by,
                            r.created_at,
                            r.updated_at,
                            r.from_book,
                            r.book_edition,
                            r.enabled,
                            r.source_kind,
                            r.added_by,
                            pages[r.page["url"]] if r.page else None,
                            r.credit_kind,
                            r.credit_text,
                            r.credit_domain,
                        )
                        for r in loaded
                    ],
                )

    @staticmethod
    async def _page_id(conn: Conn, page: dict[str, Any]) -> int:
        """The id of the page with this address: the one already here (it may be newer than the
        backup's copy, so it is left as it is), else the backup's copy, recreated as `restored`
        with no fetch time. A backup file is untrusted and every campaign reads a page, so words
        from a file never become a page another campaign relies on: `SourceStore.page_for`
        skips a restored page, and the next fetch of that address replaces it."""
        await conn.execute(
            "INSERT INTO homebrew_pages (url, domain, kind, name, text, summary, fetched_at,"
            " fetch_status) VALUES (%s, %s, %s, %s, %s, %s, 0, 'restored')"
            " ON CONFLICT (url) DO NOTHING",
            (
                page["url"],
                page["domain"],
                page["kind"],
                page["name"],
                page["text"],
                page["summary"],
            ),
        )
        cur = await conn.execute("SELECT id FROM homebrew_pages WHERE url = %s", (page["url"],))
        row = await cur.fetchone()
        assert row is not None
        return int(row["id"])

    @staticmethod
    def _is_checked(rows: Any) -> bool:
        return isinstance(rows, list) and all(isinstance(r, _Checked) for r in rows) and bool(rows)

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM homebrew_entries WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
