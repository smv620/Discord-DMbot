"""🔒 Make private and 🗑️ Delete under the link DMbot posts for a kept picture (#3274).

A picture someone who agreed posted in the table's chat is kept with the campaign and shared by
link. The person who posted it, or one of the campaign's DMs, can take that back right where the
link is: **Make private** (the link stops working for people outside the campaign) or **Delete**
(the picture and its files go; one confirmation, because it cannot be undone). Marking a picture
permanent is a DM's choice and stays on the website.

Both buttons carry only the picture's number; the server comes from where they are pressed, so a
made-up number finds nothing. The work is the website's own path (`PictureActions.apply`), which
re-checks the poster or DM from the database on every press, so the rules cannot differ between
Discord and the website. Taking a picture back never needs the poster's yes to still stand.
The link line and its buttons stay in the chat after a choice (they then answer "That picture
isn't kept any more."); the line is a reply to the poster's message and goes with it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.pictures.actions import GONE
from dmbot.pictures.actions import NOT_YOURS as _NOT_YOURS
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NOT_HERE = "I can't do that here. Try it in your campaign's server."
NOT_YOURS = f"{_NOT_YOURS} Ask them if it should go."
OFF = "Kept pictures are off right now. Ask your DM."
# What the person sees after each choice. The link line stays in the chat, so each says what the
# link does now; only Make private says where to undo it (the website's Pictures page).
SAID = {
    "private": (
        "Done. The link now works only for people in this campaign. "
        "To share it again, use the Pictures page on the website."
    ),
    "delete": "Deleted. The link no longer works.",
}
SURE = "Delete this picture for good? The link stops working and the file is removed."
FAILED = "I couldn't do that just now. Try again in a minute."

_PRIVATE = r"dmbot:picpriv:(?P<id>[0-9]{1,18})"
_DELETE = r"dmbot:picdel:(?P<id>[0-9]{1,18})"
_YES = r"dmbot:picdely:(?P<id>[0-9]{1,18})"


async def _may(bot: Any, guild_id: int, picture: Any, user_id: int) -> bool:
    """The poster or one of the campaign's DMs, read now (not from the button)."""
    campaign = await bot.campaigns.get(guild_id, picture.campaign_id)
    return campaign is not None and (
        user_id == picture.poster_id or user_id in campaign.dm_user_ids
    )


async def _do(interaction: discord.Interaction, picture_id: int, action: str) -> None:
    """Apply one choice as the person who pressed, and tell them the plain-words outcome."""
    bot, guild = _bot(interaction), interaction.guild
    if guild is None:
        await _tell(interaction, NOT_HERE)
        return
    actions, store = bot.picture_actions, bot.picture_store
    if actions is None or store is None:
        await _tell(interaction, OFF)
        return
    try:
        picture = await store.by_id(guild.id, picture_id)
        if picture is None or picture.gone:
            await _tell(interaction, GONE)
            return
        if not await _may(bot, guild.id, picture, interaction.user.id):
            await _tell(interaction, NOT_YOURS)  # (the website's path checks again before it acts)
            return
        outcome = await actions.apply(
            guild.id, picture.campaign_id, interaction.user.id, picture_id, action
        )
    except Exception:
        log.exception("A kept picture's button failed")
        await _tell(interaction, FAILED)
        return
    # (a refusal keeps the website's own plain words)
    await _tell(interaction, SAID[action] if outcome.state == "done" else outcome.note)


class PrivateButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_PRIVATE):
    """🔒 Make private under the link."""

    def __init__(self, picture_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="🔒 Make private",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:picpriv:{picture_id}",
            )
        )
        self.picture_id = picture_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PrivateButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer(ephemeral=True, thinking=False)
        await _do(interaction, self.picture_id, "private")  # (poster or DM, checked there)


class DeleteButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_DELETE):
    """🗑️ Delete under the link: asks once, to the person who pressed only."""

    def __init__(self, picture_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="🗑️ Delete",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:picdel:{picture_id}",
            )
        )
        self.picture_id = picture_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        """The question goes only to the person who pressed. A stranger is told no here, so they
        are not led to a second button that would refuse them anyway."""
        await interaction.response.defer(ephemeral=True, thinking=False)
        bot, guild = _bot(interaction), interaction.guild
        store = bot.picture_store
        if guild is None or store is None:
            await _tell(interaction, NOT_HERE if guild is None else OFF)
            return
        try:
            picture = await store.by_id(guild.id, self.picture_id)
            if picture is None or picture.gone:
                await _tell(interaction, GONE)
                return
            if not await _may(bot, guild.id, picture, interaction.user.id):
                await _tell(interaction, NOT_YOURS)  # (on every press)
                return
        except Exception:
            log.exception("A kept picture's delete question failed")
            await _tell(interaction, FAILED)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(DeleteYesButton(self.picture_id))
        await _tell(interaction, SURE, view=view)


class DeleteYesButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_YES):
    """The one confirmation. It checks the person again: the question could be forwarded."""

    def __init__(self, picture_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Yes, delete it",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:picdely:{picture_id}",
            )
        )
        self.picture_id = picture_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteYesButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer(ephemeral=True, thinking=False)
        await _do(interaction, self.picture_id, "delete")  # (poster or DM, checked there)


def link_view(picture_id: int) -> discord.ui.View:
    """The buttons under the 🖼️ link line."""
    view = discord.ui.View(timeout=None)
    view.add_item(PrivateButton(picture_id))
    view.add_item(DeleteButton(picture_id))
    return view
