"""The DM screen's time-jump suggestion (#1758; docs/PLAN.md, "TimeBot"): one line quoting what the
DM narrated, "about 2 hours?", and two buttons, **+2 hours** and **Not now**. Nothing moves until
a DM presses the first: it goes through the clock's own path (the same note, the players' clock and
an Undo button as "an hour later" said aloud). DM screen only: a player never sees it and it is
never in a transcript. Register with `bot.add_dynamic_items(TimeJumpButton)`.

The amount rides in the button's id (10 to 1,440 minutes), so a press after a restart still works;
every press checks again that the person is one of the campaign's DMs, and the clock refuses a
moment when the party is split.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections import OrderedDict
from typing import Any

import discord

from dmbot.campaigns import CampaignError
from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import effects as timer_screen
from dmbot.mentions import NO_PINGS
from dmbot.timebot import clock as game
from dmbot.timebot import suggest

log = logging.getLogger(__name__)

# Suggestion messages whose "+N" is being applied right now: a second press (the same DM twice,
# or two DMs) of one suggestion must not move the clock twice. In memory is enough: the buttons
# come off once it is done.
_BEING_APPLIED: set[int] = set()
# Suggestions already applied: if taking the buttons off then fails, a later press still moves
# nothing. Bounded: the oldest are forgotten (a Discord message id only ever grows).
_APPLIED: OrderedDict[int, None] = OrderedDict()
_APPLIED_KEPT = 500
ALREADY = "That one was already done."
_ID = r"(?P<campaign>[0-9a-f]{32})"
QUOTE_MAX = 70
NOT_A_DM = "Only a DM of this campaign can press this. Ask your DM."
GONE = "I can't find that campaign any more."
FAILED = "Something went wrong, and nothing moved. Use the clock's buttons."
STILL = "The clock didn't move. Use the clock's buttons."
FIGHT = "A fight is on, so I didn't move the clock. Use the clock's buttons."
SPLIT = "The party is split, so I didn't move the clock. Use the clock's own buttons."
NO_CLOCK = "There's no game clock yet. Set one up: Settings, then Game clock."
BAD = "That button is out of date. Use the clock's buttons."


def suggestion_text(line: str, minutes: int) -> str:
    """⏳ "you walk for a while": about 2 hours?"""
    said = " ".join(line.split())
    if len(said) > QUOTE_MAX:
        said = said[: QUOTE_MAX - 1].rstrip() + "…"
    quote = discord.utils.escape_markdown(said)
    return f"⏳ “{quote}”: about {suggest.hours_label(minutes)[1:]}?"


def suggestion_view(campaign_id: str, minutes: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TimeJumpButton(campaign_id, str(minutes)))
    view.add_item(TimeJumpButton(campaign_id, "no"))
    return view


class TimeJumpButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:tjump:{_ID}:(?P<what>no|[0-9]{{2,4}})",
):
    def __init__(self, campaign_id: str, what: str) -> None:
        no = what == "no"
        super().__init__(
            discord.ui.Button(
                label="Not now" if no else suggest.hours_label(int(what)),
                emoji=None if no else "⏳",
                style=discord.ButtonStyle.secondary if no else discord.ButtonStyle.primary,
                custom_id=f"dmbot:tjump:{campaign_id}:{what}",
            )
        )
        self.campaign_id, self.what = campaign_id, what

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TimeJumpButton:
        return cls(match["campaign"], match["what"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            if interaction.user.id not in campaign.dm_user_ids:
                await _reply(interaction, NOT_A_DM)
                return
            if self.what == "no":
                await self._close(interaction, "Not now.")
                return
            minutes = int(self.what)
            if not suggest.MIN_MINUTES <= minutes <= suggest.MAX_MINUTES:
                await _reply(interaction, BAD)
                return
            await interaction.response.defer()
            key = interaction.message.id if interaction.message is not None else None
            if key is not None:
                if key in _APPLIED:
                    await _reply(interaction, ALREADY)
                    return
                if key in _BEING_APPLIED:
                    return  # already on its way: the other press will close the buttons
                _BEING_APPLIED.add(key)
            try:
                moved = await self._move(interaction, client, campaign, minutes)
                if moved is not None:
                    if key is not None:
                        _APPLIED[key] = None
                        while len(_APPLIED) > _APPLIED_KEPT:
                            _APPLIED.popitem(last=False)
                    await self._close(interaction, moved, answered=True)
            finally:
                if key is not None:
                    _BEING_APPLIED.discard(key)
        except Exception:
            log.exception("The time-jump suggestion's button failed")
            await _reply(interaction, FAILED)

    async def _close(
        self, interaction: discord.Interaction, note: str, *, answered: bool = False
    ) -> None:
        """Take the buttons away and say what was done, in the suggestion's own message."""
        message = interaction.message
        content = message.content if message is not None else ""
        text = f"{content}\n_{note}_"
        try:
            if answered:
                if message is not None:
                    await message.edit(content=text, view=None, allowed_mentions=NO_PINGS)
            else:
                await interaction.response.edit_message(content=text, view=None)
        except discord.HTTPException:
            log.warning("The time-jump suggestion's buttons could not be taken off")

    async def _move(
        self, interaction: discord.Interaction, client: Any, campaign: Any, minutes: int
    ) -> str | None:
        """The clock moves through the same path as "an hour later" said at the table: the
        change, the clock redrawn, the players' clock, any timers due, and one DM-screen note
        with Undo. The note to put on the suggestion, or None if nothing moved (the presser has
        been told why)."""
        before = await client.clocks.get(campaign.guild_id, campaign.id)
        if before is not None and game.in_combat(before.clock):
            await _reply(interaction, FIGHT)  # the suggestion is older than the fight
            return None
        try:
            result = await clock_screen.press(
                client,
                campaign.guild_id,
                campaign.id,
                interaction.user.id,
                "advance",
                minutes=minutes,
                refuse_if_split=True,
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return None
        if result.before is None or result.after is None:
            await _reply(interaction, NO_CLOCK)
            return None
        if result.after.minute == result.before.minute:
            held = await client.clocks.get(campaign.guild_id, campaign.id)
            await _reply(interaction, SPLIT if held is not None and held.split else STILL)
            return None
        # The clock has moved. What follows only shows it: a failure there is logged, never a
        # reason to leave the buttons on (a second press would move the clock again).
        try:
            stored = await client.clocks.get(campaign.guild_id, campaign.id)
            if stored is not None:
                await clock_screen.show(client, campaign, stored)
                await clock_screen.update_players(client, campaign, "advance", result)
                await timer_screen.announce_due(client, campaign, stored.clock.minute)
            await clock_screen.say(
                client,
                campaign,
                [clock_screen.rest_note("advance", result.after, minutes), *result.lines],
                clock_screen.undo_id(result.before, result.after),
            )
        except Exception:
            log.exception("The clock moved, but showing it failed")
        return f"Clock moved forward {suggest.hours_label(minutes)[1:]}."


async def _reply(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True)
