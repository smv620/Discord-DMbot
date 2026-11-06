"""The DM screen's offer to set the game clock from an imported session (#2979; docs/PLAN.md,
"TimeBot"): "This session ended on **Day 5, evening**. Set the clock to that?" with **🕰️ Set it**
and **Not now**. Nothing moves until a DM presses the first, and then the clock changes as a DM's
manual set does it (the same note with Undo, the players' clock line and any timers due). DM screen
only: a player never sees it and it is never in a transcript. Register with
`bot.add_dynamic_items(ImportTimeButton)`.

The target game minute rides in the button's id, so a press after a restart still works. Every
press checks again that the person is one of the campaign's DMs, that the clock is set and the
party not split, and that the target is still later than the clock (an offer is never a way to move
the clock back: the DM may have moved it on since). One offer is applied once.
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

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
MINUTE_DIGITS = 9  # game minutes: far beyond the clock's longest day count
NOT_A_DM = "Only a DM of this campaign can press this. Ask your DM."
GONE = "I can't find that campaign any more."
FAILED = "Something went wrong, and nothing moved. Use the clock's buttons."
NO_CLOCK = "There's no game clock yet. Set one up: Settings, then Game clock."
SPLIT = "The party is split, so I didn't move the clock. Use the clock's own buttons."
PAST = "The clock is already at or past that. Nothing moved."
ALREADY = "That one was already done."
BAD = "That button is out of date. Use the clock's buttons."
SPLIT_WHY = (
    "🕰️ This imported session may have passed game time, but the party is split, so I didn't "
    "offer to set the clock. Use the clock's own buttons when the party is together."
)

# Offers being applied right now, and applied: a second press (the same DM twice, or two DMs)
# must not set the clock twice. In memory is enough: the buttons come off once it is done.
_BEING_APPLIED: set[int] = set()
_APPLIED: OrderedDict[int, None] = OrderedDict()
_APPLIED_KEPT = 500


def offer_view(campaign_id: str, minute: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ImportTimeButton(campaign_id, str(minute)))
    view.add_item(ImportTimeButton(campaign_id, "no"))
    return view


class ImportTimeButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:itime:{_ID}:(?P<what>no|[0-9]{{1,{MINUTE_DIGITS}}})",
):
    def __init__(self, campaign_id: str, what: str) -> None:
        no = what == "no"
        super().__init__(
            discord.ui.Button(
                label="Not now" if no else "Set it",
                emoji=None if no else "🕰️",
                style=discord.ButtonStyle.secondary if no else discord.ButtonStyle.primary,
                custom_id=f"dmbot:itime:{campaign_id}:{what}",
            )
        )
        self.campaign_id, self.what = campaign_id, what

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ImportTimeButton:
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
                done = await self._set(interaction, client, campaign, int(self.what))
                if done is not None:
                    if key is not None:
                        _APPLIED[key] = None
                        while len(_APPLIED) > _APPLIED_KEPT:
                            _APPLIED.popitem(last=False)
                    await self._close(interaction, done, answered=True)
            finally:
                if key is not None:
                    _BEING_APPLIED.discard(key)
        except Exception:
            log.exception("The imported-session clock offer's button failed")
            await _reply(interaction, FAILED)

    async def _close(
        self, interaction: discord.Interaction, note: str, *, answered: bool = False
    ) -> None:
        """Take the buttons away and say what was done, in the offer's own message."""
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
            log.warning("The clock offer's buttons could not be taken off")

    async def _set(
        self, interaction: discord.Interaction, client: Any, campaign: Any, minute: int
    ) -> str | None:
        """Set the clock through the same path as a DM's manual set. The note to put on the offer,
        or None if nothing moved (the presser has been told why)."""
        before = await client.clocks.get(campaign.guild_id, campaign.id)
        if before is None:
            await _reply(interaction, NO_CLOCK)
            return None
        if before.split:
            await _reply(interaction, SPLIT)
            return None
        if minute <= before.clock.minute:
            await _reply(interaction, PAST)  # never backwards, and never "set" to where it is
            return None
        try:
            result = await clock_screen.press(
                client,
                campaign.guild_id,
                campaign.id,
                interaction.user.id,
                "set",
                set_to=minute,
                refuse_if_split=True,
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return None
        if result.before is None or result.after is None:
            await _reply(interaction, NO_CLOCK)
            return None
        if result.after.minute == result.before.minute:
            await _reply(interaction, SPLIT)
            return None
        # The clock has moved. What follows only shows it: a failure there is logged, never a
        # reason to leave the buttons on (a second press would set it again).
        try:
            stored = await client.clocks.get(campaign.guild_id, campaign.id)
            if stored is not None:
                await clock_screen.show(client, campaign, stored)
                await clock_screen.update_players(client, campaign, "set", result)
                await timer_screen.announce_due(client, campaign, stored.clock.minute)
            await clock_screen.say(
                client,
                campaign,
                [
                    f"🕰️ Clock set to {game.short_label(result.after.minute)} "
                    "(where the imported session ended).",
                    *result.lines,
                ],
                clock_screen.undo_id(result.before, result.after),
            )
        except Exception:
            log.exception("The clock was set, but showing it failed")
        return f"Clock set to {game.short_label(result.after.minute)}."


async def _reply(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True)
