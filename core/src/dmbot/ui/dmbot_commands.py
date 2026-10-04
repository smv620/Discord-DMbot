"""`/dmbot start · stop · help · backup · restore`: buttons, menus, and forms.

Every step after the command is a button, a menu, or a short form (CLAUDE.md,
"Simple enough for a child"). All replies are private to the person who ran the command.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

import discord
from discord import app_commands

from dmbot.campaigns import (
    DEFAULT_DM_SCREEN_VISIBILITY,
    DM_SCREEN_VISIBILITY,
    FALLBACK_NONE,
    RULESETS,
    Campaign,
    CampaignError,
)
from dmbot.campaigns.models import DEFAULT_FALLBACK, DEFAULT_TARGET, clean_name, name_key
from dmbot.campaigns.store import MAX_BACKUP_BYTES, decode_backup, encode_backup
from dmbot.ui import logic

if TYPE_CHECKING:
    from dmbot.bot import DMBot

VIEW_TIMEOUT_S = 600
NO_PINGS = discord.AllowedMentions.none()
VOICE_TYPES = [discord.ChannelType.voice, discord.ChannelType.stage_voice]


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


# ---- /dmbot start: step 1, which campaign? -------------------------------------------


class CampaignPicker(discord.ui.View):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaigns = {c.id: c for c in campaigns}
        last = campaigns[0]  # list_campaigns() is most recently used first
        self.last_id = last.id

        cont = _Button(
            self._continue,
            label=logic.continue_label(last),
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.add_item(cont)

        new = _Button(
            self._new, label="＋ New campaign", style=discord.ButtonStyle.secondary, row=0
        )
        self.add_item(new)

        others = campaigns[1 : 1 + logic.SELECT_OPTIONS_MAX]
        if others:
            now = _now()
            pick = _Select(
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
            self.pick = pick
            self.add_item(pick)

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
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        return
    campaign = await bot.campaigns.get(guild.id, campaign_id)
    if campaign is None:
        await _tell(interaction, "That campaign isn't here any more. Run `/dmbot start` again.")
        return
    if not logic.can_run(campaign, interaction.user.id, _is_manager(interaction)):
        await _tell(
            interaction,
            f"Only the DM of **{campaign.name}** ({logic.dm_list(campaign)}) or a server "
            "manager can start it. Pick another campaign, or start a new one.",
        )
        return
    await show_voice_step(interaction, campaign, edit=True)


# ---- new campaign: name form, then settings -----------------------------------------


class NewCampaignForm(discord.ui.Modal, title="New campaign"):
    name: discord.ui.TextInput[NewCampaignForm] = discord.ui.TextInput(
        label="Campaign name",
        placeholder="For example: Rime of the Frostmaiden",
        max_length=80,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = _bot(interaction)
        guild = interaction.guild
        if guild is None:
            return
        try:
            name = clean_name(self.name.value)
        except CampaignError as exc:
            await _tell(interaction, str(exc))
            return
        existing = await bot.campaigns.list_campaigns(guild.id)
        if any(name_key(c.name) == name_key(name) for c in existing):
            await _tell(interaction, "This server already has a campaign with that name.")
            return
        view = NewCampaignSettings(name)
        await interaction.response.send_message(
            view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )


class NewCampaignSettings(discord.ui.View):
    """Recommended settings are pre-selected; the DM can just press Create."""

    def __init__(self, name: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
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

    def _select(
        self, row: int, placeholder: str, choices: dict[str, str], current: str, attr: str
    ) -> None:
        async def changed(interaction: discord.Interaction) -> None:
            value = select.values[0]
            if attr == "optional":
                self.optional = value == "on"
            else:
                setattr(self, attr, value)
            self._build()
            await interaction.response.edit_message(content=self.text(), view=self)

        select = _Select(
            changed,
            placeholder=placeholder,
            row=row,
            options=[
                discord.SelectOption(label=label, value=value, default=value == current)
                for value, label in choices.items()
            ],
        )
        self.add_item(select)

    def _build(self) -> None:
        self.clear_items()
        self._select(0, "Main rules", dict(RULESETS), self.target, "target")
        backups = {k: v for k, v in RULESETS.items() if k != self.target}
        backups[FALLBACK_NONE] = logic.ruleset_label(FALLBACK_NONE)
        if self.fallback == self.target:
            self.fallback = next(iter(backups))
        self._select(1, "Backup rules", backups, self.fallback, "fallback")
        self._select(
            2,
            "Optional rules",
            {
                "on": "Optional rules on (recommended)",
                "off": "Optional rules off",
            },
            "on" if self.optional else "off",
            "optional",
        )
        self._select(
            3, "Who sees the DM screen", dict(DM_SCREEN_VISIBILITY), self.visibility, "visibility"
        )
        create = _Button(
            self._create, label="✅ Create campaign", style=discord.ButtonStyle.success, row=4
        )
        self.add_item(create)

    async def _create(self, interaction: discord.Interaction) -> None:
        bot = _bot(interaction)
        guild = interaction.guild
        if guild is None:
            return
        try:
            campaign = await bot.campaigns.create(
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
        await show_voice_step(interaction, campaign, edit=True, created=True)


# ---- /dmbot start: step 2, which voice channel? -------------------------------------


class VoicePicker(discord.ui.View):
    def __init__(self, campaign: Campaign, default_id: int | None) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign
        self.channel_id = default_id

        pick = _ChannelSelect(
            self._picked,
            channel_types=VOICE_TYPES,
            placeholder="Pick the table's voice channel",
            default_values=(
                [
                    discord.SelectDefaultValue(
                        id=default_id, type=discord.SelectDefaultValueType.channel
                    )
                ]
                if default_id is not None
                else []
            ),
            row=0,
        )
        self.pick = pick
        self.add_item(pick)

        start = _Button(
            self._start,
            label="▶ Start listening",
            style=discord.ButtonStyle.success,
            disabled=default_id is None,
            row=1,
        )
        self.start_button = start
        self.add_item(start)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.channel_id = self.pick.values[0].id
        self.start_button.disabled = False
        await interaction.response.edit_message(view=self)

    async def _start(self, interaction: discord.Interaction) -> None:
        if self.channel_id is None:
            await _tell(interaction, "Pick a voice channel first.")
            return
        bot = _bot(interaction)
        ok, message = await bot.start_campaign_session(
            interaction, self.campaign.id, self.channel_id
        )
        if ok:
            self.stop()
            await interaction.response.edit_message(
                content=message, view=None, allowed_mentions=NO_PINGS
            )
        else:
            await _tell(interaction, message)


async def show_voice_step(
    interaction: discord.Interaction,
    campaign: Campaign,
    *,
    edit: bool,
    created: bool = False,
) -> None:
    guild = interaction.guild
    if guild is None:
        return
    member = interaction.user
    member_voice = (
        member.voice.channel.id
        if isinstance(member, discord.Member) and member.voice and member.voice.channel
        else None
    )
    usable = {c.id for c in [*guild.voice_channels, *guild.stage_channels]}
    default_id = logic.default_voice_channel(campaign, member_voice, usable)
    if created:
        heading = f"✅ Created **{campaign.name}**."
    else:
        heading = f"**{campaign.name}** ({logic.played_line(campaign)})"
    text = f"{heading}\n**Which voice channel is the table in?**"
    if default_id is not None:
        text += f"\nReady to use <#{default_id}>. Pick a different one below if needed."
    view = VoicePicker(campaign, default_id)
    if edit and interaction.type is discord.InteractionType.component:
        await interaction.response.edit_message(content=text, view=view, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(
            text, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )


# ---- /dmbot backup and restore ------------------------------------------------------


async def send_backup(interaction: discord.Interaction, campaign_id: str) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        return
    campaign = await bot.campaigns.get(guild.id, campaign_id)
    if campaign is None or not logic.can_run(
        campaign, interaction.user.id, _is_manager(interaction)
    ):
        await _tell(interaction, logic.NO_CAMPAIGN_ACCESS)
        return
    data = await bot.campaigns.export(guild.id, campaign.id)
    raw = await asyncio.to_thread(encode_backup, data)
    file = discord.File(io.BytesIO(raw), filename=logic.backup_filename(campaign.name, _now()))
    text = (
        f"💾 Here's a copy of **{campaign.name}**. Keep it somewhere safe.\n"
        "Use `/dmbot restore` to bring it back, here or in another server."
    )
    if interaction.response.is_done():
        await interaction.followup.send(text, file=file, ephemeral=True)
    else:
        await interaction.response.send_message(text, file=file, ephemeral=True)


class BackupPicker(discord.ui.View):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        now = _now()
        pick = _Select(
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
        self.pick = pick
        self.add_item(pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await send_backup(interaction, self.pick.values[0])


class RestoreChoice(discord.ui.View):
    def __init__(self, data: object, name: str, replaceable: list[Campaign]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.data = data
        self.name = name
        self.replace_id: str | None = None

        new = _Button(
            self._as_new,
            label="Restore as a new campaign",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.add_item(new)

        if replaceable:
            pick = _Select(
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
            self.pick = pick
            self.add_item(pick)

    async def _as_new(self, interaction: discord.Interaction) -> None:
        await self._restore(interaction, None)

    async def _picked(self, interaction: discord.Interaction) -> None:
        bot = _bot(interaction)
        guild = interaction.guild
        if guild is None:
            return
        target = await bot.campaigns.get(guild.id, self.pick.values[0])
        if target is None:
            await _tell(interaction, "That campaign isn't here any more.")
            return
        confirm = ConfirmReplace(self, target)
        await interaction.response.edit_message(
            content=(
                f"⚠️ Replace **{target.name}** with the copy of **{self.name}**?\n"
                f"Everything in **{target.name}** will be swapped for what's in the copy. "
                "This can't be undone. (Tip: make a `/dmbot backup` of it first.)"
            ),
            view=confirm,
            allowed_mentions=NO_PINGS,
        )

    async def _restore(self, interaction: discord.Interaction, replace_id: str | None) -> None:
        bot = _bot(interaction)
        guild = interaction.guild
        if guild is None:
            return
        if replace_id is not None and bot.active_campaign_id(guild.id) == replace_id:
            await _tell(interaction, "That campaign is playing right now. Use `/dmbot stop` first.")
            return
        try:
            campaign = await bot.campaigns.import_backup(
                guild.id, self.data, interaction.user.id, replace_campaign_id=replace_id
            )
        except CampaignError as exc:
            await _tell(interaction, str(exc))
            return
        self.stop()
        verb = "Replaced with" if replace_id else "Restored"
        await interaction.response.edit_message(
            content=(
                f"✅ {verb} **{campaign.name}**. Use `/dmbot start` to play it.\n"
                "You'll be asked which voice channel to use, and where DM updates go."
            ),
            view=None,
            allowed_mentions=NO_PINGS,
        )


class ConfirmReplace(discord.ui.View):
    def __init__(self, parent: RestoreChoice, target: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.parent = parent
        self.target = target

    @discord.ui.button(label="Yes, replace it", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self.parent._restore(interaction, self.target.id)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.stop()
        await interaction.response.edit_message(content="Nothing was changed.", view=None)


# ---- /dmbot help ---------------------------------------------------------------------


class HelpButtons(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary)
    async def status(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        lines = await _bot(interaction).status_lines(guild.id)
        await _tell(interaction, "\n".join(lines))


# ---- the /dmbot command group --------------------------------------------------------

dmbot_group = app_commands.Group(
    name="dmbot", description="DMbot: help for the Dungeon Master", guild_only=True
)


@dmbot_group.command(name="start", description="Pick your campaign and voice channel, then start")
async def dmbot_start(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await _tell(interaction, "Use this in a server.")
        return
    problem = bot.start_blocker(guild.id)
    if problem:
        await _tell(interaction, problem)
        return
    campaigns = await bot.campaigns.list_campaigns(guild.id)
    if not campaigns:
        # First time in this server: go straight to naming the first campaign.
        await interaction.response.send_modal(NewCampaignForm())
        return
    await interaction.response.send_message(
        picker_text(campaigns[0]),
        view=CampaignPicker(campaigns),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


@dmbot_group.command(name="stop", description="Stop listening")
async def dmbot_stop(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, "Use this in a server.")
        return
    await _tell(
        interaction, await bot.stop_session(guild.id, interaction.user.id, _is_manager(interaction))
    )


@dmbot_group.command(name="help", description="What DMbot does, and how to use it")
async def dmbot_help(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        logic.HELP_TEXT, view=HelpButtons(), ephemeral=True, allowed_mentions=NO_PINGS
    )


@dmbot_group.command(name="backup", description="Download a copy of a campaign")
async def dmbot_backup(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, "Use this in a server.")
        return
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    if not mine:
        await _tell(interaction, "You're not the DM of any campaign in this server yet.")
    elif len(mine) == 1:
        await send_backup(interaction, mine[0].id)
    else:
        await interaction.response.send_message(
            "**Which campaign do you want a copy of?**",
            view=BackupPicker(mine),
            ephemeral=True,
        )


@dmbot_group.command(name="restore", description="Bring back a campaign from a copy")
@app_commands.describe(file="The .dmbot.json file you got from /dmbot backup")
async def dmbot_restore(interaction: discord.Interaction, file: discord.Attachment) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, "Use this in a server.")
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
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    # Only a campaign's own DM may replace it (the store enforces this too).
    replaceable = [c for c in mine if interaction.user.id in c.dm_user_ids]
    await interaction.followup.send(
        f"**Restore the copy of {name}?**\n"
        "You'll be its DM. Channel settings aren't copied; `/dmbot start` asks for them.",
        view=RestoreChoice(data, name, replaceable),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )
