"""🔒 Only some people know this, on a clue's name card (#1649; docs/STORY_MEMORY.md, section 2).

The DM lists the characters who know a clue or secret; after a session DMbot then points out a
claim that has someone else speaking of it, and never anything about a clue left open. Who knows
it is its confirmed `knows` facts, so listing someone is the same as saying "they know it".
DM only, checked on every press; nothing here is shown to a player.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory.clues import TURNED_OFF
from dmbot.memory.models import CONFIRMED, DM, Entity, MemoryRuleError
from dmbot.memory.search import MAX_RESULTS, find
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)


ONLY_DM = "Only the DM can change who knows this."
NOBODY = "Nobody is listed yet."


def audience_text(name: str, who: list[str], limited: bool, note: str = "") -> str:
    """The screen in plain words: what the switch does now, and who is listed."""
    lines = [f"🔒 **{_md(name)}**"]
    if limited:
        lines.append(
            "**Only some people know this.** After a session, DMbot tells you if someone who "
            "isn't listed talks about it."
        )
    else:
        lines.append(
            "**DMbot isn't tracking who knows this,** so it says nothing about it. Add who knows, "
            "then press **Only some know**."
        )
    lines.append(f"**Who knows it:** {', '.join(_md(n) for n in who) if who else NOBODY}")
    if limited:
        lines.append("DMbot never decides who learned it. You do.")
    return fit_message("\n".join(([note] if note else []) + lines), limit=MESSAGE_MAX - 100)


async def show_audience(
    interaction: discord.Interaction,
    campaign_id: str,
    clue_id: str,
    *,
    note: str = "",
    replace: bool = True,
) -> None:
    """Draw the screen from the database as it is now (the DM may have just changed it)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    clue = await memory.entity(campaign.guild_id, campaign.id, clue_id)
    if clue is None or clue.status != CONFIRMED or clue.type != "clue":
        await _tell(interaction, GONE)
        return
    who = await memory.clue_audience(campaign.guild_id, campaign.id, clue_id)
    view = AudienceMenu(campaign_id, clue_id, clue.audience_limited, who)
    text = audience_text(clue.name, [w.name for w in who], clue.audience_limited, note)
    await _replace(interaction, text, view)


class AudienceMenu(_Menu):
    def __init__(
        self, campaign_id: str, clue_id: str, limited: bool, who: Sequence[Entity]
    ) -> None:
        super().__init__()
        self.campaign_id, self.clue_id = campaign_id, clue_id
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._add, label="➕ Add who knows", style=discord.ButtonStyle.primary)
        )
        self.add_item(
            _Button(
                self._switch,
                label="🔓 Let anyone know" if limited else "🔒 Only some know",
                style=grey,
            )
        )
        self.add_item(_Button(self._back, label="↩ Back", style=grey))
        self.who: dict[str, str] = {w.id: w.name for w in who}
        if self.who:
            self.remove = _Select(
                self._remove,
                placeholder="Take someone off…",
                options=[
                    discord.SelectOption(label=logic.shorten(name, logic.OPTION_LABEL_MAX), value=i)
                    for i, name in list(self.who.items())[: logic.SELECT_OPTIONS_MAX]
                ],
                row=1,
            )
            self.add_item(self.remove)

    async def _dm(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return False
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return False
        return True

    async def _add(self, interaction: discord.Interaction) -> None:
        if await self._dm(interaction):
            await interaction.response.send_modal(KnowerForm(self.campaign_id, self.clue_id))

    async def _switch(self, interaction: discord.Interaction) -> None:
        if not await self._dm(interaction):
            return
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        clue = await memory.entity(campaign.guild_id, campaign.id, self.clue_id)
        if clue is None:
            await _tell(interaction, GONE)
            return
        try:
            await memory.set_audience_limited(
                campaign.guild_id, campaign.id, self.clue_id, not clue.audience_limited, source=DM
            )
        except MemoryRuleError as exc:
            await show_audience(interaction, self.campaign_id, self.clue_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        await show_audience(interaction, self.campaign_id, self.clue_id)

    async def _remove(self, interaction: discord.Interaction) -> None:
        if not await self._dm(interaction):
            return
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        who = self.remove.values[0]
        name = self.who.get(who, "They")
        try:
            written = await memory.remove_knower(
                campaign.guild_id, campaign.id, self.clue_id, who, source=DM
            )
        except MemoryRuleError as exc:
            await show_audience(interaction, self.campaign_id, self.clue_id, note=f"⚠️ {exc}")
            return
        _clue, turned_off = written.value
        changed(interaction, campaign)
        note = f"✅ **{_md(name)}** is off the list." + (f" {TURNED_OFF}" if turned_off else "")
        self.stop()
        await show_audience(interaction, self.campaign_id, self.clue_id, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.clue_id, replace=True)


class KnowerForm(discord.ui.Modal, title="Who knows it?"):
    name: discord.ui.TextInput[KnowerForm] = discord.ui.TextInput(
        label="A character's name",
        placeholder="Type part of the name or a nickname",
        max_length=100,
    )

    def __init__(self, campaign_id: str, clue_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.clue_id = campaign_id, clue_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        cache = getattr(interaction.client, "lookup", None)
        if campaign is None or cache is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        names = await cache.get(campaign.guild_id, campaign.id)
        found = [
            m
            for m in find(names, self.name.value, secrets=True)
            if names.entities[m.entity_id].type == "character"
            and names.entities[m.entity_id].status == CONFIRMED
        ][:MAX_RESULTS]
        if len(found) == 1:
            await add_knower(interaction, self.campaign_id, self.clue_id, found[0].entity_id)
            return
        if not found:
            await show_audience(
                interaction,
                self.campaign_id,
                self.clue_id,
                note=(
                    f"No character called **{_md(self.name.value)}**. "
                    "Add them as a name first (/dmbot names)."
                ),
            )
            return
        options = [
            discord.SelectOption(
                label=logic.shorten(names.entities[m.entity_id].name, logic.OPTION_LABEL_MAX),
                value=m.entity_id,
            )
            for m in found
        ]
        await _replace(
            interaction,
            f"**Which one is {_md(self.name.value)}?**",
            PickKnower(self.campaign_id, self.clue_id, options),
        )


class PickKnower(_Menu):
    def __init__(self, campaign_id: str, clue_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__()
        self.campaign_id, self.clue_id = campaign_id, clue_id
        self.pick = _Select(self._picked, placeholder="Pick one…", options=options)
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await add_knower(interaction, self.campaign_id, self.clue_id, self.pick.values[0])


async def add_knower(
    interaction: discord.Interaction, campaign_id: str, clue_id: str, character_id: str
) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    character = await memory.entity(campaign.guild_id, campaign.id, character_id)
    try:
        await memory.add_knower(campaign.guild_id, campaign.id, clue_id, character_id, source=DM)
    except MemoryRuleError as exc:
        await show_audience(interaction, campaign_id, clue_id, note=f"⚠️ {exc}")
        return
    changed(interaction, campaign)
    who = _md(character.name) if character is not None else "They"
    await show_audience(interaction, campaign_id, clue_id, note=f"✅ **{who}** is on the list.")
