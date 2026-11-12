"""The wizard's homebrew (#4211): the homebrew files and the published-page links of step 8, read
into the new campaign through the same paths as the Homebrew page: `homebrew_file.parse` and
`HomebrewStore.load_file` for a file, and the approved-sites path (`HomebrewImporter.read` and
`save`) for a link. No Discord here.

The DM's right-to-use confirmation for the draft is recorded earlier in the finish run, and covers
these (one confirmation per draft); it is also what approves a link's site for this campaign, with
who and when, exactly as the page's "I have the right" button does. Entries are the DM's own: the
wizard cannot ask "is this from a published book" mid-run, and the Homebrew page still can later.
A link is one page, fetched once, never a crawl; an address already kept is re-used. Counts only in
logs and notes, never an entry's words or an address.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import HomebrewError
from dmbot.rules.homebrew_import import ImportRefused

log = logging.getLogger(__name__)

HOMEBREW_UNREADABLE = "homebrew_unreadable"  # a document held no entry that could be read
HOMEBREW_LINK_FAILED = "homebrew_link_failed"  # a link was refused, not public, or gave no words


async def read_documents(
    store: Any, guild_id: int, campaign_id: str, user_id: int, docs: list[dict[str, Any]]
) -> tuple[int, int]:
    """Load each document's entries. Returns (entries added or changed, documents with no readable
    entry). One document's trouble never stops the next; the store refuses a full campaign with a
    `HomebrewError`, which counts the document as unreadable."""
    loaded = unreadable = 0
    for doc in docs:
        parsed = homebrew_file.parse(str(doc["text"]))
        if not parsed.entries:
            unreadable += 1
            continue
        try:
            done = await store.load_file(
                guild_id, campaign_id, user_id, parsed.entries, from_book=False
            )
        except HomebrewError:
            unreadable += 1
            continue
        loaded += done.added + done.updated
    log.info(
        "A wizard run read %d homebrew document(s): %d entries loaded, %d unreadable",
        len(docs), loaded, unreadable,
    )  # fmt: skip
    return loaded, unreadable


async def read_links(
    importer: Any,
    sources: Any,
    homebrew: Any,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    links: list[str],
) -> tuple[int, int]:
    """Add each published page. Returns (pages added, links that failed). A site is approved for
    the campaign the first time a link needs it (the draft's confirmation); a page already in the
    campaign is not added twice."""
    added = failed = 0
    have = {
        e.page_url
        for e in await homebrew.list(guild_id, campaign_id, usable_only=False)
        if e.source_kind == "link"
    }
    for text in links:
        try:
            url, domain = importer.check_address(text)
            if url in have:
                continue  # (an earlier try of this run added it: neither added nor failed)
            if not await sources.approved(guild_id, campaign_id, domain):
                await sources.approve(guild_id, campaign_id, user_id, domain)
            preview = await importer.read(guild_id, campaign_id, url)
            await importer.save(guild_id, campaign_id, user_id, preview)
            have.add(url)
            added += 1
        except (ImportRefused, HomebrewError) as exc:
            log.info("A wizard homebrew link was not added: %s", type(exc).__name__)
            failed += 1
        except Exception as exc:  # one link's trouble never stops the others
            log.warning("A wizard homebrew link failed (%s)", type(exc).__name__)
            failed += 1
    log.info(
        "A wizard run read %d homebrew link(s): %d added, %d failed", len(links), added, failed
    )
    return added, failed
