"""🗑 Delete this campaign, in ⚙️ Settings → 🗂️ Campaign management (#1608; owner decision
2026-10-16, docs/PLAN.md, "Data,
ownership and deletion"). Only the game master whose plan the campaign uses (its owner) may
delete it (a campaign with no owner: any of its game masters, #2556); every other game master
is told whose it is. One warning, then the campaign's name
typed back, so a stray tap can never do it. Not while a session of it is running.

The delete is `CampaignStore.delete`, then the channels are tidied the way retention does it
(`retention_channels.retire_channels`), and the other game masters are told once, privately.
Everything is checked again on the typed confirmation, against the campaign read again: the
button and the warning may be minutes old. Logs carry ids only, never names.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CampaignError
from dmbot.logs import log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"

GONE = "That campaign isn't here any more."
FAILED = "That didn't work. Nothing was deleted. Try again in a moment."
ONLY_GMS = "Only this campaign's game masters can use its settings."
NOT_YOURS = "Only {who}, whose plan this campaign uses, can delete it. Ask them to do it."
PLAYING = "**{name}** is being played right now. Stop the session first, then try again."
WARNING = (
    "🗑 This deletes **{name}** for everyone: its story memory, notes, recaps, house rules, "
    "homebrew and transcripts. It can't be undone. Copies people already downloaded stay, "
    "and transcripts already posted in your channels stay there."
)
WARNING_NO_OWNER = "\n\nThis campaign has no owner, so any of its game masters may delete it."
WARNING_BACKUP = " To keep a copy, press 💾 Back up first."
WARNING_END = "\n\nSure? Press **Delete it**. You'll type the name to confirm."
KEPT = "OK, **{name}** is kept. Nothing was deleted."
TYPE_NAME = "Type the name to confirm"
WRONG_NAME = (
    "That doesn't match **{name}**, so nothing was deleted. "
    "Press **Delete this campaign** to try again."
)
DELETED = (
    "🗑 Deleted **{name}**. Its transcript and chat channels stay until a server admin removes them."
)
TOLD = "🗑 {who} deleted **{name}**."
NAME_LIMIT = 100  # a campaign name's longest length; the typed box holds as much


def holder_may(campaign: Campaign, user_id: int) -> bool:
    """The plan holder: the campaign's owner, and still one of its game masters. A campaign with
    no owner yet may be deleted by any of its game masters (Supervisor, #2556): any of them could
    press Take it on and then delete it, but taking it on uses a slot of their plan, which they may
    not have, and the campaign would be one nobody can remove."""
    if user_id not in campaign.dm_user_ids:
        return False
    return campaign.owner_user_id is None or campaign.owner_user_id == user_id


def same_name(typed: str, campaign: Campaign) -> bool:
    """The typed confirmation: the campaign's name, any case, extra spaces ignored."""
    return " ".join(typed.split()).casefold() == " ".join(campaign.name.split()).casefold()


async def _tell(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


async def refusal(client: Any, campaign: Campaign | None, user_id: int) -> str | None:
    """Why this person may not delete the campaign now, in plain words, or None. Read fresh
    every time it matters: on the button, and again on the typed name."""
    if campaign is None:
        return GONE
    if user_id not in campaign.dm_user_ids:
        return ONLY_GMS
    if not holder_may(campaign, user_id):
        owner = campaign.owner_user_id
        who = discord.utils.escape_markdown(client.name_of(campaign.guild_id, owner))
        return NOT_YOURS.format(who=who)
    if await client.is_campaign_playing(campaign.guild_id, campaign.id):
        return PLAYING.format(name=discord.utils.escape_markdown(campaign.name))
    return None


class DeleteButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:delcampaign:{_ID}",
):
    """🗑 Delete this campaign: the warning, for the plan holder only."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Delete this campaign",
                emoji="🗑",
                style=discord.ButtonStyle.secondary,
                row=2,
                custom_id=f"dmbot:delcampaign:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        # Answer Discord first (#1798): the warning reads the campaign, its owner and the plan.
        # (A button's defer is a quiet update; the follow-up below sets ephemeral itself.)
        await interaction.response.defer(ephemeral=True)
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            refused = await refusal(client, campaign, interaction.user.id)
            if refused is not None or campaign is None:
                await _tell(interaction, refused or GONE)
                return
            can_copy = (
                await client.plan_gate("copy", guild.id, campaign, interaction.user.id) is None
            )
        except Exception:
            log.exception("Couldn't open the delete warning")
            await _tell(interaction, FAILED)
            return
        text = WARNING.format(name=discord.utils.escape_markdown(campaign.name))
        await interaction.followup.send(
            text
            + (WARNING_NO_OWNER if campaign.owner_user_id is None else "")
            + (WARNING_BACKUP if can_copy else "")
            + WARNING_END,
            view=DeleteWarning(campaign.id, campaign.name, can_copy=can_copy),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class DeleteWarning(discord.ui.View):
    """The warning's buttons. Short-lived and private: an answer to one press."""

    def __init__(self, campaign_id: str, name: str, *, can_copy: bool) -> None:
        super().__init__(timeout=300)
        self.campaign_id, self.name = campaign_id, name
        if not can_copy:  # only where copies exist (paid campaigns)
            self.remove_item(self.backup)

    @discord.ui.button(label="Delete it", emoji="🗑", style=discord.ButtonStyle.danger)
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(ConfirmForm(self.campaign_id, self.name))

    @discord.ui.button(label="Back up first", emoji="💾", style=discord.ButtonStyle.secondary)
    async def backup(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        from dmbot.ui.dmbot_commands import send_backup

        await send_backup(interaction, self.campaign_id)

    @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=KEPT.format(name=discord.utils.escape_markdown(self.name)), view=None
        )


class ConfirmForm(discord.ui.Modal):
    """The campaign's name, typed back."""

    name_box: discord.ui.TextInput[ConfirmForm] = discord.ui.TextInput(
        label=TYPE_NAME[:45], max_length=NAME_LIMIT
    )

    def __init__(self, campaign_id: str, name: str = "") -> None:
        super().__init__(title="Delete this campaign?")
        self.campaign_id = campaign_id
        self.name_box.placeholder = name[:100] or None  # shown so it can be copied

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await delete_now(interaction, self.campaign_id, self.name_box.value)


async def delete_now(interaction: discord.Interaction, campaign_id: str, typed: str) -> bool:
    """The typed name arrived: check everything again, delete, tidy the channels, tell the
    other game masters. True only if the campaign was deleted."""
    client: Any = interaction.client
    guild = interaction.guild
    if guild is None:
        return False
    user_id = interaction.user.id
    # The session lock stops a session starting while the campaign goes (as a restore does).
    async with client.session_lock(guild.id):
        try:
            campaign = await client.campaigns.get(guild.id, campaign_id)
            refused = await refusal(client, campaign, user_id)
            if refused is not None or campaign is None:
                await _tell(interaction, refused or GONE)
                return False
            if not same_name(typed, campaign):
                await _tell(
                    interaction,
                    WRONG_NAME.format(name=discord.utils.escape_markdown(campaign.name)),
                )
                return False
            await client.campaigns.delete(guild.id, campaign.id, only_if_owner=user_id)
        except CampaignError as exc:
            await _tell(interaction, str(exc))
            return False
        except Exception:
            with log_context(guild_id=guild.id, campaign_id=campaign_id):
                log.exception("Couldn't delete a campaign")
            await _tell(interaction, FAILED)
            return False
    with log_context(guild_id=guild.id, campaign_id=campaign.id):
        log.info("A campaign was deleted by its plan holder")
    # After the delete went through, from the row as it was before it (the channel ids went
    # with the row). The campaign is gone either way: a failure here only leaves a channel.
    try:
        await client.retire_campaign_channels(campaign, int(time.time()), by_hand=True)
    except Exception:
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            log.exception("Couldn't tidy a deleted campaign's channels")
    name = discord.utils.escape_markdown(campaign.name)
    who = discord.utils.escape_markdown(client.name_of(guild.id, user_id))
    await _tell(interaction, DELETED.format(name=name))  # first: the messages below can be slow
    for other in sorted(campaign.dm_user_ids - {user_id}):
        with contextlib.suppress(Exception):  # (messages off, left Discord: nothing to do)
            await client.dm_user(other, TOLD.format(who=who, name=name))
    return True
