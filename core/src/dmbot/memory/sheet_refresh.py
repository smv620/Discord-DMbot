"""Refreshing a campaign's character sheets, and the names they add to speech-to-text
hints (#723). Pure apart from the store and the fetch, which are passed in."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from itertools import zip_longest
from typing import Any, Protocol

import aiohttp

from dmbot.memory import sheets
from dmbot.memory.models import name_key
from dmbot.memory.sheet_store import CharacterSheet

log = logging.getLogger(__name__)

# Sheet names in the hints, across every character. Speech-to-text takes 50 hints in
# all (memory.scene.MAX_HINTS): a wizard's spell list must not crowd out the scene.
SHEET_HINTS_MAX = 15

Fetch = Callable[[int], Awaitable[dict[str, Any]]]


class Sheets(Protocol):  # what refresh needs of SheetStore
    async def sheets(self, guild_id: int, campaign_id: str) -> list[CharacterSheet]: ...

    async def save(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        snapshot: dict[str, Any],
        now: int,
        *,
        url: str | None = None,
    ) -> bool: ...


async def refresh(
    store: Sheets,
    guild_id: int,
    campaign_id: str,
    now: int,
    *,
    found: Sequence[CharacterSheet] | None = None,
    fetch: Fetch | None = None,
    still_wanted: Callable[[], bool] = lambda: True,
) -> list[CharacterSheet]:
    """Read every linked sheet again, one at a time (one request per character, over one
    connection), and return the campaign's sheets. `found`: the sheets if already read.
    A sheet that can't be read keeps its old snapshot; the failure is logged with the
    character's entry id only. Stops early once `still_wanted()` is False (the session
    ended)."""
    sheets_now = list(found) if found is not None else await store.sheets(guild_id, campaign_id)
    linked = [s for s in sheets_now if s.character is not None]
    if not linked:
        return sheets_now
    async with contextlib.AsyncExitStack() as stack:
        if fetch is None:
            fetch = _shared_session(stack)
        for sheet in linked:
            if not still_wanted():
                break
            await _refresh_one(store, guild_id, campaign_id, sheet, now, fetch)
    return await store.sheets(guild_id, campaign_id)


async def _refresh_one(
    store: Sheets,
    guild_id: int,
    campaign_id: str,
    sheet: CharacterSheet,
    now: int,
    fetch: Fetch,
) -> None:
    assert sheet.character is not None
    try:
        snapshot = await fetch(sheet.character)
        await store.save(guild_id, campaign_id, sheet.entity_id, snapshot, now, url=sheet.url)
    except sheets.SheetError as exc:
        reason = "not public" if exc.refused else "unreachable"
        log.info("Couldn't refresh the sheet of entry %s (%s)", sheet.entity_id, reason)
    except Exception as exc:
        # Never the exception's text: a database error can quote the row (links, names).
        log.error(
            "Couldn't refresh the sheet of entry %s (%s)", sheet.entity_id, type(exc).__name__
        )


def _shared_session(stack: contextlib.AsyncExitStack) -> Fetch:
    """sheets.fetch over one session for the whole refresh, opened at the first fetch
    (not before waiting for a permit) and closed with `stack`, cancelled or not."""
    client: list[aiohttp.ClientSession] = []

    async def fetch(character: int) -> dict[str, Any]:
        if not client:
            client.append(await stack.enter_async_context(sheets.new_session()))
        return await sheets.fetch(character, session=client[0])

    return fetch


def hint_names(found: Sequence[CharacterSheet], limit: int = SHEET_HINTS_MAX) -> list[str]:
    """Names from the campaign's sheets for speech-to-text, taking turns between
    characters so each gets some, at most `limit`, each name once."""
    lists = [sheets.hint_names(s.sheet) for s in found if s.sheet is not None]
    out: dict[str, str] = {}
    for turn in zip_longest(*lists):
        for name in turn:
            if name is not None and len(out) < limit:
                out.setdefault(name_key(name), name)
    return list(out.values())
