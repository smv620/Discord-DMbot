"""Adding homebrew from a published page's link (#1634; docs/PLAN.md, "Homebrew (part 2)"): the
steps between the DM pasting an address and an entry in the campaign. No Discord here.

1. `check_address`: an https address on an allowed site (`dndbeyond`), else say what is allowed.
2. The campaign must have approved the site (`SourceStore.approved`; the screen asks once).
3. `read`: the page, once. An address already kept in `homebrew_pages` is re-used and not fetched
   again; otherwise ONE page is fetched (`page_fetch`) and read (`dndbeyond`). Nothing is saved.
4. `save`: the page is kept (one copy per address) and the campaign gets an entry pointing to it.

What a refusal says is always in plain words for the DM; a block or any other failure is logged
without the page or its address.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.rules import dndbeyond, page_fetch
from dmbot.rules.homebrew import (
    Homebrew,
    HomebrewError,
    HomebrewStore,
    clean_kind,
    clean_name,
    clean_text,
)
from dmbot.rules.homebrew_sources import Page, SourceStore
from dmbot.rules.site_urls import normalise_domain, normalise_url

log = logging.getLogger(__name__)

Fetcher = Callable[[str], Awaitable[page_fetch.Reply]]


NOT_YET = "Add this site first: press **Add from a link** again and say you have the right."
TAKEN_BACK = "This site was taken off the list. Press **Add from a link** again to start over."


NOT_A_LINK = (
    "This entry was typed in or loaded, not added from a link, so there is nothing to update."
)
PAGE_LOST = "DMbot lost the saved copy of this page. Remove the entry and add the link again."
GONE = "That page isn't public any more. DMbot keeps the words it has."
SAME = "Nothing changed on the page."


class ImportRefused(Exception):
    """Why the link was not added, in plain words for the DM."""


class PageGone(ImportRefused):
    """The page answered "not found" or "not public" (an update marks the page stale)."""


@dataclass(frozen=True, slots=True)
class Refresh:
    """A page fetched again: the new words and a plain note on what changed."""

    preview: Preview
    note: str
    # The kept copy was `stale` or `restored`: saving makes it fresh and trusted even when the
    # words are the same, so the DM is offered Save.
    renews: bool = False

    @property
    def changed(self) -> bool:
        return self.note != SAME or self.renews


@dataclass(frozen=True, slots=True)
class Preview:
    """A page read but not yet saved."""

    url: str  # in its normal form
    domain: str
    kind: str
    name: str
    text: str
    truncated: bool  # the page was longer than an entry may be
    reused: bool  # it was already kept here: nothing was fetched


def _redirect_ok(host: str) -> bool:
    domain = normalise_domain(host)
    return domain is not None and dndbeyond.allowed(domain)


async def _fetch(url: str) -> page_fetch.Reply:
    return await page_fetch.fetch_page(url, redirect_ok=_redirect_ok)


class HomebrewImporter:
    def __init__(
        self,
        homebrew: HomebrewStore,
        sources: SourceStore,
        *,
        fetch: Fetcher = _fetch,
        gate: page_fetch.FetchGate | None = None,
    ) -> None:
        self._homebrew = homebrew
        self._sources = sources
        self._fetch = fetch
        self._gate = gate or page_fetch.FetchGate()

    @staticmethod
    def check_address(text: str) -> tuple[str, str]:
        """(address, site) for a link on an allowed site; else `ImportRefused`."""
        found = dndbeyond.check_address(text)
        if found is None:
            raise ImportRefused(dndbeyond.NOT_SUPPORTED)
        return found

    async def read(self, guild_id: int, campaign_id: str, text: str) -> Preview:
        """The page behind the address, not yet saved. The site must already be approved in this
        campaign (the caller asked). Re-uses a page kept here; else fetches it once."""
        url, domain = self.check_address(text)
        if not await self._sources.approved(guild_id, campaign_id, domain):
            raise ImportRefused(NOT_YET)
        kept = await self._sources.page_for(url)
        if kept is not None:
            return Preview(
                url,
                domain,
                kept.kind,
                kept.name,
                kept.text,
                truncated=kept.text.endswith("…"),
                reused=True,
            )
        return await self._fetch_preview(guild_id, campaign_id, url, domain)

    async def _fetch_preview(
        self, guild_id: int, campaign_id: str, url: str, domain: str
    ) -> Preview:
        """ONE fetch of the page, read and shown, nothing saved. A page that is gone raises
        `PageGone` (a kind of refusal) so the caller can mark it."""
        try:
            self._gate.enter(guild_id, campaign_id)
        except page_fetch.PageError as exc:
            raise ImportRefused(str(exc)) from None
        try:
            reply = await self._fetch(url)
        except page_fetch.PageError as exc:
            log.warning("Homebrew page not read: campaign=%s why=%s", campaign_id, exc)
            raise ImportRefused(dndbeyond.NO_ANSWER) from None
        finally:
            self._gate.leave(guild_id, campaign_id)
        if dndbeyond.is_not_found(reply.status, reply.text):
            raise PageGone(dndbeyond.NOT_PUBLIC)
        if reply.status != 200:
            log.warning("Homebrew page not read: campaign=%s status=%s", campaign_id, reply.status)
            raise ImportRefused(dndbeyond.NO_ANSWER)
        page = await asyncio.to_thread(dndbeyond.parse_page, reply.text)  # (CPU: off the loop)
        if page is None:
            raise ImportRefused(dndbeyond.NO_WORDS)
        return Preview(
            url, domain, dndbeyond.kind_for(url), page.name, page.text, page.truncated, False
        )

    async def refresh(self, guild_id: int, campaign_id: str, entry: Homebrew) -> Refresh:
        """🔄 Update from the link (#1645): fetch the entry's ONE page again, at a DM's press, with
        the same guards and gate as adding, and the site's approval checked again. Never re-uses
        the kept copy (that is the point). Nothing is saved; `save_refresh` does that. A page that
        is gone is marked `stale` and its words are kept."""
        found = normalise_url(entry.page_url) if entry.page_url else None
        if entry.source_kind != "link" or found is None:
            raise ImportRefused(NOT_A_LINK)
        url, domain = found
        if dndbeyond.check_address(url) is None:  # (a crafted backup could carry any address)
            raise ImportRefused(dndbeyond.NOT_SUPPORTED)
        if not await self._sources.approved(guild_id, campaign_id, domain):
            raise ImportRefused(TAKEN_BACK)
        kept = await self._sources.page_for(url, include_restored=True)
        if kept is None:
            raise ImportRefused(PAGE_LOST)
        try:
            fresh = await self._fetch_preview(guild_id, campaign_id, url, domain)
        except PageGone as exc:
            await self._sources.mark_stale(url)
            raise ImportRefused(GONE) from exc
        note = _what_changed(kept, fresh)
        renews = kept.fetch_status != "ok"
        if renews and note == SAME:
            note = "The page answers again. Its words are the same."
        return Refresh(fresh, note, renews)

    async def save_refresh(self, guild_id: int, campaign_id: str, preview: Preview) -> None:
        """Replace the page's words. There is one copy per address, so every campaign pointing at
        it gets them (the screen says so). The page becomes trusted and fresh: `ok`."""
        if not await self._sources.approved(guild_id, campaign_id, preview.domain):
            raise ImportRefused(TAKEN_BACK)
        mine = await self._homebrew.list(guild_id, campaign_id, usable_only=False)
        if not any(e.source_kind == "link" and e.page_url == preview.url for e in mine):
            raise ImportRefused(NOT_A_LINK)  # (removed since the page was fetched)
        await self._sources.save_page(
            preview.url, kind=preview.kind, name=preview.name, text=preview.text
        )

    async def save(
        self, guild_id: int, campaign_id: str, user_id: int, preview: Preview
    ) -> Homebrew:
        """Keep the page (one copy per address) and add the entry that points to it. Only the
        campaign's DMs; a name the campaign has, or a full campaign, is refused like any add. The
        site must be approved here still (checked again: it could have been taken back)."""
        if not await self._sources.approved(guild_id, campaign_id, preview.domain):
            raise ImportRefused(TAKEN_BACK)
        page = await self._sources.page_for(preview.url)
        if page is None:
            page = await self._sources.save_page(
                preview.url, kind=preview.kind, name=preview.name, text=preview.text
            )
        try:
            return await self._homebrew.add_link(
                guild_id,
                campaign_id,
                user_id,
                page_id=page.id,
                kind=page.kind,
                name=page.name,
                domain=page.domain,
            )
        except HomebrewError as exc:
            raise ImportRefused(str(exc)) from None


def _what_changed(kept: Page, fresh: Preview) -> str:
    """Said in plain words, comparing as the store would keep them (so cleaning alone is no
    change)."""
    parts = []
    if clean_name(fresh.name) != kept.name:
        parts.append("the name")
    if clean_kind(fresh.kind) != kept.kind:
        parts.append("the type")
    if clean_text(fresh.text) != kept.text:
        parts.append("the words")
    if not parts:
        return SAME
    return "The page changed: " + " and ".join(parts) + "."
