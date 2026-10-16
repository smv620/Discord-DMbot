"""The secret-said-aloud question on the DM screen (#1407): "A player said *Auril* this session,
and it's in the transcript. Keep it secret, or not any more?" with two buttons. Private to the
campaign's DM: the message is on the DM screen and only a DM of the campaign can press. The
buttons carry the campaign and the name's id, so they work after a restart.

"Not secret any more" clears the flag through the memory's own change path (a change like any
other, undoable from the name's card). "Keep it secret" changes nothing and closes the question;
the next session asks again if a player says it again.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui.dmbot_commands import NO_PINGS, _bot, _tell

KEPT = "👍 Kept secret. DMbot will ask again if a player says it in a later session."
OPENED = "✅ Not secret any more."
NOT_YOURS = "Only a DM of this campaign can answer that."
GONE = "That name isn't there any more, so nothing was changed."


def question_text(name: str) -> str:
    return (
        f"🤫 A player said **{discord.utils.escape_markdown(name)}** this session, and it's in "
        "the transcript. Keep it secret, or not any more?"
    )


class SecretAloudButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:secret:(?P<act>keep|open):(?P<campaign>[0-9a-f]{32}):(?P<alias>[0-9a-f]{32})",
):
    def __init__(self, act: str, campaign_id: str, alias_id: str) -> None:
        keep = act == "keep"
        super().__init__(
            discord.ui.Button(
                label="Keep it secret" if keep else "Not secret any more",
                style=discord.ButtonStyle.primary if keep else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:secret:{act}:{campaign_id}:{alias_id}",
            )
        )
        self.act, self.campaign_id, self.alias_id = act, campaign_id, alias_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SecretAloudButton:
        return cls(match["act"], match["campaign"], match["alias"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        # Only a DM of the campaign: a server manager who is not one cannot answer (the question
        # names a secret).
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        if self.act == "keep":
            await interaction.response.edit_message(
                content=KEPT, view=None, allowed_mentions=NO_PINGS
            )
            return
        memory = bot.memory
        if memory is None:
            await _tell(interaction, GONE)
            return
        try:
            await memory.update_alias(
                campaign.guild_id, campaign.id, self.alias_id, source=DM, secret=False
            )
        except MemoryRuleError as exc:
            await _tell(interaction, str(exc))
            return
        if bot.lookup is not None:
            bot.lookup.drop([campaign.id])
        await interaction.response.edit_message(
            content=OPENED, view=None, allowed_mentions=NO_PINGS
        )


def secret_aloud_view(campaign_id: str, alias_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(SecretAloudButton("keep", campaign_id, alias_id))
    view.add_item(SecretAloudButton("open", campaign_id, alias_id))
    return view
