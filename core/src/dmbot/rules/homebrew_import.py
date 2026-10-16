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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.rules import dndbeyond, page_fetch
from dmbot.rules.homebrew import Homebrew, HomebrewError, HomebrewStore
from dmbot.rules.homebrew_sources import SourceStore
from dmbot.rules.site_urls import normalise_domain

log = logging.getLogger(__name__)

Fetcher = Callable[[str], Awaitable[page_fetch.Reply]]


NOT_YET = "Add this site first: press **Add from a link** again and say you have the right."
TAKEN_BACK = "This site was taken off the list. Press **Add from a link** again to start over."


class ImportRefused(Exception):
    """Why the link was not added, in plain words for the DM."""


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
                url, domain, kept.kind, kept.name, kept.text, truncated=False, reused=True
            )
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
            raise ImportRefused(dndbeyond.NOT_PUBLIC)
        if reply.status != 200:
            log.warning("Homebrew page not read: campaign=%s status=%s", campaign_id, reply.status)
            raise ImportRefused(dndbeyond.NO_ANSWER)
        page = dndbeyond.parse_page(reply.text)
        if page is None:
            raise ImportRefused(dndbeyond.NO_WORDS)
        return Preview(
            url, domain, dndbeyond.kind_for(url), page.name, page.text, page.truncated, False
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
