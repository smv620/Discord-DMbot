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

from dmbot.memory import sheet_changes, sheet_checks, sheet_questions, sheet_watch, sheets
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
        table.sheet_watch.note(entity_id, changes, now, user_id)


async def run(bot: Any, table: Any) -> None:
    """The loop for one session: every few seconds, read the sheets that are due and send
    the questions that are ready. Ends with the session."""
    while bot.tables.get(table.guild_id) is table:
        await asyncio.sleep(TICK_S)
        if not table.listening or table.campaign_id is None or bot.sheets is None:
            continue
        try:
            await tick(bot, table)
        except Exception as exc:  # never the text: it can quote a row
            log.error("Sheet re-reads: a pass failed (%s)", type(exc).__name__)


async def _in_combat(bot: Any, table: Any) -> bool:
    return bot.clocks is not None and bool(
        await bot.clocks.in_combat(table.guild_id, table.campaign_id)
    )


async def tick(bot: Any, table: Any) -> None:
    now = time.monotonic()
    if table.sheet_questions.any_waiting():
        await _flush(bot, table, now)
    # Most ticks have nothing due: look at the clock (a database read) only when something is.
    if not table.sheet_watch.due(now, in_combat=False):
        return
    if sheets.wait_time() > 0.0:
        return  # D&D Beyond is paused or the bot-wide limit is full: wait, penalise no one
    in_combat = await _in_combat(bot, table)
    due = table.sheet_watch.due(now, in_combat=in_combat)
    if not due:
        return
    async with sheets.new_session() as client:  # one connection for the whole pass
        for entity_id in due:
            if bot.tables.get(table.guild_id) is not table or not table.listening:
                break
            if sheets.wait_time() > 0.0:
                break  # the limit filled up during the pass
            await reread(bot, table, entity_id, client)


async def reread(bot: Any, table: Any, entity_id: str, client: Any) -> None:
    sheet: CharacterSheet | None = table.sheet_links.get(entity_id)
    if sheet is None or sheet.character is None:
        return
    now = time.monotonic()
    periodic = table.sheet_watch.periodic_due(entity_id, now)
    noted = table.sheet_watch.take(entity_id, now)
    # Consent is checked again here: a speaker may have stopped being recorded since they
    # were heard. If every change waiting was theirs and the schedule isn't asking, no read.
    agreed = [n for n in noted if bot.consent.has_consent(table.guild_id, n.speaker)]
    heard = [n.heard for n in agreed]
    if noted and not heard and not periodic:
        return
    try:
        snapshot = await sheets.fetch(sheet.character, session=client)
    except sheets.SheetError as exc:
        if exc.limited:  # D&D Beyond (or our own limit) said wait: not this sheet's fault
            table.sheet_watch.defer(entity_id, noted)
            log.info(
                "Sheet re-read of entry %s held back (%s); every sheet waits",
                entity_id,
                exc.status or "busy",
            )
            return
        table.sheet_watch.read_failed(entity_id, now, noted)
        reason = "not public" if exc.refused else "unreachable"
        log.info("Sheet re-read of entry %s failed (%s); waiting", entity_id, reason)
        return
    except Exception as exc:
        table.sheet_watch.read_failed(entity_id, now, noted)
        log.error("Sheet re-read of entry %s failed (%s)", entity_id, type(exc).__name__)
        return
    try:
        kept = await bot.sheets.save(
            table.guild_id, table.campaign_id, entity_id, snapshot, int(time.time()), url=sheet.url
        )
    except Exception as exc:  # never the text: it can quote a row
        table.sheet_watch.read_failed(entity_id, now, noted)
        log.error("Sheet re-read of entry %s couldn't be kept (%s)", entity_id, type(exc).__name__)
        return
    if not kept:  # unlinked or relinked while it was being read: stop watching it
        table.sheet_links.pop(entity_id, None)
        table.sheet_watch.track(list(table.sheet_links), now)
        return
    table.sheet_watch.read_ok(entity_id, now)
    after = sheets.clean(snapshot) or snapshot
    before = sheet.sheet
    current = table.sheet_links.get(entity_id)  # may have been reloaded while we waited
    if current is not None:
        table.sheet_links[entity_id] = replace(current, sheet=after, fetched_at=int(time.time()))
    # One line, never the sheet: which entry, why, and whether anything differed.
    log.info(
        "Sheet re-read of entry %s: %d change(s) heard, %s",
        entity_id,
        len(heard),
        "different" if before != after else "same",
    )
    if before != after:
        bot.sheets_changed(table.guild_id, table.campaign_id)  # new names for the hints
    # Who plays it now, and whether they want questions, as stored now (not as loaded at the
    # start of the session): a character given to someone else gets nothing about the old one.
    fresh = await bot.sheets.sheet(table.guild_id, table.campaign_id, entity_id)
    if fresh is None or fresh.played_by is None or fresh.url is None:
        return
    if bot.tables.get(table.guild_id) is not table or not table.listening:
        return  # the session ended meanwhile
    _hold_questions(bot, table, fresh, agreed, before, after)
    await _hold_card(bot, table, fresh, before, after)
    await _flush(bot, table, time.monotonic())


def _hold_questions(
    bot: Any,
    table: Any,
    sheet: CharacterSheet,
    agreed: list[sheet_watch.Noted],
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    """Put what the sheet doesn't show on the player's list of questions (sent later, rarely).
    Damage and healing of the same size cancel out; nothing is asked about those. Each
    question carries the speaker of its own line, and consent is checked again here: the
    read took seconds, and a speaker may have stopped being recorded meanwhile."""
    if sheet.played_by is None or sheet.url is None or not sheet.asks:
        return
    left = list(agreed)  # each line's speaker is used once
    for h in sheet_changes.net([n.heard for n in agreed]):
        speaker = _take_speaker(left, h)
        if speaker is None or not bot.consent.has_consent(table.guild_id, speaker):
            continue
        text = sheet_changes.mismatch(h, before, after, sheet.name)
        if text:
            table.sheet_questions.add(
                sheet.played_by,
                sheet_questions.Waiting(
                    sheet.entity_id,
                    (h.kind, h.amount, h.condition),
                    text,
                    sheet_changes.shown(after),
                    speaker,
                    sheet.url,
                ),
            )


def _take_speaker(left: list[sheet_watch.Noted], h: sheet_changes.Heard) -> int | None:
    """Whose line this change came from: the first one not yet used that says the same."""
    for n in left:
        if n.heard == h:
            left.remove(n)
            return n.speaker
    return None


async def _hold_card(
    bot: Any,
    table: Any,
    sheet: CharacterSheet,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    """The status card (#1124) waits for a quiet moment, and only when a part that was ✅ is
    now ⚠️ or ⛔."""
    if sheet.played_by is None or before is None or before == after:
        return
    campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
    target, fallback = (
        (campaign.target_ruleset, campaign.fallback_ruleset)
        if campaign
        else sheet_checks.NO_CAMPAIGN_RULESETS
    )
    if sheet_checks.got_worse(before, after, target=target, fallback=fallback):
        table.sheet_questions.hold_card(sheet.played_by, after)


async def _flush(bot: Any, table: Any, now: float) -> None:
    """Send what is ready: each player's waiting questions as one message (not in combat, at
    most one every 15 minutes), and a waiting status card (not in combat)."""
    questions: sheet_questions.Questions = table.sheet_questions
    # The cheap checks first: this runs every few seconds, and most of the time nothing is due.
    players = {p for p in {*questions.waiting, *questions.cards} if questions.due(p, now)}
    if not players:
        return
    if await _in_combat(bot, table):
        return  # nothing is sent during a fight; it all waits
    campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
    for player in players:
        if bot.tables.get(table.guild_id) is not table or not table.listening:
            return
        if not bot.consent.has_consent(table.guild_id, player):
            continue
        # Whoever said it must still agree to be recorded: their questions go if not.
        for speaker in {w.speaker for w in questions.waiting.get(player, [])}:
            if not bot.consent.has_consent(table.guild_id, speaker):
                questions.drop_speaker(speaker)

        def shown_now(entity_id: str) -> tuple[Any, ...] | None:
            have = table.sheet_links.get(entity_id)
            return None if have is None or have.sheet is None else sheet_changes.shown(have.sheet)

        ready = questions.ready(player, now, shown_now=shown_now)
        if ready:
            await _ask(bot, table, player, ready, campaign)
        card = questions.take_card(player)
        if card is not None:
            from dmbot.ui import sheets as sheets_ui

            text = await sheets_ui.status_card(bot, table.guild_id, table.campaign_id, card)
            await _dm(bot, player, text, None)


async def _ask(
    bot: Any,
    table: Any,
    player: int,
    ready: list[sheet_questions.Waiting],
    campaign: Any,
) -> None:
    """One private message with the questions, naming the campaign (a player may be in two
    servers) and with the Open on D&D Beyond link. Not if they turned the questions off or no
    longer play the character."""
    kept: list[sheet_questions.Waiting] = []
    for w in ready:
        fresh = await bot.sheets.sheet(table.guild_id, table.campaign_id, w.entity_id)
        # Checked after the await too: the speaker may have withdrawn while it ran.
        if (
            fresh is not None
            and fresh.asks
            and fresh.played_by == player
            and bot.consent.has_consent(table.guild_id, w.speaker)
        ):
            kept.append(w)
    if not kept:
        return
    where = f" in **{discord.utils.escape_markdown(campaign.name)}**" if campaign else ""
    view = discord.ui.View(timeout=None)
    if len({w.url for w in kept}) == 1:  # two characters: no button, it could be the wrong one
        view.add_item(
            discord.ui.Button(label=OPEN_LABEL, style=discord.ButtonStyle.link, url=kept[0].url)
        )
    text = f"📜 About your D&D Beyond sheet{where}:\n" + "\n".join(
        dict.fromkeys(w.text for w in kept)
    )
    text += "\nTurn these questions off any time in 📜 My character sheet. Only you see this."
    await _dm(bot, player, text, view)


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
