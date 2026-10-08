"""Refreshing a campaign's character sheets, and the names they add to speech-to-text
hints (#723). Pure apart from the store and the fetch, which are passed in."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from itertools import zip_longest
from typing import Any

from dmbot.memory import sheets
from dmbot.memory.models import name_key
from dmbot.memory.sheet_store import CharacterSheet, SheetStore

log = logging.getLogger(__name__)

# Sheet names in the hints, across every character. Speech-to-text takes 50 hints in
# all (memory.scene.MAX_HINTS): a wizard's spell list must not crowd out the scene.
SHEET_HINTS_MAX = 15

Fetch = Callable[[int], Awaitable[dict[str, Any]]]


async def refresh(
    store: SheetStore,
    guild_id: int,
    campaign_id: str,
    now: int,
    *,
    fetch: Fetch = sheets.fetch,
) -> list[CharacterSheet]:
    """Read every linked sheet again, one at a time (one request per character), and
    return the campaign's sheets. A sheet that can't be read keeps its old snapshot;
    the failure is logged with the character's entry id only."""
    for sheet in await store.sheets(guild_id, campaign_id):
        character = sheet.character
        if character is None:
            continue
        try:
            snapshot = await fetch(character)
            await store.save(guild_id, campaign_id, sheet.entity_id, snapshot, now, url=sheet.url)
        except sheets.SheetError as exc:
            reason = "not public" if not exc.public else "unreachable"
            log.info("Couldn't refresh the sheet of entry %s (%s)", sheet.entity_id, reason)
        except Exception:
            log.exception("Couldn't refresh the sheet of entry %s", sheet.entity_id)
    return await store.sheets(guild_id, campaign_id)


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
