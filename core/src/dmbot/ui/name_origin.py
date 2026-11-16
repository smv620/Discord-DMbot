"""Where an entry came from, for every "📜 Where was it said?" button (#4554).

One place gathers it so the names card, the look-alikes card and the rest never differ: the
transcript places (newest first), the sentence from the DM's upload while DMbot still holds it,
and, when the sentence is gone, the upload's name and date; for a story thread the DM kept, the
lines its suggestion pointed at or its upload; for a name the DM typed, a line saying so. "No
place is kept" is left for an entry with no source at all. Words are read when the button is
pressed and never copied; the DM only, checked by the caller; counts only in the log.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Collection, Sequence
from typing import Any

import discord

from dmbot.memory.models import DM
from dmbot.memory.thread_suggestions import KeptFrom, ThreadSuggestionStore
from dmbot.ui import name_places

log = logging.getLogger(__name__)

YOURS = "You added this name yourself."
UPLOADS_SHOWN = 3
SESSION_LINES_SHOWN = 3


def _day(at: int) -> str:
    when = dt.datetime.fromtimestamp(at, dt.UTC)
    return f"{when.day} {when.strftime('%b')}"


def upload_line(label: str, at: int) -> str:
    """Pure: the upload an entry was read from, when its sentence is no longer held."""
    shown = name_places._plain(label)
    return (
        f"📄 From your upload *{shown}*, {_day(at)}. DMbot doesn't keep the sentence after reading."
    )


def upload_lines(uploads: Sequence[tuple[str, str, int]]) -> list[str]:
    """Pure: newest upload first, at most `UPLOADS_SHOWN`."""
    newest = sorted(uploads, key=lambda u: u[2], reverse=True)[:UPLOADS_SHOWN]
    return [upload_line(label, at) for _key, label, at in newest]


async def _session_lines(
    transcripts: Any, guild_id: int, kept: KeptFrom, agreed: Collection[int]
) -> list[str]:
    """What a kept session suggestion pointed at, read now: its session and day, then the lines
    still there from people who still agree (never by place: a deleted line moves them)."""
    from dmbot.ui.thread_questions import heard_lines  # (lazily: that card imports a lot)

    if kept.session_id is None or not kept.line_starts:
        return []
    session = await transcripts.session(guild_id, kept.session_id)
    if session is None:
        return []
    if session.deleted_at is not None:
        return [name_places.REMOVED]
    lines = heard_lines(await transcripts.lines(guild_id, kept.session_id), kept, agreed)  # type: ignore[arg-type]
    if not lines:
        return [name_places.REMOVED]
    shown = "\n".join(
        "> “" + discord.utils.escape_markdown(text[:300], ignore_links=False) + "”"
        for text in lines[:SESSION_LINES_SHOWN]
    )
    return [f"_From session {session.number}, {_day(session.started_at)}_\n{shown}"]


async def where_entry(
    interaction: discord.Interaction,
    campaign: Any,
    memory: Any,
    entity_id: str,
    spellings: Sequence[str],
    *,
    refs: Sequence[tuple[str, int | None]] | None = None,
    lore: str = "",
    lore_source: str = "",
    lore_card: bool = False,
) -> str:
    """The reply for one entry. `lore_card`: the caller is the names-to-check card, where an
    entry read from the DM's upload with nothing left to show says the sentence is gone."""
    guild_id = campaign.guild_id
    client: Any = interaction.client
    transcripts: Any = getattr(client, "transcripts", None)
    try:
        if refs is None:
            refs = await memory.mentions(guild_id, campaign.id, entity_id)
        uploads = await memory.name_uploads(guild_id, campaign.id, entity_id)
        entity = await memory.entity(guild_id, campaign.id, entity_id)
        kept = await ThreadSuggestionStore(memory._db).kept_from(guild_id, campaign.id, entity_id)
        agreed = await client.consent.consenting(guild_id)
        extra: list[str] = []
        if not lore:
            extra.extend(upload_lines(uploads))
        if kept is not None:
            if transcripts is not None:  # (no transcripts: the upload sources still show)
                extra.extend(await _session_lines(transcripts, guild_id, kept, agreed))
            # (the day is the suggestion's own, made when the upload was read)
            if kept.upload and not uploads and not lore:
                extra.append(upload_line(kept.upload, kept.created_at))
        text = await name_places.where_said(
            interaction.guild,
            transcripts,
            guild_id,
            refs
            if transcripts is not None
            else [],  # (no transcripts: the other sources still show)
            spellings,
            agreed,
            lore=lore,
            lore_source=lore_source,
            extra=extra,
        )
        if text == name_places.NOTHING_KEPT or not text:
            if entity is not None and entity.source == DM:
                text = YOURS
            elif lore_card:
                text = name_places.LORE_GONE
            else:
                text = name_places.NOTHING_KEPT
    except Exception:
        log.exception("Couldn't show where a name was said")
        return name_places.TRY_AGAIN
    log.info(
        "Showed where an entry was said: %d places kept, %d uploads, thread %s",
        len(refs),
        len(uploads),
        "yes" if kept is not None else "no",
    )
    return text
