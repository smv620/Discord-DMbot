"""Share a recap (#2118): a DM picks one of the campaign's recaps and gets its public link, to copy
or to send to the players in `dmb-chat`.

The link shows only the table version of a recap that was posted. Sharing one that was not posted
yet counts as posting it, with every check Post makes (`DMBot.recap_share_link`), so nothing here
decides anything. Only the campaign's DMs, checked on every press, in the picker and again on each
button, because the replies are private but the messages can be found by anyone.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this campaign any more. Nothing was shared."
NO_SITE = "DMbot doesn't know its website address yet, so there's no link to share."
NO_RECAPS = "There's no recap to share yet. One is written after each session."
PICK = "Pick a recap to share. Newest first. Anyone with the link can read it, no sign-in."
HOW = "How do you want to share it?"
HOW_DRAFT = (
    "This recap isn't posted yet. Sharing it posts it, and anyone with the link can read it."
)
NO_CHAT = (
    "There's no chat channel for this campaign yet. It's made when a session starts. "
    "You can copy the link instead."
)
SENT = "✅ Sent the link to the players in your chat channel."
FAILED = "Something went wrong, so nothing was shared. Try again in a moment."
SENT_ALREADY = "The link was already sent. Nothing more was posted."
SEND_FAILED = (
    "I couldn't write in your chat channel, so the link wasn't sent. Let DMbot post there, "
    "then try again. You can copy the link instead."
)
COPY_LEAD = (
    "Here's the link. Anyone who has it can read the recap, no sign-in. "
    "Turn it off or make a new one on your recap page:\n"
)
TIMEOUT_S = 840  # a private reply's buttons stop working after 15 minutes
_TEMPLATE = r"dmbot:recapshare:(?P<campaign>[0-9a-f]{32})"


async def session_numbers(bot: Any, campaign: Any) -> dict[int, int]:
    """The campaign's real session numbers, by when each started: the same ones the transcript
    and the continuity pages say. Empty when they can't be read; then no number is shown."""
    transcripts = getattr(bot, "transcripts", None)
    if transcripts is None:
        return {}
    try:
        sessions = await transcripts.sessions(campaign.guild_id, campaign.id, limit=1000)
    except Exception:
        return {}
    return {s.started_at: s.number for s in sessions}


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
        if len(recent) == 1:  # nothing to pick from: straight to how
            at, _title, status = recent[0]
            await _tell(
                interaction,
                HOW_DRAFT if status == "draft" else HOW,
                view=HowView(campaign.id, at, draft=status == "draft"),
            )
            return
        await _tell(
            interaction,
            PICK,
            view=PickView(campaign.id, recent, await session_numbers(bot, campaign)),
        )


class PickView(discord.ui.View):
    """The recaps to choose from, newest first, at most 25 (Discord's limit for a menu)."""

    def __init__(
        self, campaign_id: str, recent: list[tuple[int, str, str]], numbers: dict[int, int]
    ) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id = campaign_id
        self.status = {str(at): status for at, _t, status in recent}
        # The session number first, so two similar titles differ on a phone's short menu.
        options = [
            discord.SelectOption(
                label=(
                    f"Session {numbers[at]}: {title or 'A recap'}"
                    if at in numbers
                    else title or "A recap"
                )[:100],
                value=str(at),
                description="Not posted yet" if status == "draft" else None,
            )
            for at, title, status in recent[:25]
        ]
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder="Choose a recap", options=options
        )
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        picked = self.select.values[0]
        draft = self.status.get(picked) == "draft"
        await interaction.response.send_message(
            HOW_DRAFT if draft else HOW,
            view=HowView(campaign.id, int(picked), draft=draft),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class HowView(discord.ui.View):
    """Copy or send. A recap not posted yet says so and its buttons say "Post and ...", so
    sharing never posts by surprise. Labels stay short for a phone."""

    def __init__(self, campaign_id: str, session_at: int, *, draft: bool = False) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id, self.session_at = campaign_id, session_at
        self.sent = False  # a second press of Send posts nothing more
        self._posted = False
        if draft:
            self.copy.label = "Post and copy link"
            self.send.label = "Post and send"

    @discord.ui.button(label="Copy the link", style=discord.ButtonStyle.primary)
    async def copy(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        link = await self._link(interaction)
        if link is not None:
            await interaction.followup.send(
                COPY_LEAD + link, ephemeral=True, allowed_mentions=NO_PINGS
            )

    @discord.ui.button(label="Send to the players", style=discord.ButtonStyle.success)
    async def send(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        # Claimed before the first await: two quick presses can't both get past this check, so
        # the link is posted to the players once. Any way out but a sent link gives it back.
        if self.sent:
            await _tell(interaction, SENT_ALREADY)
            return
        self.sent = True
        self._posted = False
        try:
            await self._send(interaction)
        finally:
            if not self._posted:  # only a link that really went out keeps the press used
                self.sent = False

    async def _send(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return False
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
            return False
        link = await self._link(interaction, campaign)
        if link is None:
            return False
        try:
            numbers = await session_numbers(bot, campaign)
            number = numbers.get(self.session_at)
            lead = f"📜 Recap of session {number}" if number is not None else "📜 A recap"
            await channel.send(f"{lead}: {link}", allowed_mentions=NO_PINGS)
            self._posted = True  # right after the send, before anything else can fail
        except Exception as exc:  # the deferred reply must always be answered
            log.warning("Share a recap: the send failed: %s", type(exc).__name__)  # never the link
            await interaction.followup.send(SEND_FAILED, ephemeral=True)
            return False
        await interaction.followup.send(SENT, ephemeral=True)
        return True

    async def _link(
        self, interaction: discord.Interaction, campaign: Any | None = None
    ) -> str | None:
        """The link after every check, or None once the DM has been told why."""
        campaign = campaign or await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return None
        bot = _bot(interaction)
        await interaction.response.defer(ephemeral=True, thinking=False)
        try:
            token, why = await bot.recap_share_link(campaign, self.session_at)
        except Exception as exc:  # the deferred reply must always be answered
            log.warning("Share a recap failed: %s", type(exc).__name__)  # never the link
            await interaction.followup.send(FAILED, ephemeral=True)
            return None
        if token is None:
            await interaction.followup.send(why, ephemeral=True, allowed_mentions=NO_PINGS)
            return None
        return public_link(bot.settings.site_url, token)
