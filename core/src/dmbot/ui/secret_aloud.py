"""The secret-said-aloud question (#1407): "A player said *Auril* this session, and it's in the
transcript. Keep it secret, or not any more?" with two buttons. It is on the DM screen while the
screen is private, and a private message to each DM when players may look at the screen (#2097).
Only a DM of the campaign can press, checked on every press. The buttons carry the campaign, the
name's id and the server, so they work after a restart and in a private message.

"Not secret any more" clears the flag through the memory's own change path (a change like any
other, undoable from the name's card), on every entry that shares the spelling at once, as one
change (#1429), so one undo puts them all back. "Keep it secret" changes nothing and closes the
question; the next session asks again if a player says it again.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.dm_screen import secret_posts
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

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


PRIVATE_NOTE = "\n(Sent to you alone, because your players can look at your DM screen.)"


def question_text(
    name: str, entries: int = 1, *, private: bool = False, campaign_name: str = ""
) -> str:
    shown = discord.utils.escape_markdown(name)
    after_name, covers = names_note(entries)
    where = (
        f" (campaign {discord.utils.escape_markdown(campaign_name)})"
        if private and campaign_name
        else ""
    )
    return (
        f"🤫 A player said **{shown}**{after_name} at the table{where}. You'd marked it secret, "
        f"and the transcript (which anyone in this server can read) now has it.{covers}\n"
        "**Keep it secret:** DMbot still won't use it when it answers players.\n"
        "**Not secret any more:** DMbot may use it in answers to players."
        + (PRIVATE_NOTE if private else "")
    )


def more_text(count: int) -> str:
    what = "name was" if count == 1 else "names were"
    return f"🤫 {count} more secret {what} said. DMbot will ask about them next session."


def kept_text() -> str:
    """The answer, without the name: an answered question can stay on a screen players may look
    at (#2097)."""
    return (
        "👍 It stays secret. You'll find it on the name's card. DMbot will ask again if a "
        "player says it in a later session."
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


async def settle(bot: Any, campaign: Any, alias_id: str) -> None:
    """The DM answered about this name, so the exposure the session recorded for it is settled and
    the summary will not ask again (#2410). Nothing here changes what the players see."""
    exposures = getattr(bot, "exposures", None)
    if exposures is None:
        return
    try:
        await exposures.settle_alias(campaign.guild_id, campaign.id, alias_id)
    except Exception as exc:  # bookkeeping must never cost the DM their answer
        # (the error's text can carry a name, so only its type is logged)
        log.warning("Couldn't settle a secret exposure: %s", type(exc).__name__)


def name_in(interaction: discord.Interaction) -> str:
    """The name the question was about, read back from the message being answered (already
    escaped there), or a plain word if it can't be read."""
    content = getattr(getattr(interaction, "message", None), "content", "") or ""
    found = _NAME.search(content)
    return found.group(1) if found else "That name"


class SecretAloudButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        # Sent before the server was on the id (the screen only): the old form, still answered.
        r"dmbot:secret:(?P<act>keep|open):(?P<campaign>[0-9a-f]{32}):(?P<alias>[0-9a-f]{32})"
        # With the server, so a private message can be answered. The short prefix keeps it
        # under Discord's 100 characters even with a 19-digit server id.
        r"|dmbot:sa:(?P<act2>k|o):(?P<campaign2>[0-9a-f]{32}):(?P<alias2>[0-9a-f]{32}):"
        r"(?P<guild>[0-9]{1,20})"
    ),
):
    def __init__(
        self, act: str, campaign_id: str, alias_id: str, guild_id: int | None = None
    ) -> None:
        keep = act == "keep"
        super().__init__(
            discord.ui.Button(
                label="Keep it secret" if keep else "Not secret any more",
                style=discord.ButtonStyle.primary if keep else discord.ButtonStyle.secondary,
                custom_id=(
                    f"dmbot:secret:{act}:{campaign_id}:{alias_id}"
                    if guild_id is None
                    else f"dmbot:sa:{act[0]}:{campaign_id}:{alias_id}:{guild_id}"
                ),
            )
        )
        self.act, self.campaign_id, self.alias_id = act, campaign_id, alias_id
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SecretAloudButton:
        if match["guild"] is not None:
            act = "keep" if match["act2"] == "k" else "open"
            return cls(act, match["campaign2"], match["alias2"], int(match["guild"]))
        return cls(match["act"], match["campaign"], match["alias"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        # A press in a server must be in the server the question is about; in a private message
        # the server comes from the button (an older button has none: it works on the screen only).
        here = interaction.guild.id if interaction.guild is not None else self.guild_id
        about = self.guild_id if self.guild_id is not None else here
        campaign = (
            await bot.campaigns.get(about, self.campaign_id)
            if here is not None and here == about
            else None
        )
        # Only a DM of the campaign: a server manager who is not one cannot answer (the question
        # names a secret).
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
        if self.act == "keep":
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


def secret_aloud_view(
    campaign_id: str, alias_id: str, guild_id: int | None = None
) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(SecretAloudButton("keep", campaign_id, alias_id, guild_id))
    view.add_item(SecretAloudButton("open", campaign_id, alias_id, guild_id))
    return view
