"""Peek and Hide buttons. They keep working after a restart (custom IDs carry the campaign).

Register them with `bot.add_dynamic_items(PeekButton, HideButton, VisibilityButton)` in
`setup_hook`.
Peek toggles: a player who is already peeking is offered Hide instead.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import (
    DMScreenError,
    campaign_lock,
    current_overwrites,
    fresh_screen,
    held_permissions,
    setup_dm_screen,
)
from dmbot.dm_screen.rules import READ_ONLY, Target, is_peeker, missing_required, restrict

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
CONFIRM_TIMEOUT_S = 120
_ID = r"(?P<campaign>[0-9a-f]{32})"


def _store(interaction: discord.Interaction) -> CampaignStore | None:
    store = getattr(interaction.client, "campaigns", None)
    return store if isinstance(store, CampaignStore) else None


async def _campaign(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    store = _store(interaction)
    guild = interaction.guild
    # Scoped to the server the button was pressed in: a campaign ID from another
    # server finds nothing.
    return await store.get(guild.id, campaign_id) if store and guild else None


async def _load(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Campaign, discord.TextChannel, discord.Member] | None:
    """Campaign, fresh screen channel and member, or None after telling the user why."""
    campaign = await _campaign(interaction, campaign_id)
    guild = interaction.guild
    member = interaction.user
    if campaign is None or guild is None or not isinstance(member, discord.Member):
        await interaction.response.send_message(messages.CAMPAIGN_GONE, ephemeral=True)
        return None
    channel = await fresh_screen(guild, campaign)
    if channel is None:
        await interaction.response.send_message(messages.SCREEN_GONE, ephemeral=True)
        return None
    return campaign, channel, member


def _peeking(channel: discord.TextChannel, member: discord.Member) -> bool:
    return is_peeker(current_overwrites(channel).get(Target("member", member.id), {}))


async def _tell_dm_about_failure(channel: discord.TextChannel) -> None:
    me = channel.guild.me
    missing = missing_required(held_permissions(me)) if me else []
    text = messages.needs_permissions(missing) if missing else messages.FORBIDDEN_HERE
    with contextlib.suppress(discord.HTTPException):
        await channel.send(text, allowed_mentions=NO_PINGS)


class PeekButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:peek:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=messages.PEEK_LABEL,
                emoji="👀",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:peek:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PeekButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        loaded = await _load(interaction, self.campaign_id)
        if loaded is None:
            return
        campaign, channel, member = loaded
        reply = interaction.response.send_message
        if member.id in campaign.dm_user_ids:
            await reply(messages.peek_is_dm(channel.mention), ephemeral=True)
        elif campaign.dm_screen_visibility == "private":
            await reply(messages.PEEK_PRIVATE, ephemeral=True)
        elif campaign.dm_screen_visibility == "open":
            await reply(messages.peek_open(channel.mention), ephemeral=True)
        elif _peeking(channel, member):
            await reply(messages.ALREADY_PEEKING, view=hide_view(campaign.id), ephemeral=True)
        else:
            confirm = ConfirmPeek(self.campaign_id, interaction)
            await reply(messages.PEEK_WARNING, view=confirm, ephemeral=True)


class ConfirmPeek(discord.ui.View):
    """The spoiler warning's [Yes, show me] [Cancel]. Shown only to the player who asked."""

    def __init__(self, campaign_id: str, origin: discord.Interaction) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.origin = origin  # to mark the warning expired on timeout

    async def on_timeout(self) -> None:
        with contextlib.suppress(discord.HTTPException):
            await self.origin.edit_original_response(content=messages.PEEK_EXPIRED, view=None)

    @discord.ui.button(label=messages.YES_LABEL, style=discord.ButtonStyle.danger)
    async def yes(
        self, interaction: discord.Interaction, button: discord.ui.Button[ConfirmPeek]
    ) -> None:
        self.stop()
        loaded = await _load(interaction, self.campaign_id)
        if loaded is None:
            return
        _, channel, member = loaded
        edit = interaction.response.edit_message
        async with campaign_lock(self.campaign_id):
            # Re-read under the lock: the DM may have changed the setting meanwhile.
            campaign = await _campaign(interaction, self.campaign_id)
            if campaign is None:
                await edit(content=messages.CAMPAIGN_GONE, view=None)
                return
            if campaign.dm_screen_visibility == "open":
                await edit(content=messages.peek_open(channel.mention), view=None)
                return
            if campaign.dm_screen_visibility != "peek":
                await edit(content=messages.PEEK_PRIVATE, view=None)
                return
            me = channel.guild.me
            held = held_permissions(me) if me else set()
            try:
                await channel.set_permissions(
                    member,
                    overwrite=discord.PermissionOverwrite(**restrict(READ_ONLY, held)),
                    reason=f"DMbot: {member} chose to peek",
                )
            except discord.HTTPException:
                log.warning("Could not grant peek access in channel %s", channel.id)
                await edit(content=messages.PLAYER_FAILED, view=None)
                await _tell_dm_about_failure(channel)
                return
        await edit(content=messages.peek_done(channel.mention), view=hide_view(self.campaign_id))
        with contextlib.suppress(discord.HTTPException):
            await channel.send(messages.peeked_note(member.display_name), allowed_mentions=NO_PINGS)

    @discord.ui.button(label=messages.CANCEL_LABEL, style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button[ConfirmPeek]
    ) -> None:
        self.stop()
        await interaction.response.edit_message(content=messages.PEEK_CANCELLED, view=None)


class HideButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:hide:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=messages.HIDE_LABEL,
                emoji="🙈",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:hide:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HideButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        loaded = await _load(interaction, self.campaign_id)
        if loaded is None:
            return
        campaign, channel, member = loaded
        reply = interaction.response.send_message
        if member.id in campaign.dm_user_ids:
            await reply(messages.HIDE_IS_DM, ephemeral=True)
            return
        if campaign.dm_screen_visibility == "open":
            await reply(messages.HIDE_OPEN, ephemeral=True)
            return
        async with campaign_lock(self.campaign_id):
            if not _peeking(channel, member):
                await reply(messages.HIDE_NOT_PEEKING, ephemeral=True)
                return
            try:
                await channel.set_permissions(
                    member, overwrite=None, reason=f"DMbot: {member} stopped peeking"
                )
            except discord.HTTPException:
                log.warning("Could not remove peek access in channel %s", channel.id)
                await reply(messages.PLAYER_FAILED, ephemeral=True)
                await _tell_dm_about_failure(channel)
                return
        await reply(messages.HIDE_DONE, ephemeral=True)
        with contextlib.suppress(discord.HTTPException):
            await channel.send(
                messages.unpeeked_note(member.display_name), allowed_mentions=NO_PINGS
            )


class VisibilityButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:vis:{_ID}:(?P<visibility>private|peek|open)",
):
    """On the DM screen's help card: the DM changes who can see the screen."""

    def __init__(self, campaign_id: str, visibility: str, *, current: bool = False) -> None:
        emoji, label = messages.VISIBILITY_BUTTONS[visibility]
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                disabled=current,
                custom_id=f"dmbot:vis:{campaign_id}:{visibility}",
            )
        )
        self.campaign_id = campaign_id
        self.visibility = visibility

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> VisibilityButton:
        return cls(match["campaign"], match["visibility"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign(interaction, self.campaign_id)
        guild = interaction.guild
        member = interaction.user
        store = _store(interaction)
        if campaign is None or guild is None or store is None:
            await interaction.response.send_message(messages.CAMPAIGN_GONE, ephemeral=True)
            return
        if not isinstance(member, discord.Member) or not (
            member.id in campaign.dm_user_ids or member.guild_permissions.manage_guild
        ):
            await interaction.response.send_message(messages.NOT_THE_DM, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        was = campaign.dm_screen_visibility
        try:
            # Saved and applied together under the campaign's lock.
            result = await setup_dm_screen(guild, campaign.id, store, visibility=self.visibility)
        except DMScreenError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            # Never leave the DM on "thinking…".
            log.exception("Changing DM-screen visibility failed")
            await interaction.followup.send(messages.SOMETHING_WENT_WRONG, ephemeral=True)
            return
        campaign = result.campaign
        reply = messages.visibility_changed(self.visibility, was=was)
        if result.warning:
            reply += "\n" + result.warning
            with contextlib.suppress(discord.HTTPException):
                await result.channel.send(result.warning, allowed_mentions=NO_PINGS)
        await interaction.followup.send(reply, ephemeral=True, allowed_mentions=NO_PINGS)
        # Let a live session catch up (for example, give players the Peek button).
        hook = getattr(interaction.client, "after_screen_change", None)
        if hook is not None:
            await hook(campaign, result.channel)


def peek_view(campaign_id: str) -> discord.ui.View:
    """A message view with the Peek button, for places players can see."""
    view = discord.ui.View(timeout=None)
    view.add_item(PeekButton(campaign_id))
    return view


def hide_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(HideButton(campaign_id))
    return view


def card_view(campaign: Campaign) -> discord.ui.View:
    """The help card's buttons: who can see the screen (for the DM), and Hide (peekers)."""
    view = discord.ui.View(timeout=None)
    for visibility in messages.VISIBILITY_BUTTONS:
        current = visibility == campaign.dm_screen_visibility
        view.add_item(VisibilityButton(campaign.id, visibility, current=current))
    if campaign.dm_screen_visibility == "peek":
        view.add_item(HideButton(campaign.id))
    return view


class StopListeningButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:stop:{_ID}",
):
    """On the DM screen's "Listening" message (#108): the DM stops the session with one
    press, like `/dmbot stop`. Only this campaign's DMs or a server manager; players are
    told how to stop recording themselves instead. Works after a restart."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=messages.STOP_LISTENING_LABEL,
                emoji="⏹",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:stop:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopListeningButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        guild = interaction.guild
        bot: Any = interaction.client
        member = interaction.user
        if guild is None or not hasattr(bot, "stop_session"):
            await interaction.response.send_message(messages.CAMPAIGN_GONE, ephemeral=True)
            return
        if bot.active_campaign_id(guild.id) != self.campaign_id:
            # An old message: this campaign isn't the one being listened to now.
            await interaction.response.send_message(messages.NOT_LISTENING_NOW, ephemeral=True)
            with contextlib.suppress(discord.HTTPException):
                if interaction.message is not None:
                    await interaction.message.edit(view=None)
            return
        manager = isinstance(member, discord.Member) and member.guild_permissions.manage_guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply: str = await bot.stop_session(guild.id, member.id, manager)
        await interaction.followup.send(reply, ephemeral=True, allowed_mentions=NO_PINGS)


def stop_listening_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(StopListeningButton(campaign_id))
    return view
