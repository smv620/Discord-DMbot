"""The secret-said-aloud question on the DM screen (#1407): "A player said *Auril* this session,
and it's in the transcript. Keep it secret, or not any more?" with two buttons. Private to the
campaign's DM: the message is on the DM screen and only a DM of the campaign can press. The
buttons carry the campaign and the name's id, so they work after a restart.

"Not secret any more" clears the flag through the memory's own change path (a change like any
other, undoable from the name's card), on every entry that shares the spelling at once, as one
change (#1429), so one undo puts them all back. "Keep it secret" changes nothing and closes the
question; the next session asks again if a player says it again.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui.dmbot_commands import NO_PINGS, _bot, _tell

NOT_YOURS = "Only this campaign's DM can answer this. Ask your DM."
NO_CAMPAIGN = "I can't find this campaign any more, so nothing was changed."
NO_MEMORY = "DMbot's memory isn't available right now. Try again in a minute."
_NAME = re.compile(r"\*\*(.+?)\*\*")


def names_note(entries: int) -> tuple[str, str]:
    """When DMbot's memory holds the same secret name in more than one entry (#1429), the question
    says so after the name, "(two names)" as the owner asked, and adds that one answer covers every
    one. Returns (the words after the name, the sentence after the question)."""
    if entries <= 1:
        return "", ""
    if entries == 2:
        return " (two names)", "\nDMbot has it twice. Your answer covers both."
    return (
        f" ({entries} names)",
        f"\nDMbot has it {entries} times. Your answer covers all {entries}.",
    )


def question_text(name: str, entries: int = 1) -> str:
    shown = discord.utils.escape_markdown(name)
    after_name, covers = names_note(entries)
    return (
        f"🤫 A player said **{shown}**{after_name} at the table. You'd marked it secret, "
        f"and the transcript (which anyone in this server can read) now has it.{covers}\n"
        "**Keep it secret:** DMbot still won't use it when it answers players.\n"
        "**Not secret any more:** DMbot may use it in answers to players."
    )


def more_text(count: int) -> str:
    what = "name was" if count == 1 else "names were"
    return f"🤫 {count} more secret {what} said. DMbot will ask about them next session."


def kept_text(name: str) -> str:
    return (
        f"👍 **{name}** stays secret. DMbot will ask again if a player says it in a later session."
    )


def opened_text(name: str, entries: int = 1) -> str:
    if entries > 1:
        covered = "both" if entries == 2 else f"all {entries}"
        return (
            f"✅ **{name}** is no longer secret. That covers {covered} copies. DMbot may now use "
            f"it in answers to players. To undo it, use the name's card: it puts {covered} back."
        )
    return (
        f"✅ **{name}** is no longer secret. DMbot may now use it in answers to players. "
        "You can make it secret again from the name's card."
    )


def name_in(interaction: discord.Interaction) -> str:
    """The name the question was about, read back from the message being answered (already
    escaped there), or a plain word if it can't be read."""
    content = getattr(getattr(interaction, "message", None), "content", "") or ""
    found = _NAME.search(content)
    return found.group(1) if found else "That name"


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
        if campaign is None:
            await _tell(interaction, NO_CAMPAIGN)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        name = name_in(interaction)
        if self.act == "keep":
            await interaction.response.edit_message(
                content=kept_text(name), view=None, allowed_mentions=NO_PINGS
            )
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
        await interaction.response.edit_message(
            content=opened_text(name, len(written.value)), view=None, allowed_mentions=NO_PINGS
        )


def secret_aloud_view(campaign_id: str, alias_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(SecretAloudButton("keep", campaign_id, alias_id))
    view.add_item(SecretAloudButton("open", campaign_id, alias_id))
    return view
