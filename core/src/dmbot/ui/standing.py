"""🤝 Standing, on an NPC's, group's or place's name card (#1655; docs/STORY_MEMORY.md, section 5).
What the DM has said about how this one feels about each player character and the party, in five
plain steps, with the reasons; and the buttons to set it. DMbot only reminds the DM: it never
decides how anyone acts. DM only, asked again on every press; never shown to players or put in a
transcript.
"""

from __future__ import annotations

import logging

import discord

from dmbot.memory import standing
from dmbot.memory.models import CONFIRMED, DM, Entity, MemoryRuleError
from dmbot.memory.store import MemoryStore
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

BUTTON = "🤝 Standing"  # on the name card
ONLY_DM = "Only the DM can see or change that."
LINE_MAX = 330


def standing_line(viewer: str, subject: str, result: standing.Standing) -> str:
    """One line: who feels what about whom, and why."""
    head = f"**{_md(viewer)}** → **{_md(subject)}**: {result.shown}."
    if result.reasons:
        head += " " + " ".join(_md(r) for r in result.reasons)
    return head if len(head) <= LINE_MAX else head[: LINE_MAX - 1].rstrip() + "…"


def standing_text(
    viewer: Entity,
    targets: list[Entity],
    index: standing.Index,
    note: str = "",
) -> str:
    lines = [note] if note else []
    lines.append(f"🤝 **{_md(viewer.name)}**: standing")
    if not targets:
        lines.append("No player characters yet. Add them as names, or set standing for the party.")
    for target in targets:
        lines.append(
            "• " + standing_line(viewer.name, target.name, index.standing(viewer.id, target.id))
        )
    lines.append(f"_{standing.REMINDER}_")
    text = "\n".join(lines)
    return text if len(text) <= 1900 else text[:1899].rstrip() + "…"


async def _targets(memory: MemoryStore, guild_id: int, campaign_id: str) -> list[Entity]:
    """The party first (when there is one), then the player characters by name."""
    everyone = await memory.entities(guild_id, campaign_id)
    party = [e for e in everyone if e.party and e.status == CONFIRMED]
    pcs = sorted(
        (e for e in everyone if e.is_player_character and e.status == CONFIRMED),
        key=lambda e: e.name.casefold(),
    )
    return [*party, *pcs]


async def show_standing(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, *, note: str = ""
) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    viewer = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if viewer is None or viewer.status != CONFIRMED:
        await _tell(interaction, GONE)
        return
    index = await memory.standing_index(campaign.guild_id, campaign.id)
    targets = await _targets(memory, campaign.guild_id, campaign.id)
    targets = [t for t in targets if t.id != entity_id]
    text = standing_text(viewer, targets, index, note)
    group = viewer.type in ("faction", "place")
    await _replace(interaction, text, StandingMenu(campaign_id, viewer, targets, group))


class StandingMenu(_Menu):
    def __init__(
        self, campaign_id: str, viewer: Entity, targets: list[Entity], group: bool
    ) -> None:
        super().__init__()
        self.campaign_id, self.viewer, self.group = campaign_id, viewer, group
        self.targets = targets
        grey = discord.ButtonStyle.secondary
        self.add_item(_Button(self._set, label="Set standing…", style=discord.ButtonStyle.primary))
        if group:
            self.add_item(_Button(self._attitude, label="Set attitude…", style=grey))
        self.add_item(_Button(self._back, label="↩ Back", style=grey))

    async def _dm(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return False
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return False
        return True

    async def _set(self, interaction: discord.Interaction) -> None:
        if not await self._dm(interaction):
            return
        options = [
            discord.SelectOption(label=logic.shorten(t.name, logic.OPTION_LABEL_MAX), value=t.id)
            for t in self.targets[: logic.SELECT_OPTIONS_MAX]
        ]
        if not any(t.party for t in self.targets):
            options.insert(0, discord.SelectOption(label="The party", value=PARTY))
        await _replace(
            interaction,
            f"**Who is {_md(self.viewer.name)} feeling about?**",
            PickTarget(self.campaign_id, self.viewer, options[: logic.SELECT_OPTIONS_MAX]),
        )

    async def _attitude(self, interaction: discord.Interaction) -> None:
        if await self._dm(interaction):
            await interaction.response.send_modal(PeopleForm(self.campaign_id, self.viewer))

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.viewer.id, replace=True)


PARTY = "party"  # the party isn't made until the DM first sets something about it


class PickTarget(_Menu):
    def __init__(
        self, campaign_id: str, viewer: Entity, options: list[discord.SelectOption]
    ) -> None:
        super().__init__()
        self.campaign_id, self.viewer = campaign_id, viewer
        self.pick = _Select(self._picked, placeholder="Pick one…", options=options)
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back"))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        wanted = self.pick.values[0]
        try:
            if wanted == PARTY:
                target = await memory.ensure_party(campaign.guild_id, campaign.id, source=DM)
            else:
                found = await memory.entity(campaign.guild_id, campaign.id, wanted)
                if found is None or found.status != CONFIRMED:
                    raise MemoryRuleError(GONE)
                target = found
        except MemoryRuleError as exc:
            await show_standing(interaction, self.campaign_id, self.viewer.id, note=f"⚠️ {exc}")
            return
        await _replace(
            interaction,
            f"**How does {_md(self.viewer.name)} feel about {_md(target.name)}?**",
            PickStep(self.campaign_id, self.viewer, target, attitude=False),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await show_standing(interaction, self.campaign_id, self.viewer.id)


class PeopleForm(discord.ui.Modal, title="Toward which people?"):
    people: discord.ui.TextInput[PeopleForm] = discord.ui.TextInput(
        label="A people or kind",
        placeholder="For example: tabaxi, elves, outsiders",
        max_length=60,
    )

    def __init__(self, campaign_id: str, viewer: Entity) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.viewer = campaign_id, viewer

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        name = " ".join(self.people.value.split())
        if not name:
            await show_standing(interaction, self.campaign_id, self.viewer.id)
            return
        try:
            people = await memory.find_or_add_people(
                campaign.guild_id, campaign.id, name, source=DM
            )
        except MemoryRuleError as exc:
            await show_standing(interaction, self.campaign_id, self.viewer.id, note=f"⚠️ {exc}")
            return
        await _replace(
            interaction,
            f"**How does {_md(self.viewer.name)} feel about {_md(people.name)}?**",
            PickStep(self.campaign_id, self.viewer, people, attitude=True),
        )


class PickStep(_Menu):
    """The five steps, in order."""

    def __init__(self, campaign_id: str, viewer: Entity, target: Entity, *, attitude: bool) -> None:
        super().__init__()
        self.campaign_id, self.viewer, self.target, self.attitude = (
            campaign_id,
            viewer,
            target,
            attitude,
        )
        self.pick = _Select(
            self._picked,
            placeholder="Pick one…",
            options=[discord.SelectOption(label=s, value=s) for s in standing.STEPS],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back"))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        step = self.pick.values[0]
        try:
            save = memory.set_attitude if self.attitude else memory.set_impression
            await save(
                campaign.guild_id, campaign.id, self.viewer.id, self.target.id, step, source=DM
            )
        except MemoryRuleError as exc:
            await show_standing(interaction, self.campaign_id, self.viewer.id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        log.info("Standing set: %s", "attitude" if self.attitude else "impression")  # a count only
        self.stop()
        who, whom = _md(self.viewer.name), _md(self.target.name)
        note = f"✅ Saved: **{who}** is **{step}** toward **{whom}**."
        await show_standing(interaction, self.campaign_id, self.viewer.id, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await show_standing(interaction, self.campaign_id, self.viewer.id)
