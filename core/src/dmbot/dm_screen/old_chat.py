"""The old `#dmb-chat` channel (#3212): DMbot no longer makes, reads or posts in it.

The table's chat is now the voice channel's own text chat, or the one channel the DM picked. A
campaign that still has an old channel gets **one notice** on the DM screen with a button to remove
it. DMbot never deletes the channel by itself: the DM presses **Remove the old channel**, sees one
confirmation, and only then it goes. Only the campaign's DMs may press either button, checked
again on every press, and only a channel DMbot named like a DMbot chat channel is ever deleted.
The transcripts and recaps already posted there stay until it is removed.

The notice is told once for each run of the bot (nothing is stored: no migration), so after a
restart a DM who has not removed the channel is told once more. Register with
`bot.add_dynamic_items(OldChatButton)`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.dm_screen.names import PREFIX
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NAME_PREFIX = f"{PREFIX}-chat-"  # how DMbot named it; nothing else is ever deleted here
ONLY_DMS = "Only this campaign's DMs can remove the old channel."
GONE = "That campaign isn't here any more. Nothing was changed."
ALREADY = "The old channel is already gone."
NOT_OURS = "That isn't a channel DMbot made, so DMbot won't delete it. Nothing was changed."
CANT = (
    "DMbot isn't allowed to delete channels here, so it left the old one alone. Remove it yourself "
    "in Discord, or give DMbot the Manage Channels permission and press the button again."
)
KEPT = "Okay, the old channel stays. Nothing was changed."
DONE = "🗑️ The old channel is removed."

_TEMPLATE = r"dmbot:oldchat:(?P<act>ask|yes|keep):(?P<campaign>[0-9a-f]{32})"
_LABELS = {
    "ask": ("🗑️ Remove the old channel", discord.ButtonStyle.secondary),
    "yes": ("Yes, delete it", discord.ButtonStyle.danger),
    "keep": ("Keep it", discord.ButtonStyle.secondary),
}


def notice_text(channel_id: int) -> str:
    return (
        f"DMbot doesn't use <#{channel_id}> any more: players can ask in the voice channel's chat "
        "(or the chat channel you picked). The old channel is still there, with everything in it. "
        "DMbot will not delete it by itself."
    )


def confirm_text(name: str) -> str:
    return (
        f"Delete **{discord.utils.escape_markdown(name)}** for good? Everything in it goes, "
        "including the players' old questions and DMbot's answers. This can't be undone."
    )


class OldChatButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    def __init__(self, act: str, campaign_id: str) -> None:
        label, style = _LABELS[act]
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=f"dmbot:oldchat:{act}:{campaign_id}"
            )
        )
        self.act, self.campaign_id = act, campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OldChatButton:
        return cls(match["act"], match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.act)
        except Exception:
            log.exception("An old-chat button failed")
            await _tell(interaction, "Something went wrong. Nothing was changed.")


def notice_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(OldChatButton("ask", campaign_id))
    return view


def confirm_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(OldChatButton("yes", campaign_id))
    view.add_item(OldChatButton("keep", campaign_id))
    return view


async def press(interaction: discord.Interaction, campaign_id: str, act: str) -> None:
    """What a button does. The campaign and the person are checked again every time."""
    bot = _bot(interaction)
    guild = interaction.guild
    campaign = await bot.campaigns.get(guild.id, campaign_id) if guild is not None else None
    if guild is None or campaign is None:
        await _tell(interaction, GONE)
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return
    channel_id = campaign.dmbchat_channel_id
    channel = guild.get_channel(channel_id) if channel_id is not None else None
    if channel is None:
        if channel_id is not None:  # it is gone already: forget it
            await bot.campaigns.set_dmbchat_channel(guild.id, campaign.id, None)
        await _tell(interaction, ALREADY)
        return
    if not str(getattr(channel, "name", "")).startswith(NAME_PREFIX):
        await _tell(interaction, NOT_OURS)
        return
    if act == "ask":
        await _tell(interaction, confirm_text(channel.name), view=confirm_view(campaign.id))
        return
    if act == "keep":
        await interaction.response.edit_message(content=KEPT, view=None)
        return
    try:
        await channel.delete(reason="The DM removed the old DMbot chat channel (#3212)")
    except discord.Forbidden:
        await interaction.response.edit_message(content=CANT, view=None)
        return
    await bot.campaigns.set_dmbchat_channel(guild.id, campaign.id, None)
    await interaction.response.edit_message(content=DONE, view=None, allowed_mentions=NO_PINGS)
