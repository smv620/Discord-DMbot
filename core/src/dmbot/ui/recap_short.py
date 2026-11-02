"""The question about a short session (#2104): "Write a recap anyway?" with two buttons on the DM
screen. The buttons carry the campaign and the session, so they work after a restart. Only the
campaign's DMs can press them, checked on every press. **Write a recap** queues it as usual (the
plan, the recap switch and the month's allowance are still checked by the usual stages); **No
recap** writes nothing and counts nothing. Once the campaign's next session has started the
question is expired: it says so and its buttons come off.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui.decision_lines import with_outcome
from dmbot.ui.dmbot_commands import _bot, _tell

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this campaign any more. Nothing was changed."
EXPIRED = "Expired: a newer session started."
EXPIRED_TELL = "This question has expired: a newer session has started. Nothing was changed."
NOT_NOW = "No recap was written. Recaps are off for this campaign, or not in your plan."
NO_SCREEN = "Set up the DM screen first (in the server, press ⚙️ Manage DMbot), then try again."
WRITING = "Writing the recap."
ALREADY = "Already being written."
HELD = "Waiting for your answers about names."
NONE_WRITTEN = "No recap written."

_TEMPLATE = r"dmbot:recapask:(?P<act>go|skip):(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"


def ask_key(campaign_id: str, session_at: int) -> tuple[str, str, int]:
    """The item, for the other copies of its line and for expiring it."""
    return ("recapask", campaign_id, session_at)


class ShortRecapButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, act: str, campaign_id: str, session_at: int) -> None:
        custom_id = f"dmbot:recapask:{act}:{campaign_id}:{session_at}"
        # (the labels are written out here so the site's button-label test can find them)
        button: discord.ui.Button[discord.ui.View]
        if act == "go":
            button = discord.ui.Button(
                label="Write a recap", style=discord.ButtonStyle.primary, custom_id=custom_id
            )
        else:
            button = discord.ui.Button(
                label="No recap", style=discord.ButtonStyle.secondary, custom_id=custom_id
            )
        super().__init__(button)
        self.act, self.campaign_id, self.session_at = act, campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ShortRecapButton:
        return cls(match["act"], match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        # In the DM's private chat a press has no server (#3210): the database says where the
        # campaign is.
        guild_id = interaction.guild.id if interaction.guild is not None else None
        if guild_id is None:
            guild_id = await bot.campaigns.guild_of(self.campaign_id)
        campaign = (
            await bot.campaigns.get(guild_id, self.campaign_id) if guild_id is not None else None
        )
        if campaign is None:
            await _tell(interaction, GONE)
            return
        # Only a DM of this campaign, on every press.
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        # Answered at once: the checks below make several database calls and a post, which can
        # take longer than Discord waits for an answer.
        await interaction.response.defer()
        key = ask_key(self.campaign_id, self.session_at)
        message = interaction.message
        bot.decision_lines.remember_pressed(key, interaction)
        if await bot.recap_short_expired(campaign.guild_id, campaign.id, self.session_at):
            await self._close(interaction, EXPIRED)
            await _tell(interaction, EXPIRED_TELL)
            await bot.decision_lines.close(
                key, EXPIRED, pressed_id=message.id if message is not None else None
            )
            return
        if self.act == "go":
            # The DM screen, where the recap's own notes go; in a private chat this channel is the
            # chat, so it is the campaign's, not the press's.
            channel_id = campaign.dm_screen_channel_id or (
                interaction.channel_id if interaction.guild is not None else None
            )
            if channel_id is None:
                await _tell(interaction, NO_SCREEN)
                return
            result = await bot.recap_write_anyway(
                campaign.guild_id, campaign.id, self.session_at, channel_id
            )
            if result == "refused":
                await _tell(interaction, NOT_NOW)  # the buttons stay: the DM can try again
                return
            outcome = {"queued": WRITING, "held": HELD}.get(result, ALREADY)
        else:
            # Nothing is written and nothing is counted, so nothing is recorded either: two DMs
            # pressing "No recap" and "Write a recap" at the same moment end with a recap. The
            # DM can delete it; recording a "no" would need a stored row, for little gain.
            outcome = NONE_WRITTEN
        await self._close(interaction, outcome)
        await bot.decision_lines.close(
            key, outcome, pressed_id=message.id if message is not None else None
        )

    async def _close(self, interaction: discord.Interaction, outcome: str) -> None:
        message = interaction.message
        await interaction.edit_original_response(
            content=with_outcome(message.content, outcome) if message is not None else outcome,
            view=None,
            allowed_mentions=NO_PINGS,
        )


def ask_view(campaign_id: str, session_at: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("go", "skip"):
        view.add_item(ShortRecapButton(act, campaign_id, session_at))
    return view
