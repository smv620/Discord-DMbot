"""The "Campaign setting" line on the ⚙️ Manage DMbot card, and its ✅ Keep it button (#4291).

The setting is the one place the whole story happens in (#4173). The card shows it in one of three
states: the DM's own word, DMbot's guess from the shared adventure (the DM's alone to see and keep),
or nothing yet. Only the campaign's DMs get the line or the button; every press is checked again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.logs import set_log_context
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.setting_worlds import WORLDS
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32})"
_ID = rf"{_CAMPAIGN}:(?P<entity>[0-9a-f]{{32}})"

LABEL = "Campaign setting"
KEEP_LABEL = "Keep it"
NOT_SET = (
    f"• **{LABEL}:** not set. Pick one here, or open a big place's card (a region or "
    "bigger) and tap 🌍 Make it the setting."
)
ONLY_DMS = "Only the DM can change the campaign's setting."
CHANGED = "That guess had already changed. The card now shows the latest."
STALE_LIST = "That list was out of date. Pick again from the card."
FAILED = "Couldn't change the campaign's setting. Try again in a moment."


@dataclass(frozen=True)
class SettingField:
    """What the card needs to draw the line: the place (None when there is no setting) and
    whether it is only DMbot's guess so far."""

    entity_id: str | None
    name: str | None
    guessed: bool


def line(field: SettingField | None) -> list[str]:
    """The card's line, or nothing when the setting could not be read (a card never says "not
    set" about a setting it couldn't look up)."""
    if field is None:
        return []
    if field.entity_id is None or field.name is None:
        return [NOT_SET]
    name = discord.utils.escape_markdown(field.name)
    if field.guessed:
        return [
            f"• **{LABEL}:** {name}? DMbot guessed this from your adventure. "
            f"Keep it, or pick another."
        ]
    return [f"• **{LABEL}:** {name}"]


async def load(
    interaction: discord.Interaction, guild_id: int, campaign_id: str
) -> SettingField | None:
    """The campaign's setting as stored; None when it can't be read (story memory off or down)."""
    memory = getattr(interaction.client, "memory", None)
    if memory is None:
        return None
    try:
        place = await memory.setting(guild_id, campaign_id)
    except Exception:
        log.exception("Couldn't read the campaign's setting for the settings card")
        return None
    if place is None:
        return SettingField(None, None, False)
    return SettingField(place.id, place.name, place.setting_guessed)


class KeepSettingButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setkeep:{_ID}",
):
    """✅ Keep it, on DMbot's guess at the setting: the DM's word from now on (one change, so one
    Undo). The place is in the custom id so a card from before a change can't keep another place."""

    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=KEEP_LABEL,
                emoji="✅",
                style=discord.ButtonStyle.success,
                row=4,
                custom_id=f"dmbot:setkeep:{campaign_id}:{entity_id}",
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepSettingButton:
        return cls(match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        # (imported here: settings.py imports this module for its card)
        from dmbot.dm_screen import settings
        from dmbot.ui.names import changed

        set_log_context(guild_id=interaction.guild_id)
        allowed = await settings._may(interaction, self.campaign_id)
        if allowed is None:
            return
        campaign = allowed[0]
        if interaction.user.id not in campaign.dm_user_ids:  # (a server manager who isn't a DM)
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        memory = getattr(interaction.client, "memory", None)
        if memory is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await memory.keep_setting(campaign.guild_id, campaign.id, self.entity_id, source=DM)
        except MemoryRuleError:  # (the guess is gone or changed: the card shows it as it is now)
            await interaction.followup.send(CHANGED, ephemeral=True)
        except Exception:
            log.exception("Couldn't keep the campaign's setting")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        else:
            changed(interaction, campaign)
        await settings._redraw(interaction, campaign)


OWN = "own"  # the menu's last choice: a world that isn't in the list
OWN_LABEL = "Somewhere of my own"
PICK_TEXT = "🌍 **Which world is your story in?**\nPick one, or choose {own}."
CHOOSE_LABEL = "Choose a world"  # (no world chosen yet, or DMbot only guessed one: the DM keeps it)
FORM_TITLE = "Your world's name"
FORM_HINT = "Like Golarion or Middle Vale"


async def _choose(interaction: discord.Interaction, campaign_id: str, name: str) -> None:
    """The DM's pick, by name: checked again, saved as one change, and the card is drawn again.
    The store's own words are said when it refuses (a typed name can be a place that is too small
    or two places of one name)."""
    from dmbot.dm_screen import settings
    from dmbot.ui.names import changed

    set_log_context(guild_id=interaction.guild_id)
    allowed = await settings._may(interaction, campaign_id)
    if allowed is None:
        return
    campaign = allowed[0]
    if interaction.user.id not in campaign.dm_user_ids:  # (every press, and the form's send)
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return
    memory = getattr(interaction.client, "memory", None)
    if memory is None:
        await interaction.response.send_message(FAILED, ephemeral=True)
        return
    await interaction.response.defer()
    try:
        await memory.choose_setting(campaign.guild_id, campaign.id, name, source=DM)
    except MemoryRuleError as exc:  # (its words say what to do; the card stays as it is)
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception:
        log.exception("Couldn't choose the campaign's setting")
        await interaction.followup.send(FAILED, ephemeral=True)
        return
    else:
        changed(interaction, campaign)
    await settings._redraw(interaction, campaign)


class PickButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setpick:{_CAMPAIGN}",
):
    """The world button: shows the chosen world's name, or "Choose a world". It opens the list of
    worlds in place. DM only, checked again on every press."""

    def __init__(self, campaign_id: str, *, world: str | None = None) -> None:
        # (Discord cuts labels at 80 characters, so a long world name is cut here with an ellipsis;
        # a button rebuilt after a restart is only a handle, its label comes from the message)
        label = CHOOSE_LABEL if not world else (world if len(world) <= 80 else world[:79] + "…")
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="🌍",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:setpick:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PickButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.dm_screen import settings
        from dmbot.dm_screen.settings import BackToSettingsButton

        set_log_context(guild_id=interaction.guild_id)
        allowed = await settings._may(interaction, self.campaign_id)
        if allowed is None:
            return
        if interaction.user.id not in allowed[0].dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(WorldMenu(self.campaign_id))
        view.add_item(BackToSettingsButton(self.campaign_id))
        await interaction.response.edit_message(
            content=PICK_TEXT.format(own=OWN_LABEL), view=view, allowed_mentions=NO_PINGS
        )


class WorldMenu(
    discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]],
    template=rf"dmbot:setworld:{_CAMPAIGN}",
):
    """The list of worlds, each by its own name with its place under it, and Somewhere of my own.
    A world is picked by key, never by text from the menu."""

    def __init__(self, campaign_id: str) -> None:
        options = [
            discord.SelectOption(label=w.label, value=w.key, description=w.description)
            for w in WORLDS
        ]
        options.append(discord.SelectOption(label=OWN_LABEL, value=OWN))
        super().__init__(
            discord.ui.Select(
                placeholder="Pick a world",
                options=options,
                row=0,
                custom_id=f"dmbot:setworld:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WorldMenu:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.dm_screen import settings

        allowed = await settings._may(interaction, self.campaign_id)
        if allowed is None:
            return
        if interaction.user.id not in allowed[0].dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        value = self.item.values[0]
        if value == OWN:
            # (a form isn't registered anywhere: one open across a restart fails, as all forms do)
            await interaction.response.send_modal(OwnWorldForm(self.campaign_id))
            return
        world = next((w for w in WORLDS if w.key == value), None)
        if world is None:  # (a stale menu)
            await interaction.response.send_message(STALE_LIST, ephemeral=True)
            return
        await _choose(interaction, self.campaign_id, world.place)


class OwnWorldForm(discord.ui.Modal, title=FORM_TITLE):
    """Somewhere of my own: the world's name, typed."""

    name: discord.ui.TextInput[OwnWorldForm] = discord.ui.TextInput(
        label="World name", placeholder=FORM_HINT, max_length=80
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=900)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _choose(interaction, self.campaign_id, self.name.value)
