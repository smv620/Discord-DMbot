"""Share a recap (#2118): a DM picks one of the campaign's recaps and gets its public link, to copy
or to send to the players in `dmb-chat`.

The link shows only the table version of a recap that was posted. Sharing one that was not posted
yet counts as posting it, with every check Post makes (`DMBot.recap_share_link`), so nothing here
decides anything. Only the campaign's DMs, checked on every press, in the picker and again on each
button, because the replies are private but the messages can be found by anyone.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this campaign any more. Nothing was shared."
NO_SITE = "The website address isn't set up, so there's no link to share."
NO_RECAPS = "There's no recap to share yet. One is written after each session."
PICK = "Which recap? The newest is first. Anyone with the link can read the story, no sign-in."
HOW = "How do you want to share it?"
NO_CHAT = "There's no chat channel for this campaign yet. It's made when a session starts."
SENT = "✅ Sent the link to the players."
SEND_FAILED = "DMbot couldn't write in the chat channel. Check it can post there, then try again."
TIMEOUT_S = 840  # a private reply's buttons stop working after 15 minutes
_TEMPLATE = r"dmbot:recapshare:(?P<campaign>[0-9a-f]{32})"


def public_link(site: str, token: str) -> str:
    """The recap page anyone with the link can read. The token is a secret: never logged."""
    return f"{site.rstrip('/')}/r/{token}"


async def _campaign(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    """The campaign, if the person pressing is one of its DMs; otherwise they are told why."""
    bot = _bot(interaction)
    guild = interaction.guild
    campaign = await bot.campaigns.get(guild.id, campaign_id) if guild is not None else None
    if campaign is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_YOURS)
        return None
    return campaign


class ShareButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Share a recap",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recapshare:{campaign_id}",
                row=row,
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ShareButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        bot = _bot(interaction)
        if not bot.settings.site_url:
            await _tell(interaction, NO_SITE)
            return
        if bot.recaps is None:
            await _tell(interaction, NO_RECAPS)
            return
        recent = await bot.recaps.recent(campaign.guild_id, campaign.id)
        if not recent:
            await _tell(interaction, NO_RECAPS)
            return
        await _tell(interaction, PICK, view=PickView(campaign.id, recent))


class PickView(discord.ui.View):
    """The recaps to choose from, newest first, at most 25 (Discord's limit for a menu)."""

    def __init__(self, campaign_id: str, recent: list[tuple[int, str, str]]) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id = campaign_id
        options = [
            discord.SelectOption(
                label=(title or "A recap")[:100],
                value=str(at),
                description="Not posted yet: sharing posts it" if status == "draft" else None,
            )
            for at, title, status in recent[:25]
        ]
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder="Pick a recap", options=options
        )
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        at = int(self.select.values[0])
        await interaction.response.send_message(
            HOW, view=HowView(campaign.id, at), ephemeral=True, allowed_mentions=NO_PINGS
        )


class HowView(discord.ui.View):
    def __init__(self, campaign_id: str, session_at: int) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id, self.session_at = campaign_id, session_at

    @discord.ui.button(label="Copy the link", style=discord.ButtonStyle.primary)
    async def copy(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        link = await self._link(interaction)
        if link is not None:
            await interaction.followup.send(link, ephemeral=True, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Send the link to the players", style=discord.ButtonStyle.success)
    async def send(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        bot = _bot(interaction)
        channel = (
            bot.get_channel(campaign.dmbchat_channel_id) if campaign.dmbchat_channel_id else None
        )
        if (
            channel is None
            or not hasattr(channel, "send")
            or getattr(getattr(channel, "guild", None), "id", campaign.guild_id)
            != campaign.guild_id
        ):
            await _tell(interaction, NO_CHAT)
            return
        link = await self._link(interaction, campaign)
        if link is None:
            return
        number = await bot.recaps.number_of(campaign.guild_id, campaign.id, self.session_at)
        try:
            await channel.send(f"📜 Recap of session {number}: {link}", allowed_mentions=NO_PINGS)
        except Exception:
            await interaction.followup.send(SEND_FAILED, ephemeral=True)
            return
        await interaction.followup.send(SENT, ephemeral=True)

    async def _link(
        self, interaction: discord.Interaction, campaign: Any | None = None
    ) -> str | None:
        """The link after every check, or None once the DM has been told why."""
        campaign = campaign or await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return None
        bot = _bot(interaction)
        await interaction.response.defer(ephemeral=True, thinking=False)
        token, why = await bot.recap_share_link(campaign, self.session_at)
        if token is None:
            await interaction.followup.send(why, ephemeral=True, allowed_mentions=NO_PINGS)
            return None
        return public_link(bot.settings.site_url, token)
