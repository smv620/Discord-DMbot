"""Where homebrew comes from, and which sites a campaign has approved (#1633; docs/PLAN.md,
"Homebrew (part 2)"). No Discord here, and nothing here fetches a page.

- **Pages.** A published page imported by link is public content, not a campaign's own, so one
  copy is kept per address (`homebrew_pages`, no campaign scope: the second exception to campaign
  isolation, next to shared rulesets) and each campaign's entry points to it. The table holds
  nothing about who imported a page or where.
- **Site approvals.** The first time a campaign imports from a site, its DM confirms the
  right-to-use warning once; that stays, for that site in that campaign only (`site_approvals`:
  campaign scope, forced row-level security, deleted with the campaign, in its backups). Who
  approved and when is kept. A campaign can use an entry from a site only while the site is
  approved there: that test is one place, `HomebrewStore`'s `_USABLE`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from typing import Any

from psycopg import errors

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.rules.homebrew import (
    INT64_MAX,
    HomebrewError,
    clean_kind,
    clean_name,
    clean_summary,
    clean_text,
    require_dm,
)
from dmbot.rules.site_urls import normalise_domain, normalise_url

log = logging.getLogger(__name__)

PAGE_KEEP_S = 30 * 24 * 3600  # an unused page is kept this long, in case it is wanted again

DAMAGED = "This backup file is damaged (bad site approval)."
BAD_ADDRESS = "That isn't a web address DMbot can read. It must start with https://."
APPROVALS_MAX = 50  # sites a campaign may approve: a backup cannot grow without bound


@dataclass(frozen=True, slots=True)
class Page:
    """One published page, as kept once for every campaign that imported it."""

    id: int
    url: str
    domain: str
    kind: str
    name: str
    text: str
    summary: str | None
    fetched_at: int
    fetch_status: str


@dataclass(frozen=True, slots=True)
class Approval:
    domain: str
    approved_by: int
    approved_at: int


_PAGE_COLUMNS = "id, url, domain, kind, name, text, summary, fetched_at, fetch_status"


def _page(row: dict[str, Any]) -> Page:
    return Page(
        int(row["id"]),
        row["url"],
        row["domain"],
        row["kind"],
        row["name"],
        row["text"],
        row["summary"],
        int(row["fetched_at"]),
        row["fetch_status"],
    )


class SourceStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    # ---- pages: one copy per address, whoever imported it ----

    async def page_for(self, url: str, *, include_restored: bool = False) -> Page | None:
        """The page kept for this address (in its normal form), if any. A page recreated from a
        backup file is `restored` (never fetched, so not trusted): it is not returned unless
        asked for, so the fetch step fetches again and replaces it."""
        found = normalise_url(url)
        if found is None:
            return None
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                f"SELECT {_PAGE_COLUMNS} FROM homebrew_pages WHERE url = %s"
                + ("" if include_restored else " AND fetch_status <> 'restored'"),
                (found[0],),
            )
            row = await cur.fetchone()
        return None if row is None else _page(row)

    async def pages_by_url(self, urls: Collection[str]) -> dict[str, Page]:
        """The kept pages for these addresses (each in its normal form), by address. A page
        recreated from a backup (`restored`, never fetched) is not trusted and is left out, so a
        followed pack never shows words only a backup file vouched for."""
        wanted = sorted(set(urls))
        if not wanted:
            return {}
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                f"SELECT {_PAGE_COLUMNS} FROM homebrew_pages WHERE url = ANY(%s)"
                " AND fetch_status <> 'restored'",
                (wanted,),
            )
            return {r["url"]: _page(r) for r in await cur.fetchall()}

    async def save_page(
        self,
        url: str,
        *,
        kind: str,
        name: str,
        text: str,
        summary: str | None = None,
        status: str = "ok",
    ) -> Page:
        """Keep a page's content under its address: new, or the one copy replaced when the page
        is fetched again (every campaign that imported it sees the new words). The content is
        cleaned like any entry's. Public content only: nothing about who asked for it.
        **Only the bot's own fetch step may call this**, with what it read from the address
        itself, never with words a person supplied: it writes what every campaign will read."""
        found = normalise_url(url)
        if found is None:
            raise HomebrewError(BAD_ADDRESS)
        address, domain = found
        clean = (clean_kind(kind), clean_name(name), clean_text(text), clean_summary(summary))
        if status not in ("ok", "stale"):
            raise HomebrewError("DMbot doesn't know that page status.")
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "INSERT INTO homebrew_pages (url, domain, kind, name, text, summary, fetched_at,"
                " fetch_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (url) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name,"
                " text = EXCLUDED.text, summary = EXCLUDED.summary,"
                " fetched_at = EXCLUDED.fetched_at, fetch_status = EXCLUDED.fetch_status"
                f" RETURNING {_PAGE_COLUMNS}",
                (address, domain, *clean, int(self._clock()), status),
            )
            row = await cur.fetchone()
        assert row is not None
        return _page(row)

    async def sweep_pages(self, guild_ids: Iterable[int]) -> int:
        """The daily clean-up (#1646): delete pages older than `PAGE_KEEP_S` that no entry points
        to and no pack (followed, or published by a campaign) names. Pages are shared, but the
        entries and packs that use them are each campaign's own, so every server is read in its
        own scope and only the addresses are collected (no names, no words). Returns how many were
        deleted; the log line carries the count and never an address.

        A page someone starts using after the look is safe: an entry's `page_id` is a foreign key,
        so that page's delete is refused and the rest go on; a pack naming
        it a moment later finds no page and its entry is skipped, as for any page not kept."""
        used: set[str] = set()
        for guild_id in guild_ids:
            async with self._db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "SELECT DISTINCT p.url FROM homebrew_entries h"
                    " JOIN homebrew_pages p ON p.id = h.page_id"
                )
                used.update(r["url"] for r in await cur.fetchall())
                # (`previous_content` is a follower's copy before the last update: Undo needs it)
                for table, column in (
                    ("rule_set_follows", "content"),
                    ("rule_set_follows", "previous_content"),
                    ("rule_set_versions", "content"),
                ):
                    cur = await conn.execute(
                        f"SELECT DISTINCT e ->> 'page' AS url FROM {table},"
                        f" jsonb_array_elements(CASE WHEN jsonb_typeof({column} -> 'entries') ="
                        f" 'array' THEN {column} -> 'entries' ELSE '[]'::jsonb END) e"
                        " WHERE jsonb_typeof(e) = 'object' AND e ->> 'page' IS NOT NULL"
                    )
                    used.update(r["url"] for r in await cur.fetchall())
        cutoff = int(self._clock()) - PAGE_KEEP_S
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT id FROM homebrew_pages WHERE fetched_at < %s AND url <> ALL(%s)",
                (cutoff, sorted(used)),
            )
            candidates = [int(r["id"]) for r in await cur.fetchall()]
        deleted = 0
        for page_id in candidates:  # one at a time: a page that just gained an entry stays
            try:
                async with self._db.unscoped() as conn:
                    await conn.execute("DELETE FROM homebrew_pages WHERE id = %s", (page_id,))
                deleted += 1
            except errors.ForeignKeyViolation:
                continue
        log.info("Homebrew pages no one uses were deleted: count=%s", deleted)
        return deleted

    async def mark_stale(self, url: str) -> None:
        """The page was fetched again and is gone or private (#1645): keep its words, say they may
        be old. Only a page fetched before (`ok`) moves; a restored one stays untrusted."""
        found = normalise_url(url)
        if found is None:
            return
        async with self._db.unscoped() as conn:
            await conn.execute(
                "UPDATE homebrew_pages SET fetch_status = 'stale'"
                " WHERE url = %s AND fetch_status = 'ok'",
                (found[0],),
            )

    # ---- approvals: once per site, per campaign ----

    async def approved(self, guild_id: int, campaign_id: str, domain: str) -> bool:
        """Has this campaign approved this site? (`domain` as `normalise_domain` makes it.)"""
        if normalise_domain(domain) != domain:
            return False
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM site_approvals WHERE guild_id = %s AND campaign_id = %s"
                " AND domain = %s",
                (guild_id, campaign_id, domain),
            )
            return await cur.fetchone() is not None

    async def approvals(self, guild_id: int, campaign_id: str) -> list[Approval]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT domain, approved_by, approved_at FROM site_approvals"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY domain",
                (guild_id, campaign_id),
            )
            return [
                Approval(r["domain"], int(r["approved_by"]), int(r["approved_at"]))
                for r in await cur.fetchall()
            ]

    async def approve(self, guild_id: int, campaign_id: str, user_id: int, domain: str) -> Approval:
        """Record that this campaign's DM confirmed the right to use content from this site.
        Only the campaign's DMs. Approving a site again changes nothing (who approved first and
        when stays)."""
        site = normalise_domain(domain)
        if site is None or site != domain:
            raise HomebrewError(BAD_ADDRESS)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT domain, approved_by, approved_at FROM site_approvals"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            have = {r["domain"]: r for r in await cur.fetchall()}
            found = have.get(site)
            if found is None:
                if len(have) >= APPROVALS_MAX:
                    raise HomebrewError("This campaign has approved as many sites as it can.")
                now = int(self._clock())
                await conn.execute(
                    "INSERT INTO site_approvals (guild_id, campaign_id, domain, approved_by,"
                    " approved_at) VALUES (%s, %s, %s, %s, %s)",
                    (guild_id, campaign_id, site, user_id, now),
                )
                return Approval(site, user_id, now)
        return Approval(site, int(found["approved_by"]), int(found["approved_at"]))

    async def revoke(self, guild_id: int, campaign_id: str, user_id: int, domain: str) -> bool:
        """Take an approval back: entries from that site stop being used until it is approved
        again (they are kept). Only the campaign's DMs. False when there was none."""
        if normalise_domain(domain) != domain:
            return False
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "DELETE FROM site_approvals WHERE guild_id = %s AND campaign_id = %s"
                " AND domain = %s RETURNING domain",
                (guild_id, campaign_id, domain),
            )
            return await cur.fetchone() is not None


@dataclass(frozen=True, slots=True)
class _CheckedApproval:
    domain: str
    approved_by: int
    approved_at: int


class SiteApprovalsSection:
    """A campaign's site approvals in its backups (an `ExportSection`)."""

    name = "site_approvals"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT domain, approved_by, approved_at FROM site_approvals"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY domain",
            (guild_id, campaign_id),
        )
        return [
            {"domain": r["domain"], "by": str(r["approved_by"]), "at": int(r["approved_at"])}
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_CheckedApproval]:
        if len(rows) > APPROVALS_MAX:
            raise CampaignError(DAMAGED)
        out: list[_CheckedApproval] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"domain", "by", "at"}:
                raise CampaignError(DAMAGED)
            domain, by, at = row["domain"], row["by"], row["at"]
            digits = isinstance(by, str) and by.isascii() and by.isdigit()
            if (
                not isinstance(domain, str)
                or normalise_domain(domain) != domain
                or domain in seen
                or not digits
                or not 0 < int(str(by)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            seen.add(domain)
            out.append(_CheckedApproval(domain, int(str(by)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = (
            rows
            if isinstance(rows, list)
            and rows
            and all(isinstance(r, _CheckedApproval) for r in rows)
            else self.check(rows)
        )
        if checked:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO site_approvals (guild_id, campaign_id, domain, approved_by,"
                    " approved_at) VALUES (%s, %s, %s, %s, %s)",
                    [
                        (guild_id, campaign_id, r.domain, r.approved_by, r.approved_at)
                        for r in checked
                    ],
                )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM site_approvals WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
