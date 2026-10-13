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
from dmbot.memory.sheet_store import CharacterSheet

log = logging.getLogger(__name__)

REMIND_GAP_S = 120.0  # per player, between one call (and so one message) and the next
SNOOZE_S = 600.0
TICK_S = 30
MAX_TOKENS = 120
_ID = r"(?P<campaign>[0-9a-f]{32})"
SNOOZED = "😴 Okay, back in 10 minutes."
QUIET = "🤫 Okay, quiet until the next session."
NEVER = "🚫 FightBot is off. Turn it back on in 📜 My character sheet."
FOOTER = "FightBot reminder. Only you see this. It's your call."
INTRO = (
    "FightBot reminds you of your own spells and features during a fight. "
    "Tap **Stop FightBot** to turn it off.\n"
)


@dataclass(slots=True)
class FightState:
    """One session's FightBot memory: lost with the session, never shared between campaigns."""

    last_try: dict[int, float] = field(default_factory=dict)
    last_fp: dict[int, tuple[Any, ...]] = field(default_factory=dict)
    snoozed_until: dict[int, float] = field(default_factory=dict)
    quiet: set[int] = field(default_factory=set)  # "quiet until next session"
    introduced: set[int] = field(default_factory=set)  # told once what FightBot is


def silenced(state: FightState, user_id: int, now: float) -> bool:
    return user_id in state.quiet or now < state.snoozed_until.get(user_id, 0.0)


def due(state: FightState, user_id: int, now: float) -> bool:
    """Not silenced, and at least two minutes since the last call for this player."""
    return not silenced(state, user_id, now) and (
        now - state.last_try.get(user_id, -1e9) >= REMIND_GAP_S
    )


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
        return []
    try:
        lookup = await bot.lookup.get(table.guild_id, table.campaign_id)
    except Exception:
        return None
    return [n.text for n in lookup.names if n.secret]


async def tick(bot: Any, table: Any) -> None:
    if bot.fight_ai is None or bot.sheets is None or bot.clocks is None:
        return
    stored = await bot.clocks.get(table.guild_id, table.campaign_id)
    fight: FightState = table.fight
    if stored is None or stored.clock.combat_round is None:
        fight.last_fp.clear()  # out of combat: the next fight starts fresh
        return
    round_number: int = stored.clock.combat_round
    now = time.monotonic()
    found: list[CharacterSheet] = await bot.sheets.sheets(table.guild_id, table.campaign_id)
    secrets: list[str] | None = None
    allowed: bool | None = None
    for sheet in found:
        user = sheet.played_by
        if user is None or sheet.sheet is None or not sheet.fightbot:
            continue
        if not due(fight, user, now) or not bot.consent.has_consent(table.guild_id, user):
            continue
        fp = fight_prompt.fingerprint(sheet.sheet, round_number)
        if fight.last_fp.get(user) == fp:
            continue  # nothing new since the last reminder
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
        fight.last_try[user] = now
        fight.last_fp[user] = fp
        said = fight_prompt.safe_lines(_table_lines(bot, table, now), secrets)
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
        if text is not None and _may_send(bot, table, user):
            first = user not in fight.introduced
            fight.introduced.add(user)
            await _send(bot, table, user, text, intro=first)


def _may_send(bot: Any, table: Any, user_id: int) -> bool:
    """Checked again right before a message goes out: the AI call takes seconds, and the
    session may have ended, the table paused, the player withdrawn their consent or silenced
    FightBot meanwhile."""
    return (
        bot.tables.get(table.guild_id) is table
        and table.listening
        and bot.consent.has_consent(table.guild_id, user_id)
        and not silenced(table.fight, user_id, time.monotonic())
    )


def _table_lines(bot: Any, table: Any, now: float) -> list[tuple[str, str]]:
    """The last few lines said at the table by people who still agree to be recorded: the same
    lines the shared transcript shows. (Recent holds nothing else: the DM sidebar and the DM
    screen are elsewhere.)"""
    lines: list[tuple[str, str]] = []
    for _at, speaker, text in list(table.recent.lines)[-40:]:
        if bot.consent.has_consent(table.guild_id, speaker):
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
            f"⚔️ {text}\n{INTRO if intro else ''}{FOOTER}",
            view=buttons(table.guild_id, table.campaign_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as exc:  # their private messages may be closed
        log.info("FightBot: couldn't message user %s (%s)", user_id, type(exc).__name__)


_LABELS = {
    "snooze": ("Snooze 10 min", "😴", discord.ButtonStyle.secondary),
    "quiet": ("Quiet this session", "🤫", discord.ButtonStyle.secondary),
    "never": ("Stop FightBot", "🚫", discord.ButtonStyle.secondary),
}


class FightButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:fight:(?P<guild>\d{{1,20}}):{_ID}:(?P<do>snooze|quiet|never)",
):
    """One of a reminder's three silences. It only ever changes the presser's own settings,
    so the message needs no check of who it was sent to."""

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
        try:
            if self.action == "never":
                kept = await bot.sheets.set_fightbot(self.guild_id, self.campaign_id, user, False)
                said = NEVER if kept else "Sorry, that didn't save. Please try again."
            else:
                table = bot.tables.get(self.guild_id)
                if table is not None and table.campaign_id == self.campaign_id:
                    if self.action == "snooze":
                        table.fight.snoozed_until[user] = time.monotonic() + SNOOZE_S
                    else:
                        table.fight.quiet.add(user)
                said = SNOOZED if self.action == "snooze" else QUIET
        except Exception:
            log.exception("FightBot: a silence button failed")
            said = "Sorry, that didn't save. Please try again."
            await interaction.response.send_message(said, ephemeral=True)
            return
        text = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=f"{text}\n\n{said}", view=None)
