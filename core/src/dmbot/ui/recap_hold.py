"""The DM screen's line while a recap waits for name answers (#2169): "📝 N name questions from
tonight. Answer them and DMbot writes the recap." with 📝 Check new names and Write the recap now.
The second button is the DM's choice to go ahead with what DMbot has. The buttons carry the
campaign and the session, so they work after a restart; only the campaign's DMs can press them,
checked on every press.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.recaps.hold import LINE_WRITING
from dmbot.ui.decision_lines import with_outcome
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.names import ReviewButton

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this campaign any more. Nothing was changed."
NOT_NOW = "No recap was written. Recaps are off for this campaign, or not in your plan."
OFF_EVERYWHERE = "Recaps are switched off for all of DMbot right now. No recap was written."
WRITING = LINE_WRITING  # (one wording, the same under every line)
ALREADY = "Already being written."

_TEMPLATE = r"dmbot:rhold:go:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"


class WriteNowButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, campaign_id: str, session_at: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Write the recap now",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:rhold:go:{campaign_id}:{session_at}",
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WriteNowButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        if guild is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, NOT_YOURS)
            return
        await interaction.response.defer()  # the checks below make several calls and a post
        # The line pressed is kept too, so the other copy (the reminder) comes off with it.
        bot.decision_lines.remember_pressed(
            hold_line_key(campaign.id, self.session_at), interaction
        )
        pressed = interaction.message.id if interaction.message is not None else None
        result = await bot.recap_hold_write_now(
            guild.id, campaign.id, self.session_at, pressed_id=pressed
        )
        if result == "off":
            await _tell(interaction, OFF_EVERYWHERE)
            return
        if result == "refused":
            await _tell(interaction, NOT_NOW)  # the buttons stay: the DM can try again
            return
        outcome = WRITING if result == "queued" else ALREADY
        if result != "queued":  # no hold to release: the other copies come off here instead
            await bot.decision_lines.close(
                hold_line_key(campaign.id, self.session_at), outcome, pressed_id=pressed
            )
        message = interaction.message
        await interaction.edit_original_response(
            content=with_outcome(message.content, outcome) if message is not None else outcome,
            view=None,
            allowed_mentions=NO_PINGS,
        )


def hold_line_key(campaign_id: str, session_at: int) -> tuple[str, str, int]:
    """What the bot calls the question and reminder lines of one held recap, so their buttons can
    come off when the hold is over (`DecisionLines`, #2174)."""
    return ("hold", campaign_id, session_at)


def hold_view(campaign_id: str, session_at: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(campaign_id))
    view.add_item(WriteNowButton(campaign_id, session_at))
    return view
