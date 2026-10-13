"""FightBot (#1129; docs/PLAN.md, "FightBot"): while the table is in combat, a short private
reminder to each player of their own options that could help now ("You still have Hunter's Mark
and Cure Wounds"). It advises; the player decides. It never rules on anything and never posts
publicly: every message is a private message to that one player.

What it may read is limited by `memory.fight_prompt`: only that player's own sheet, the round,
and what people who agreed said at the table lately, minus any line with a secret name. Never
the DM screen, monster stats, DM notes, or another player's private messages.

Limits: only in combat, only players who agreed to be recorded, at most one AI call (and so one
message) per player every 2 minutes, and only when something changed since the last (a new
round, hit points, a condition). Each message has **Snooze 10 min**, **Quiet until next
session** and **Never**; "Never" is kept per player per campaign and can be undone from
📜 My character sheet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import discord

from dmbot.ai import AIError
from dmbot.memory import fight_prompt
from dmbot.memory.sheet_store import CharacterSheet, FightPlayer
from dmbot.sidebar.service import SCENE_SECONDS

log = logging.getLogger(__name__)

REMIND_GAP_S = 120.0  # per player, between one call (and so one message) and the next
SNOOZE_S = 600
MAX_CALLS_PER_TICK = 2  # a table never gets a burst of AI calls
TICK_S = 30
MAX_TOKENS = 120
_ID = r"(?P<campaign>[0-9a-f]{32})"
SNOOZED = "😴 Okay, back in 10 minutes."
QUIET = "🤫 Okay, quiet until the next session."
NO_SESSION = "There is no game running just now, so there is nothing to quiet."
NEVER = "🚫 FightBot is off. Turn it back on in 📜 My character sheet."
NOT_SAVED = "Sorry, that didn't save. Please try again."
NOT_PLAYING = "FightBot is only for players who have a character in this campaign."
FOOTER = "FightBot reminder. Only you see this. It's your call."
INTRO = (
    "FightBot reminds you of your own spells and features during a fight. "
    "Tap **Never** to turn it off.\n"
)


@dataclass(slots=True)
class FightState:
    """What one session remembers in memory only: the last state each player's character was
    reminded about (so nothing is said twice for the same thing) and who has been told what
    FightBot is. Everything that must survive a restart is in the database
    (`fightbot_players`)."""

    last_fp: dict[tuple[int, str], tuple[Any, ...]] = field(default_factory=dict)
    introduced: set[int] = field(default_factory=set)


def may_remind(state: FightPlayer, session_started: int, now: float) -> bool:
    """Not Never, not quiet for this session, not snoozed, and at least two minutes since the
    last call for this player: all kept in the database, so a restart forgets none of it."""
    if state.never or state.quiet_since == session_started:
        return False
    if state.snoozed_until is not None and now < state.snoozed_until:
        return False
    return state.last_call_at is None or now - state.last_call_at >= REMIND_GAP_S


async def run(bot: Any, table: Any) -> None:
    """The loop for one session: look every half minute; ends with the session."""
    while bot.tables.get(table.guild_id) is table:
        await asyncio.sleep(TICK_S)
        if not table.listening or table.campaign_id is None:
            continue
        try:
            await tick(bot, table)
        except Exception as exc:  # never the text: it can quote a row
            log.error("FightBot: a pass failed (%s)", type(exc).__name__)


async def _secret_texts(bot: Any, table: Any) -> list[str] | None:
    """The campaign's secret names as text, or None if they can't be known (then nothing is
    sent: a reminder must never be built without being able to leave them out)."""
    if bot.lookup is None:
        return None
    try:
        lookup = await bot.lookup.get(table.guild_id, table.campaign_id)
    except Exception:
        return None
    return [n.text for n in lookup.names if n.secret]


def _present(bot: Any, table: Any) -> set[int]:
    """The people in the table's voice channel right now, not counting bots: only they are at
    the table tonight (consent and a sheet carry over, but an absent player is not reminded)."""
    guild = bot.get_guild(table.guild_id)
    channel = guild.get_channel(table.voice_channel_id) if guild is not None else None
    return {m.id for m in getattr(channel, "members", []) if not m.bot}


async def tick(bot: Any, table: Any) -> None:
    if bot.fight_ai is None or bot.sheets is None or bot.clocks is None:
        return
    stored = await bot.clocks.get(table.guild_id, table.campaign_id)
    fight: FightState = table.fight
    if stored is None or stored.clock.combat_round is None:
        fight.last_fp.clear()  # out of combat: the next fight starts fresh
        return
    round_number: int = stored.clock.combat_round
    present = _present(bot, table)
    if not present:
        return
    found: list[CharacterSheet] = await bot.sheets.sheets(table.guild_id, table.campaign_id)
    players = await bot.sheets.fight_players(table.guild_id, table.campaign_id)
    wall = time.time()
    secrets: list[str] | None = None
    allowed: bool | None = None
    calls = 0
    done: set[int] = set()  # players called this pass: one call each, however many characters
    for sheet in found:
        user = sheet.played_by
        if user is None or sheet.sheet is None or user not in present or user in done:
            continue
        if not may_remind(players.get(user, FightPlayer()), table.started_at, wall):
            continue
        if not bot.consent.has_consent(table.guild_id, user):
            continue
        fp = fight_prompt.fingerprint(sheet.sheet, round_number)
        if fight.last_fp.get((user, sheet.entity_id)) == fp:
            continue  # nothing new since the last reminder
        if calls >= MAX_CALLS_PER_TICK:
            break  # the rest next time
        if secrets is None:
            secrets = await _secret_texts(bot, table)
            if secrets is None:
                return
        if allowed is None:  # once a tick, when the first player is due
            campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
            allowed = campaign is not None and await bot.plan_allows("ai", table.guild_id, campaign)
        if not allowed:
            return
        # Checked again after the awaits: the session, the pause and their consent.
        if (
            bot.tables.get(table.guild_id) is not table
            or not table.listening
            or not bot.consent.has_consent(table.guild_id, user)
        ):
            return
        calls += 1
        done.add(user)
        fight.last_fp[(user, sheet.entity_id)] = fp
        await bot.sheets.fight_called(table.guild_id, table.campaign_id, user, int(wall))
        said = fight_prompt.safe_lines(_table_lines(bot, table, time.monotonic()), secrets)
        try:
            reply = await bot.fight_ai.complete(
                fight_prompt.PROMPT,
                fight_prompt.build(sheet.sheet, round_number, said),
                max_tokens=MAX_TOKENS,
            )
        except AIError as exc:
            log.info("FightBot: no reminder this time (%s)", type(exc).__name__)
            continue
        text = fight_prompt.clean_reply(reply.text, fight_prompt.sheet_names(sheet.sheet))
        # (A failed or empty answer still uses up this change: no retry until something new.)
        if text is not None and await _may_send(bot, table, user):
            first = user not in fight.introduced
            fight.introduced.add(user)
            await _send(bot, table, user, text, intro=first)


async def _may_send(bot: Any, table: Any, user_id: int) -> bool:
    """Checked again right before a message goes out: the AI call takes seconds, and the
    session may have ended, the table paused, the player withdrawn their consent, left the
    voice channel or silenced FightBot meanwhile."""
    if not (
        bot.tables.get(table.guild_id) is table
        and table.listening
        and bot.consent.has_consent(table.guild_id, user_id)
        and user_id in _present(bot, table)
    ):
        return False
    players = await bot.sheets.fight_players(table.guild_id, table.campaign_id)
    state = players.get(user_id, FightPlayer())
    now = time.time()
    # (The gap is not checked here: this very call has just recorded itself.)
    return not (
        state.never
        or state.quiet_since == table.started_at
        or (state.snoozed_until is not None and now < state.snoozed_until)
    )


def _table_lines(bot: Any, table: Any, now: float) -> list[tuple[str, str]]:
    """The last few things *said aloud* at the table, by people who still agree to be recorded
    and no longer ago than the sidebar's scene: the cleaned lines the shared transcript shows.
    Typed chat is raw text anyone can write anything in, so it is not used."""
    lines: list[tuple[str, str]] = []
    for at, speaker, text in list(table.recent.spoken)[-40:]:
        if now - at <= SCENE_SECONDS and bot.consent.has_consent(table.guild_id, speaker):
            lines.append((bot.name_of(table.guild_id, speaker), text))
    return lines


def buttons(guild_id: int, campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("snooze", "quiet", "never"):
        view.add_item(FightButton(guild_id, campaign_id, action))
    return view


async def _send(bot: Any, table: Any, user_id: int, text: str, *, intro: bool = False) -> None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(
            f"⚔️ {discord.utils.escape_markdown(text)}\n{INTRO if intro else ''}{FOOTER}",
            view=buttons(table.guild_id, table.campaign_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as exc:  # their private messages may be closed
        log.info("FightBot: couldn't message user %s (%s)", user_id, type(exc).__name__)


_LABELS = {
    "snooze": ("Snooze 10 min", "😴", discord.ButtonStyle.secondary),
    "quiet": ("Quiet until next session", "🤫", discord.ButtonStyle.secondary),
    "never": ("Never", "🚫", discord.ButtonStyle.secondary),
}


class FightButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:fight:(?P<guild>\d{{1,20}}):{_ID}:(?P<do>snooze|quiet|never)",
):
    """One of a reminder's three silences. It only ever changes the presser's own settings, and
    only when they play a character in the campaign the button names (a button id is just text
    that anyone's client can send, so a row is never written for a stranger)."""

    def __init__(self, guild_id: int, campaign_id: str, action: str) -> None:
        label, emoji, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"dmbot:fight:{guild_id}:{campaign_id}:{action}",
            )
        )
        self.guild_id, self.campaign_id, self.action = guild_id, campaign_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FightButton:
        return cls(int(match["guild"]), match["campaign"], match["do"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        user = interaction.user.id
        # Only ever the presser's own settings, saved whether or not a session is running.
        try:
            found = await bot.sheets.sheets(self.guild_id, self.campaign_id)
            if not any(s.played_by == user for s in found):
                await interaction.response.send_message(NOT_PLAYING, ephemeral=True)
                return
            if self.action == "never":
                await bot.sheets.set_fightbot(self.guild_id, self.campaign_id, user, False)
                said = NEVER
            elif self.action == "snooze":
                until = int(time.time() + SNOOZE_S)
                await bot.sheets.fight_snooze(self.guild_id, self.campaign_id, user, until)
                said = SNOOZED
            else:
                table = bot.tables.get(self.guild_id)
                if table is None or table.campaign_id != self.campaign_id:
                    said = NO_SESSION  # the next session starts fresh anyway
                else:
                    await bot.sheets.fight_quiet(
                        self.guild_id, self.campaign_id, user, table.started_at
                    )
                    said = QUIET
        except Exception:
            log.exception("FightBot: a silence button failed")
            await interaction.response.send_message(NOT_SAVED, ephemeral=True)
            return
        text = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=f"{text}\n\n{said}", view=None)
