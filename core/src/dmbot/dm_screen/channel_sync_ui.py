"""⚙️ Manage DMbot → 🔒 Who can see this campaign's channels (#3891): the two choices, and a
private list of people a DM adds by hand. Words are in plain language and counts only.

The choice is saved, then `channel_sync.reconcile` puts the DM Bot and transcript channels in
step at once (the hourly sweep heals anything missed). A missing permission is said in plain words
with its name, and nothing is half applied. Only the campaign's DMs, or a server manager, may use
it, checked on every press. The picker is an ordinary private message with a timeout; its
selections are checked again when made.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CHANNELS_EVERYONE, CHANNELS_PEOPLE
from dmbot.dm_screen import channel_people as cp
from dmbot.dm_screen import channel_sync
from dmbot.dm_screen.buttons import may_change_screen
from dmbot.logs import log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
MAX_BY_HAND = 25  # one Remove list holds this many (Discord's limit for a select)

CHOICES = {
    CHANNELS_PEOPLE: "Campaign people only",
    CHANNELS_EVERYONE: "Everyone in the server",
}
EVERYONE_HELP = "Everyone in the server can see its channels."
FAILED = "Sorry, that didn't save. Please try again."
NOT_A_PERSON = "Bots can't be added. Pick a person."
LIST_FULL = f"The list is full ({MAX_BY_HAND} people). Remove someone first."
NOBODY = "Nobody added yet."
ADD_PLACEHOLDER = "Add a person"
REMOVE_PLACEHOLDER = "Remove a person"
BACK_TO = "Tap a choice to change it."
ROLE_EXPOSES = (
    "\n⚠️ A role in this server still lets its members see these channels. "
    "Ask a server admin to take it off the channels' permissions."
)


def channels_text(campaign: Campaign) -> str:
    """The card: what is chosen now, in the words the choice uses."""
    on = campaign.channels_visibility
    help_line = cp.HELP if on == CHANNELS_PEOPLE else EVERYONE_HELP
    return (
        f"🔒 **Who can see {discord.utils.escape_markdown(campaign.name)}'s channels**\n"
        f"{help_line}\n"
        "This covers the DM Bot and transcript channels. The DM screen has its own setting. "
        f"{BACK_TO}"
    )


def channels_view(campaign: Campaign) -> discord.ui.View:
    """The card's buttons: the two choices (the saved one marked), 👥 Who can see, and Back."""
    from dmbot.dm_screen.settings import BackToSettingsButton  # (it imports this module)

    view = discord.ui.View(timeout=None)
    for choice in CHOICES:
        view.add_item(
            ChannelsChoiceButton(
                campaign.id, choice, current=choice == campaign.channels_visibility
            )
        )
    view.add_item(PeopleButton(campaign.id))
    view.add_item(BackToSettingsButton(campaign.id))
    return view


async def _problem_line(
    guild: discord.Guild, campaign: Campaign, interaction: discord.Interaction
) -> str:
    """Put the channels in step with the saved choice; the plain cause on a line of its own when
    that could not be done (empty when it was)."""
    bot: Any = interaction.client  # (DMBot; the screens reach its stores this way)
    problem = await channel_sync.reconcile(guild, campaign, bot.campaigns, bot.memory)
    if problem:
        return (
            f"\n⚠️ Saved, but the channels haven't changed yet. {problem} "
            "Fix that, then tap the choice again."
        )
    if channel_sync.exposed_roles(guild, campaign):
        return ROLE_EXPOSES
    return ""


class ChannelsMenuButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:chmenu:{_ID}",
):
    """🔒 Who can see this campaign's channels: opens the card."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Who can see the channels",
                emoji="🔒",
                style=discord.ButtonStyle.secondary,
                row=3,
                custom_id=f"dmbot:chmenu:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ChannelsMenuButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        allowed = await may_change_screen(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign = allowed[0]
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.edit_message(
                content=channels_text(campaign),
                view=channels_view(campaign),
                allowed_mentions=NO_PINGS,
            )


class ChannelsChoiceButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:chset:{_ID}:(?P<choice>people|everyone)",
):
    """One of the two choices; the saved one is drawn as pressed."""

    def __init__(self, campaign_id: str, choice: str, *, current: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=CHOICES[choice] + (" ✓" if current else ""),
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:chset:{campaign_id}:{choice}",
            )
        )
        self.campaign_id = campaign_id
        self.choice = choice

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ChannelsChoiceButton:
        return cls(match["campaign"], match["choice"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        allowed = await may_change_screen(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign, guild, store = allowed
        await interaction.response.defer()
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            try:
                saved = await store.set_channels_visibility(guild.id, campaign.id, self.choice)
            except Exception:
                log.exception("Couldn't change who sees the channels")
                await interaction.followup.send(FAILED, ephemeral=True)
                return
            note = await _problem_line(guild, saved, interaction)
            log.info("Who sees the channels set to %s", self.choice)
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=channels_text(saved) + note,
                view=channels_view(saved),
                allowed_mentions=NO_PINGS,
            )


class PeopleButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:chpeople:{_ID}",
):
    """👥 Who can see: a private list to add a person by hand or take one off."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Add or remove people",
                emoji="👥",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:chpeople:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PeopleButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        allowed = await may_change_screen(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign, guild, store = allowed
        await interaction.response.defer(ephemeral=True)
        by_hand = await store.channel_people_by_hand(guild.id, campaign.id)
        names = await _names(guild, by_hand)
        await interaction.followup.send(
            people_text(names),
            view=PeopleView(campaign.id, names),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


async def _names(guild: discord.Guild, ids: list[int]) -> dict[int, str]:
    """Display names for the list. The bot has no members intent, so the cache rarely holds a
    member: ask Discord for each one. Names are shown only, never stored."""
    names: dict[int, str] = {}
    for user_id in ids:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        names[user_id] = member.display_name if member is not None else "Someone who left"
    return names


def people_text(names: dict[int, str]) -> str:
    """Who was added by hand (names only; the list the DM built)."""
    lines = [f"- {discord.utils.escape_markdown(name)}" for name in names.values()] or [NOBODY]
    return (
        "👥 **Who can see this campaign's channels**\n"
        "These people always see the channels: the DMs, the players who have a character here, "
        "and anyone recorded at a session.\nYou added:\n" + "\n".join(lines)
    )


class PeopleView(discord.ui.View):
    """An Add picker and, when anyone was added, a Remove picker. Both are checked again when
    used: the DM may have lost the right, or the list may have changed, since it was opened."""

    def __init__(self, campaign_id: str, names: dict[int, str]) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign_id
        self.add_pick: discord.ui.UserSelect[PeopleView] = discord.ui.UserSelect(
            placeholder=ADD_PLACEHOLDER, min_values=1, max_values=1
        )
        self.add_pick.callback = self._added  # type: ignore[method-assign]
        self.add_item(self.add_pick)
        if names:
            self.remove_pick: discord.ui.Select[PeopleView] = discord.ui.Select(
                placeholder=REMOVE_PLACEHOLDER,
                options=[
                    discord.SelectOption(label=name[:100], value=str(i))
                    for i, name in list(names.items())[:MAX_BY_HAND]
                ],
            )
            self.remove_pick.callback = self._removed  # type: ignore[method-assign]
            self.add_item(self.remove_pick)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Changing who sees the channels failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(FAILED, ephemeral=True)
            else:
                await interaction.response.send_message(FAILED, ephemeral=True)

    async def _finish(
        self, interaction: discord.Interaction, guild: discord.Guild, text: str
    ) -> None:
        """Put the channels in step, then show the list as it is now with what happened."""
        store = interaction.client.campaigns  # type: ignore[attr-defined]
        campaign = await store.get(guild.id, self.campaign_id)
        if campaign is None:
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        note = await _problem_line(guild, campaign, interaction)
        names = await _names(guild, await store.channel_people_by_hand(guild.id, campaign.id))
        self.stop()
        await interaction.edit_original_response(
            content=f"{text}{note}\n\n{people_text(names)}",
            view=PeopleView(campaign.id, names),
            allowed_mentions=NO_PINGS,
        )

    async def _added(self, interaction: discord.Interaction) -> None:
        allowed = await may_change_screen(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign, guild, store = allowed
        person = self.add_pick.values[0]
        if person.bot:
            await interaction.response.send_message(NOT_A_PERSON, ephemeral=True)
            return
        by_hand = await store.channel_people_by_hand(guild.id, campaign.id)
        if len(by_hand) >= MAX_BY_HAND and person.id not in by_hand:
            await interaction.response.send_message(LIST_FULL, ephemeral=True)
            return
        await interaction.response.defer()
        name = discord.utils.escape_markdown(person.display_name or person.name)
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            await store.add_channel_person(
                guild.id, campaign.id, person.id, interaction.user.id, int(time.time())
            )
            log.info("A person was added by hand to those who see the channels")
        await self._finish(
            interaction, guild, f"Added {name}. They can see this campaign's channels."
        )

    async def _removed(self, interaction: discord.Interaction) -> None:
        allowed = await may_change_screen(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign, guild, store = allowed
        user_id = int(self.remove_pick.values[0])
        await interaction.response.defer()
        name = discord.utils.escape_markdown((await _names(guild, [user_id]))[user_id])
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            await store.remove_channel_person(guild.id, campaign.id, user_id)
            log.info("A person was taken off those who see the channels")
        people = await channel_sync.campaign_people(
            store,
            getattr(interaction.client, "memory", None),
            campaign,
            guild.me.id if guild.me else 0,
        )
        still = (
            f" {name} still sees the channels as a DM, a player or someone recorded here."
            if user_id in people
            else ""
        )
        await self._finish(interaction, guild, f"Removed {name} from the list.{still}")
