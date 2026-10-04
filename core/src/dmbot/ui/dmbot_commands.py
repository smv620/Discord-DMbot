"""`/dmbot start · stop · help · backup · restore`: buttons, menus, and forms.

Every step after the command is a button, a menu, or a short form (CLAUDE.md,
"Simple enough for a child"). All replies are private to the person who ran the command,
and every handler re-checks permissions, since a menu can be pressed minutes later.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

import discord
from discord import app_commands

from dmbot.campaigns import DEFAULT_DM_SCREEN_VISIBILITY, Campaign, CampaignError
from dmbot.campaigns.models import DEFAULT_FALLBACK, DEFAULT_TARGET, clean_name, name_key
from dmbot.campaigns.store import MAX_BACKUP_BYTES, decode_backup, encode_backup
from dmbot.logs import set_log_context
from dmbot.ui import logic

if TYPE_CHECKING:
    from dmbot.bot import DMBot

VIEW_TIMEOUT_S = 600  # under Discord's 15-minute limit for editing a reply
NO_PINGS = discord.AllowedMentions.none()
VOICE_TYPES = [discord.ChannelType.voice, discord.ChannelType.stage_voice]
TIMED_OUT = "This menu timed out. Run the command again."

Handler = Callable[[discord.Interaction], Awaitable[None]]


class _Button(discord.ui.Button[Any]):
    """A button that runs `handler` when pressed."""

    def __init__(self, handler: Handler, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class _Select(discord.ui.Select[Any]):
    """A menu that runs `handler` when a choice is made."""

    def __init__(self, handler: Handler, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class _ChannelSelect(discord.ui.ChannelSelect[Any]):
    """A channel menu that runs `handler` when a channel is picked."""

    def __init__(self, handler: Handler, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class _Menu(discord.ui.View):
    """A private menu that says so when it times out, instead of failing silently."""

    def __init__(self) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.origin: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)  # tag this button press's logs
        return True

    async def on_timeout(self) -> None:
        if self.origin is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.origin.edit_original_response(content=TIMED_OUT, view=None)


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast("DMBot", interaction.client)


def _now() -> int:
    return int(time.time())


def _is_manager(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and user.guild_permissions.manage_guild


async def _tell(interaction: discord.Interaction, text: str) -> None:
    """A private reply, whether or not this interaction has been answered already."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


async def _send(interaction: discord.Interaction, text: str, view: _Menu | None = None) -> None:
    """Send a new private message with a menu, remembering it so a timeout can edit it."""
    if view is None:
        await _tell(interaction, text)
        return
    if interaction.response.is_done():
        await interaction.followup.send(text, view=view, ephemeral=True, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(
            text, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )
    view.origin = interaction


async def _replace(interaction: discord.Interaction, text: str, view: _Menu | None) -> None:
    """Swap the menu message this button or menu belongs to for the next step."""
    await interaction.response.edit_message(content=text, view=view, allowed_mentions=NO_PINGS)
    if view is not None:
        view.origin = interaction


NOT_IN_SERVER = "Use this in a server."


# ---- /dmbot start: welcome, then which campaign? ---------------------------------------


class Welcome(_Menu):
    @discord.ui.button(label="Set up my first campaign", style=discord.ButtonStyle.success)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await interaction.response.send_modal(NewCampaignForm())


class CampaignPicker(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        last = campaigns[0]  # list_campaigns() is most recently used first
        self.last_id = last.id
        self.add_item(
            _Button(
                self._continue,
                label=logic.continue_label(last),
                style=discord.ButtonStyle.primary,
                row=0,
            )
        )
        self.add_item(
            _Button(self._new, label="＋ New campaign", style=discord.ButtonStyle.secondary, row=0)
        )
        others = campaigns[1 : 1 + logic.SELECT_OPTIONS_MAX]
        if others:
            now = _now()
            self.pick = _Select(
                self._picked,
                placeholder="Or pick another campaign…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(c.name, logic.OPTION_LABEL_MAX),
                        value=c.id,
                        description=logic.option_description(c, now),
                    )
                    for c in others
                ],
                row=1,
            )
            self.add_item(self.pick)

    async def _continue(self, interaction: discord.Interaction) -> None:
        await choose_campaign(interaction, self.last_id)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await choose_campaign(interaction, self.pick.values[0])

    async def _new(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NewCampaignForm())


def picker_text(last: Campaign) -> str:
    return (
        "**Which campaign are we playing?**\n"
        f"Last time: **{last.name}** ({logic.played_line(last)})"
    )


async def choose_campaign(interaction: discord.Interaction, campaign_id: str) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    campaign = await _bot(interaction).campaigns.get(guild.id, campaign_id)
    if campaign is None:
        await _tell(interaction, "That campaign isn't here any more. Run `/dmbot start` again.")
        return
    if not logic.can_run(campaign, interaction.user.id, _is_manager(interaction)):
        await _tell(
            interaction,
            f"Only **{campaign.name}**'s DM ({logic.dm_list(campaign)}) can start it.",
        )
        return
    await show_voice_step(interaction, campaign)


# ---- new campaign: name form, then settings -----------------------------------------


class NewCampaignForm(discord.ui.Modal, title="New campaign"):
    name: discord.ui.TextInput[NewCampaignForm] = discord.ui.TextInput(
        label="Campaign name",
        placeholder="For example: Rime of the Frostmaiden",
        max_length=80,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await _tell(interaction, NOT_IN_SERVER)
            return
        try:
            name = clean_name(self.name.value)
        except CampaignError as exc:
            await _tell(interaction, str(exc))
            return
        existing = await _bot(interaction).campaigns.list_campaigns(guild.id)
        if any(name_key(c.name) == name_key(name) for c in existing):
            await _tell(
                interaction,
                f"There's already a campaign called **{name}**. Pick it from "
                "`/dmbot start`, or choose another name.",
            )
            return
        view = NewCampaignSettings(name)
        await _send(interaction, view.text(), view)


class NewCampaignSettings(_Menu):
    """Recommended settings are pre-selected; the DM can just press Create."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.target = DEFAULT_TARGET
        self.fallback = DEFAULT_FALLBACK
        self.optional = True
        self.visibility = DEFAULT_DM_SCREEN_VISIBILITY
        self._build()

    def text(self) -> str:
        return "\n".join(
            [
                f"**New campaign: {self.name}**",
                "These are the recommended settings. Change any of them, "
                "then press **Create campaign**.",
                *logic.settings_summary(self.target, self.fallback, self.optional, self.visibility),
            ]
        )

    def _select(self, row: int, choices: dict[str, str], current: str, attr: str) -> None:
        async def changed(interaction: discord.Interaction) -> None:
            value = select.values[0]
            if attr == "optional":
                self.optional = value == "on"
            else:
                setattr(self, attr, value)
            self._build()
            await _replace(interaction, self.text(), self)

        select = _Select(
            changed,
            row=row,
            options=[
                discord.SelectOption(label=label, value=value, default=value == current)
                for value, label in choices.items()
            ],
        )
        self.add_item(select)

    def _build(self) -> None:
        self.clear_items()
        fallbacks = logic.fallback_choices(self.target)
        if self.fallback not in fallbacks:
            self.fallback = next(iter(fallbacks))
        self._select(0, logic.main_rules_choices(), self.target, "target")
        self._select(1, fallbacks, self.fallback, "fallback")
        self._select(2, logic.OPTIONAL_RULES_CHOICES, "on" if self.optional else "off", "optional")
        self._select(3, logic.visibility_choices(), self.visibility, "visibility")
        self.add_item(
            _Button(
                self._create,
                label="✅ Create campaign",
                style=discord.ButtonStyle.success,
                row=4,
            )
        )

    async def _create(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await _tell(interaction, NOT_IN_SERVER)
            return
        try:
            campaign = await _bot(interaction).campaigns.create(
                guild.id,
                self.name,
                interaction.user.id,
                target_ruleset=self.target,
                fallback_ruleset=self.fallback,
                optional_rules_default=self.optional,
                dm_screen_visibility=self.visibility,
            )
        except CampaignError as exc:
            await _tell(interaction, str(exc))
            return
        self.stop()
        await show_voice_step(interaction, campaign, created=True)


# ---- /dmbot start: which voice channel? ---------------------------------------------


class VoicePicker(_Menu):
    def __init__(self, campaign: Campaign, default_id: int | None) -> None:
        super().__init__()
        self.campaign = campaign
        self.channel_id = default_id
        default = (
            [discord.SelectDefaultValue(id=default_id, type=discord.SelectDefaultValueType.channel)]
            if default_id is not None
            else []
        )
        self.pick = _ChannelSelect(
            self._picked,
            channel_types=VOICE_TYPES,
            placeholder="Pick the table's voice channel",
            default_values=default,
            row=0,
        )
        self.add_item(self.pick)
        self.start_button = _Button(
            self._start,
            label="▶ Start listening",
            style=discord.ButtonStyle.success,
            disabled=default_id is None,
            row=1,
        )
        self.add_item(self.start_button)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.channel_id = self.pick.values[0].id
        self.start_button.disabled = False
        await interaction.response.edit_message(view=self)

    async def _start(self, interaction: discord.Interaction) -> None:
        if self.channel_id is None:
            await _tell(interaction, "Pick a voice channel first.")
            return
        # Starting can take a few seconds (database, voice service, DM screen setup):
        # acknowledge now so Discord doesn't give up on the button press.
        await interaction.response.defer()
        ok, message = await _bot(interaction).start_campaign_session(
            interaction, self.campaign.id, self.channel_id
        )
        if ok:
            self.stop()
            await interaction.edit_original_response(
                content=message, view=None, allowed_mentions=NO_PINGS
            )
        else:
            await _tell(interaction, message)


async def show_voice_step(
    interaction: discord.Interaction, campaign: Campaign, *, created: bool = False
) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    member = interaction.user
    member_voice = (
        member.voice.channel.id
        if isinstance(member, discord.Member) and member.voice and member.voice.channel
        else None
    )
    me = guild.me
    joinable = {
        c.id
        for c in [*guild.voice_channels, *guild.stage_channels]
        if me is not None and c.permissions_for(me).connect
    }
    default_id = logic.default_voice_channel(campaign, member_voice, joinable)
    if created:
        heading = f"✅ Created **{campaign.name}**."
    else:
        heading = f"**{campaign.name}** ({logic.played_line(campaign)})"
    text = f"{heading}\n**Which voice channel is the table in?**"
    if default_id is not None:
        text += f"\nReady to use <#{default_id}>. Pick a different one below if needed."
    view = VoicePicker(campaign, default_id)
    if interaction.type is discord.InteractionType.component:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


# ---- /dmbot backup and restore ------------------------------------------------------


async def send_backup(interaction: discord.Interaction, campaign_id: str) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    campaign = await bot.campaigns.get(guild.id, campaign_id)
    if campaign is None or not logic.can_run(
        campaign, interaction.user.id, _is_manager(interaction)
    ):
        await _tell(interaction, logic.NO_CAMPAIGN_ACCESS)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    data = await bot.campaigns.export(guild.id, campaign.id)
    raw = await asyncio.to_thread(encode_backup, data)
    file = discord.File(io.BytesIO(raw), filename=logic.backup_filename(campaign.name, _now()))
    await interaction.followup.send(
        f"💾 Here's a copy of **{campaign.name}**. Keep it somewhere safe, and don't share it "
        "publicly: it holds the campaign's private notes.\n"
        "Use `/dmbot restore` to bring it back, here or in another server.",
        file=file,
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


class BackupPicker(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        now = _now()
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(
                    label=logic.shorten(c.name, logic.OPTION_LABEL_MAX),
                    value=c.id,
                    description=logic.option_description(c, now),
                )
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await send_backup(interaction, self.pick.values[0])


class RestoreChoice(_Menu):
    def __init__(self, data: object, name: str, replaceable: list[Campaign]) -> None:
        super().__init__()
        self.data = data
        self.name = name
        self.replaceable_ids = [c.id for c in replaceable]
        self.add_item(
            _Button(
                self._as_new,
                label="Restore as a new campaign",
                style=discord.ButtonStyle.primary,
                row=0,
            )
        )
        if replaceable:
            self.pick = _Select(
                self._picked,
                placeholder="Or replace one of your campaigns…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(c.name, logic.OPTION_LABEL_MAX), value=c.id
                    )
                    for c in replaceable[: logic.SELECT_OPTIONS_MAX]
                ],
                row=1,
            )
            self.add_item(self.pick)

    async def _as_new(self, interaction: discord.Interaction) -> None:
        await self.restore(interaction, None)

    async def _picked(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await _tell(interaction, NOT_IN_SERVER)
            return
        target = await _bot(interaction).campaigns.get(guild.id, self.pick.values[0])
        if target is None:
            await _tell(interaction, "That campaign isn't here any more.")
            return
        await _replace(
            interaction,
            f"⚠️ Replace **{target.name}** with the copy of **{self.name}**?\n"
            f"Everything in **{target.name}** will be swapped for what's in the copy. "
            "This can't be undone, so you may want to download it first.",
            ConfirmReplace(self, target),
        )

    async def restore(self, interaction: discord.Interaction, replace_id: str | None) -> None:
        guild = interaction.guild
        if guild is None:
            await _tell(interaction, NOT_IN_SERVER)
            return
        bot = _bot(interaction)
        # The session lock stops a campaign being replaced while it's starting up.
        async with bot.session_lock(guild.id):
            if replace_id is not None and bot.active_campaign_id(guild.id) == replace_id:
                await _tell(
                    interaction, "That campaign is playing right now. Use `/dmbot stop` first."
                )
                return
            try:
                campaign = await bot.campaigns.import_backup(
                    guild.id, self.data, interaction.user.id, replace_campaign_id=replace_id
                )
            except CampaignError as exc:
                await _tell(interaction, str(exc))
                return
        self.stop()
        if replace_id is None:
            done = f"✅ Restored **{campaign.name}**."
        else:
            done = f"✅ **{campaign.name}** now holds everything from the copy of **{self.name}**."
        await _replace(interaction, f"{done} Use `/dmbot start` to play it.", None)


class ConfirmReplace(_Menu):
    def __init__(self, parent: RestoreChoice, target: Campaign) -> None:
        super().__init__()
        self.parent = parent
        self.target = target

    @discord.ui.button(label="Yes, replace it", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.stop()
        await self.parent.restore(interaction, self.target.id)

    @discord.ui.button(label="💾 Download it first", style=discord.ButtonStyle.secondary)
    async def download(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await send_backup(interaction, self.target.id)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.stop()
        await _replace(interaction, "Nothing was changed.", None)


# ---- /dmbot help ---------------------------------------------------------------------


class HelpButtons(_Menu):
    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary)
    async def status(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await _tell(interaction, NOT_IN_SERVER)
            return
        await _tell(interaction, "\n".join(await _bot(interaction).status_lines(guild.id)))


# ---- the /dmbot command group --------------------------------------------------------

dmbot_group = app_commands.Group(
    name="dmbot", description="DMbot: help for the Dungeon Master", guild_only=True
)


@dmbot_group.command(name="start", description="Pick your campaign and voice channel, then start")
async def dmbot_start(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    problem = bot.start_blocker(guild.id)
    if problem:
        await _tell(interaction, problem)
        return
    campaigns = await bot.campaigns.list_campaigns(guild.id)
    if not campaigns:
        await _send(interaction, logic.WELCOME_TEXT, Welcome())
        return
    await _send(interaction, picker_text(campaigns[0]), CampaignPicker(campaigns))


@dmbot_group.command(name="stop", description="Stop listening")
async def dmbot_stop(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    message = await _bot(interaction).stop_session(
        guild.id, interaction.user.id, _is_manager(interaction)
    )
    await _tell(interaction, message)


@dmbot_group.command(name="help", description="What DMbot does, and how to use it")
async def dmbot_help(interaction: discord.Interaction) -> None:
    await _send(interaction, logic.HELP_TEXT, HelpButtons())


@dmbot_group.command(name="backup", description="Download a copy of a campaign")
async def dmbot_backup(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    mine = logic.runnable(
        await _bot(interaction).campaigns.list_campaigns(guild.id),
        interaction.user.id,
        _is_manager(interaction),
    )
    if not mine:
        await _tell(
            interaction,
            "You're not the DM of any campaign here. Use `/dmbot start` to set one up.",
        )
    elif len(mine) == 1:
        await send_backup(interaction, mine[0].id)
    else:
        await _send(interaction, "**Which campaign do you want a copy of?**", BackupPicker(mine))


@dmbot_group.command(name="restore", description="Bring back a campaign from a copy")
@app_commands.describe(file="The .dmbot.json file you got from /dmbot backup")
async def dmbot_restore(interaction: discord.Interaction, file: discord.Attachment) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    if file.size > MAX_BACKUP_BYTES:
        await _tell(interaction, "That backup file is too big.")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        raw = await file.read()
        data = await asyncio.to_thread(decode_backup, raw)
    except CampaignError as exc:
        await _tell(interaction, str(exc))
        return
    except discord.HTTPException:
        await _tell(interaction, "I couldn't download that file. Try again.")
        return
    name = logic.backup_campaign_name(data)
    if name is None:
        await _tell(interaction, "That file isn't a DMbot campaign backup.")
        return
    campaigns = await _bot(interaction).campaigns.list_campaigns(guild.id)
    # Only a campaign's own DM may replace it (the store enforces this too).
    replaceable = [c for c in campaigns if interaction.user.id in c.dm_user_ids]
    await _send(
        interaction,
        f"**Restore the copy of {name}?**\n"
        "You'll be its DM. `/dmbot start` will ask which voice channel to use.",
        RestoreChoice(data, name, replaceable),
    )
