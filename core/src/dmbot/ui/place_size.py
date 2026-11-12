"""How big a place is, on the DM's place card (#4173 slice 1): one menu of sizes, from a plane down
to a room, so DMbot can tell a building from the town it stands in. DMbot reads the size from the
words beside a name ("the town of Targos"); the DM sets or changes it here, and "Not sure" clears
it. For the campaign's DMs only, checked on every press. Nothing here goes to players."""

from __future__ import annotations

import logging

import discord

from dmbot.memory import place_levels
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _Select, _tell
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

BUTTON = "📏 Size"
ONLY_DM = "Only the DM can change a place's size."
NOT_SURE = "not_sure"  # the menu's value for "no size" (a level is never this word)
HOLDER = "Pick a size"
# The menu's choices: a short name and an example each, so "Region" or "District" is plain
CHOICES = {
    "plane": ("Plane", "Like the Feywild"),
    "world": ("World or continent", "A whole world or landmass"),
    "region": ("Region", "Kingdom, forest, island"),
    "settlement": ("Town or city", "Also a village or port"),
    "district": ("District", "A quarter, ward, docks"),
    "street": ("Street or square", "Also a market or plaza"),
    "building": ("Building", "Inn, temple, shop, castle"),
    "room": ("Room", "Also a cellar or vault"),
}


def size_line(level: str | None, read: bool = False) -> str:
    """The card's line for a place's size, or "" when nobody knows it. A size DMbot read and the DM
    never checked says so: it changes which "two places at once" checks DMbot makes."""
    if level is None or level not in place_levels.LABELS:
        return ""
    label = place_levels.LABELS[level]
    if read:
        return f"\n📏 **Size:** {label} (DMbot read this; tap {BUTTON} to change)"
    return f"\n📏 **Size:** {label}"


def ask_text(name: str, level: str | None, read: bool = False) -> str:
    now = place_levels.LABELS.get(level or "", "")
    if not now:
        said = " No size yet."
    elif read:
        said = f" DMbot read: {now}. Is that right?"
    else:
        said = f" You said: {now}."
    return (
        f"📏 **How big is {_md(name)}?**{said}\n"
        'Then DMbot won\'t flag someone as "in two places" for being in a building and the town '
        "around it. Players who can see this place see its size too."
    )


async def show_sizes(interaction: discord.Interaction, campaign_id: str, entity_id: str) -> None:
    """Replace the card with the size menu."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None:
        await _replace(interaction, GONE, None)
        return
    await _replace(
        interaction,
        ask_text(entity.name, entity.place_level, entity.place_level_read),
        SizeMenu(campaign_id, entity_id, entity.place_level),
    )


def options(level: str | None) -> list[discord.SelectOption]:
    """Every size, largest first, then "Not sure"; the current one is marked as chosen."""
    out = [
        discord.SelectOption(
            label=CHOICES[key][0], description=CHOICES[key][1], value=key, default=key == level
        )
        for key in place_levels.LEVELS
    ]
    out.append(
        discord.SelectOption(
            label="Not sure",
            description="No size: DMbot won't guess",
            value=NOT_SURE,
            default=level is None,
        )
    )
    return out


class SizeMenu(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, level: str | None) -> None:
        super().__init__()
        self.campaign_id, self.entity_id = campaign_id, entity_id
        self.pick = _Select(self._picked, placeholder=HOLDER, options=options(level))
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="↩ Back", row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return
        value = self.pick.values[0]
        level = None if value == NOT_SURE else value
        try:
            written = await memory.set_place_level(
                campaign.guild_id, campaign.id, self.entity_id, level, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap ↩ Back to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        name = _md(written.value.name)
        note = (
            f"📏 Noted: **{name}** is {place_levels.LABELS[level]}."
            if level is not None
            else f"📏 Noted: no size for **{name}**."
        )
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


# ---- 🌍 The campaign's setting (#4173 slice 2) ---------------------------------------------------

SETTING_ON_BUTTON = "🌍 Make it the setting"
SETTING_OFF_BUTTON = "🌍 Remove as setting"
ONLY_SETTING_DM = "Only the DM can change the campaign's setting."


def setting_line(is_setting: bool) -> str:
    """The card's line for the campaign's setting, or "" for any other place."""
    if not is_setting:
        return ""
    # (players see the card too: the words are the same for everyone)
    return (
        "\n🌍 **Campaign setting:** the whole story happens here. DMbot counts smaller places as "
        "inside it unless the DM says otherwise."
    )


async def flip_setting(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, make: bool
) -> None:
    """Make this place the campaign's setting, or stop it being one: one change, so one Undo. The
    store holds the rules (a confirmed place, a region or bigger)."""
    from dmbot.ui.name_card import show_card

    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
        await _tell(interaction, ONLY_SETTING_DM)
        return
    try:
        written = await memory.set_setting(
            campaign.guild_id, campaign.id, entity_id if make else None, source=DM,
            only_if=entity_id,  # (a stale card never clears another place's mark)
        )  # fmt: skip
    except MemoryRuleError as exc:  # (the card is drawn again, so its buttons keep working)
        await show_card(
            interaction, campaign_id, entity_id, replace=True, note=f"🌍 Couldn't change it. {exc}"
        )
        return
    changed(interaction, campaign)
    name = _md(written.value.name) if written.value is not None else "That place"
    note = (
        f"🌍 Noted: **{name}** is the campaign's setting."
        if make
        else "🌍 Noted: no campaign setting now."
    )
    await show_card(interaction, campaign_id, entity_id, replace=True, note=note)
