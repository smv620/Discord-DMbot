"""The DM said a secret name at the table (#2088): "You just said **<name>** out loud. Is it
revealed now?" with **Yes, players know now** and **No, keep it secret**.

Private to the campaign's DMs: only a DM of the campaign can press, checked again on every press.
Yes turns the secret off through the same change path as the name's card and the after-session
question (#1407, #1429): every entry with that spelling, as one change, undoable from the card.
No writes nothing and the name is not asked about again this session. DMbot never assumes a
reveal: a DM may say a secret name in an aside. The buttons carry the campaign and the name's id,
so they work after a restart.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.dm_screen import secret_posts
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.secret_aloud import (
    NO_CAMPAIGN,
    NO_MEMORY,
    NOT_YOURS,
    names_note,
    opened_text,
    settle,
)

_NAME = re.compile(r"\*\*(.+?)\*\*")
PRIVATE_NOTE = "\n(Sent to you alone, because your players can look at your DM screen.)"


def question_text(name: str, entries: int = 1, *, private: bool = False) -> str:
    shown = discord.utils.escape_markdown(name)
    after_name, covers = names_note(entries)
    return (
        f"🤫 You just said **{shown}**{after_name} out loud. Is it revealed now?{covers}\n"
        "**Yes, players know now:** DMbot may use it in answers to players.\n"
        "**No, keep it secret:** DMbot won't ask about it again this session."
        + (PRIVATE_NOTE if private else "")
    )


def kept_text() -> str:
    """The answer, without the name (#2097)."""
    return (
        "👍 It stays secret. You'll find it on the name's card. DMbot won't ask about it again "
        "this session."
    )


def name_in(interaction: discord.Interaction) -> str:
    content = getattr(getattr(interaction, "message", None), "content", "") or ""
    found = _NAME.search(content)
    return found.group(1) if found else "That name"


class RevealButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        r"dmbot:rv:(?P<act>y|n):(?P<campaign>[0-9a-f]{32}):(?P<alias>[0-9a-f]{32}):"
        r"(?P<guild>[0-9]{1,20})"
    ),
):
    """The server is on the id because the question may be a private message (no server there)
    and has to work after a restart: `dmbot:rv:<y|n>:<campaign>:<name>:<server>` is under 100."""

    def __init__(self, act: str, campaign_id: str, alias_id: str, guild_id: int) -> None:
        yes = act == "y"
        super().__init__(
            discord.ui.Button(
                label="Yes, players know now" if yes else "No, keep it secret",
                style=discord.ButtonStyle.secondary if yes else discord.ButtonStyle.primary,
                custom_id=f"dmbot:rv:{act}:{campaign_id}:{alias_id}:{guild_id}",
            )
        )
        self.act, self.campaign_id, self.alias_id = act, campaign_id, alias_id
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RevealButton:
        return cls(match["act"], match["campaign"], match["alias"], int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        # A press in a server must be in the server the question is about; in a private message
        # the server comes from the button. Either way the press has to come from a DM of it.
        here = interaction.guild.id if interaction.guild is not None else self.guild_id
        campaign = (
            await bot.campaigns.get(self.guild_id, self.campaign_id)
            if here == self.guild_id
            else None
        )
        if campaign is None:
            await _tell(interaction, NO_CAMPAIGN)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        if secret_posts.is_exposed(campaign.dm_screen_visibility, interaction.guild is not None):
            # The screen has opened up since this was asked: answer without writing the name back.
            await interaction.response.edit_message(
                content=secret_posts.HIDDEN, view=None, allowed_mentions=NO_PINGS
            )
            secret_posts.answered(interaction, self.campaign_id)  # (#2156)
            return
        name = name_in(interaction)
        if self.act == "n":
            await settle(bot, campaign, self.alias_id)
            await interaction.response.edit_message(
                content=kept_text(), view=None, allowed_mentions=NO_PINGS
            )
            secret_posts.answered(interaction, self.campaign_id)  # (#2156)
            return
        memory = bot.memory
        if memory is None:
            await _tell(interaction, NO_MEMORY)
            return
        try:
            written = await memory.make_name_not_secret(
                campaign.guild_id, campaign.id, self.alias_id, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, str(exc))
            return
        if bot.lookup is not None:
            bot.lookup.drop([campaign.id])
        await settle(bot, campaign, self.alias_id)
        await interaction.response.edit_message(
            content=opened_text(name, len(written.value)), view=None, allowed_mentions=NO_PINGS
        )
        secret_posts.answered(interaction, self.campaign_id)  # (#2156)


def reveal_view(campaign_id: str, alias_id: str, guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RevealButton("y", campaign_id, alias_id, guild_id))
    view.add_item(RevealButton("n", campaign_id, alias_id, guild_id))
    return view
