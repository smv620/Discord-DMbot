"""Reading linked D&D Beyond sheets again while a session runs (#1128; docs/PLAN.md,
"Re-reading D&D Beyond"). The scheduling is `memory.sheet_watch` and the phrases are
`memory.sheet_changes`; this is the part that touches D&D Beyond, the sheet store and Discord.

What it never does: change a sheet on D&D Beyond, tell the DM or the table anything, or log a
sheet's content. A player may be asked a question privately when a read doesn't show what was
said; they can turn that off from 📜 My character sheet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import replace
from typing import Any

import discord

from dmbot.memory import sheet_changes, sheet_checks, sheets
from dmbot.memory.sheet_store import CharacterSheet

log = logging.getLogger(__name__)

TICK_S = 20  # how often the watch looks for sheets that are due
OPEN_LABEL = "Open on D&D Beyond"
_FIRST_PERSON = re.compile(r"^(?:and |so |ok |okay )?(?:i|i'm|i am|i've)\b", re.IGNORECASE)


def track(table: Any, found: list[CharacterSheet]) -> None:
    """The campaign's linked sheets, as they were just loaded: only ones read from D&D Beyond
    are watched (a typed or document sheet has no link to read again)."""
    linked = {s.entity_id: s for s in found if s.url is not None and s.character is not None}
    table.sheet_links = linked
    table.sheet_watch.track(list(linked), time.monotonic())


def note_line(table: Any, user_id: int, line: str) -> None:
    """A line of table talk (spoken or typed, from someone who agreed): if it plainly changes
    exactly one watched character, their sheet is to be read again in about a minute. Which
    character comes from their name just before the words ("Belleros takes 8 damage"), or
    from "I" for the speaker's own character. Unclear: nothing, never a guess."""
    if not getattr(table, "sheet_links", None):
        return
    heard = sheet_changes.find(line)
    if not heard:
        return
    now = time.monotonic()
    by_character: dict[str, list[sheet_changes.Heard]] = {}
    for h in heard:
        named = [e for e, s in table.sheet_links.items() if sheet_changes.about(line, h, s.name)]
        if not named and _FIRST_PERSON.match(" ".join(line.split())):
            named = [e for e, s in table.sheet_links.items() if s.played_by == user_id]
        if len(named) == 1:  # more than one, or none: unclear
            by_character.setdefault(named[0], []).append(h)
    for entity_id, changes in by_character.items():
        table.sheet_watch.note(entity_id, changes, now)


async def run(bot: Any, table: Any) -> None:
    """The loop for one session: every few seconds, read the sheets that are due. Ends with
    the session."""
    while bot.tables.get(table.guild_id) is table:
        await asyncio.sleep(TICK_S)
        if not table.listening or table.campaign_id is None or bot.sheets is None:
            continue
        try:
            await tick(bot, table)
        except Exception as exc:  # never the text: it can quote a row
            log.error("Sheet re-reads: a pass failed (%s)", type(exc).__name__)


async def tick(bot: Any, table: Any) -> None:
    now = time.monotonic()
    in_combat = bot.clocks is not None and await bot.clocks.in_combat(
        table.guild_id, table.campaign_id
    )
    due = table.sheet_watch.due(now, in_combat=in_combat)
    if not due:
        return
    async with sheets.new_session() as client:  # one connection for the whole pass
        for entity_id in due:
            if bot.tables.get(table.guild_id) is not table or not table.listening:
                break
            await reread(bot, table, entity_id, client)


async def reread(bot: Any, table: Any, entity_id: str, client: Any) -> None:
    sheet: CharacterSheet | None = table.sheet_links.get(entity_id)
    if sheet is None or sheet.character is None:
        return
    now = time.monotonic()
    heard = table.sheet_watch.take(entity_id, now)
    try:
        snapshot = await sheets.fetch(sheet.character, session=client)
    except sheets.SheetError as exc:
        table.sheet_watch.read_failed(entity_id, now, heard)
        reason = "not public" if exc.refused else "unreachable"
        log.info("Sheet re-read of entry %s failed (%s); waiting", entity_id, reason)
        return
    except Exception as exc:
        table.sheet_watch.read_failed(entity_id, now, heard)
        log.error("Sheet re-read of entry %s failed (%s)", entity_id, type(exc).__name__)
        return
    kept = await bot.sheets.save(
        table.guild_id, table.campaign_id, entity_id, snapshot, int(time.time()), url=sheet.url
    )
    if not kept:  # unlinked or relinked while it was being read
        return
    table.sheet_watch.read_ok(entity_id, now)
    after = sheets.clean(snapshot) or snapshot
    before = sheet.sheet
    table.sheet_links[entity_id] = replace(sheet, sheet=after, fetched_at=int(time.time()))
    # One line, never the sheet: which entry, why, and whether anything differed.
    log.info(
        "Sheet re-read of entry %s: %d change(s) heard, %s",
        entity_id,
        len(heard),
        "different" if before != after else "same",
    )
    if before != after:
        bot.sheets_changed(table.guild_id, table.campaign_id)  # new names for the hints
    await _questions(bot, table, sheet, heard, before, after)
    await _card(bot, table, sheet, before, after)


async def _questions(
    bot: Any,
    table: Any,
    sheet: CharacterSheet,
    heard: list[sheet_changes.Heard],
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    """Ask the player, privately and once per change, about what the sheet doesn't show."""
    questions = [q for h in heard if (q := sheet_changes.mismatch(h, before, after, sheet.name))]
    if not questions or sheet.played_by is None or sheet.url is None:
        return
    fresh = await bot.sheets.sheet(table.guild_id, table.campaign_id, sheet.entity_id)
    if fresh is None or not fresh.asks or fresh.played_by != sheet.played_by:
        return  # they turned the questions off, or no longer play it
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(label=OPEN_LABEL, style=discord.ButtonStyle.link, url=sheet.url)
    )
    text = "\n".join(dict.fromkeys(questions))[:1800]  # the same question once
    text += "\nTurn these questions off any time in 📜 My character sheet. Only you see this."
    await _dm(bot, sheet.played_by, text, view)


async def _card(
    bot: Any,
    table: Any,
    sheet: CharacterSheet,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    """The status card (#1124), but only when a part that was ✅ is now ⚠️ or ⛔."""
    if sheet.played_by is None or before is None:
        return
    from dmbot.ui import sheets as sheets_ui

    campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
    target, fallback = (
        (campaign.target_ruleset, campaign.fallback_ruleset) if campaign else ("2024", "none")
    )
    if not sheet_checks.got_worse(before, after, target=target, fallback=fallback):
        return
    card = await sheets_ui.status_card(bot, table.guild_id, table.campaign_id, after)
    await _dm(bot, sheet.played_by, card, None)


async def _dm(bot: Any, user_id: int, text: str, view: discord.ui.View | None) -> None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(
            text,
            view=view if view is not None else discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as exc:  # their DMs may be closed: nothing else to try
        log.info("Couldn't message user %s about their sheet: %s", user_id, type(exc).__name__)
