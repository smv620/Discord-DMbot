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

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32})"

LABEL = "Campaign setting"
KEEP_LABEL = "Keep it"
NOT_SET = (
    f"• **{LABEL}:** not set yet. To pick one, open the card of a region or a bigger "
    "place and tap "
    "🌍 Make it the setting."
)
ONLY_DMS = "Only the DM can change the campaign's setting."
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
            f"• **{LABEL}:** {name} (DMbot's guess from your adventure). "
            f"Tap ✅ {KEEP_LABEL} if it's right."
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
        from dmbot.dm_screen import settings
        from dmbot.ui.names import changed

        set_log_context(guild_id=interaction.guild_id)
        allowed = await settings._may(interaction, self.campaign_id)  # (DMs only, every press)
        if allowed is None:
            return
        campaign = allowed[0]
        memory = getattr(interaction.client, "memory", None)
        if memory is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await memory.keep_setting(campaign.guild_id, campaign.id, self.entity_id, source=DM)
        except MemoryRuleError:  # (the guess is gone or changed: the card is drawn as it is now)
            pass
        except Exception:
            log.exception("Couldn't keep the campaign's setting")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        changed(interaction, campaign)
        await settings._redraw(interaction, campaign)
