"""🤝 Standing, on an NPC's, group's or place's name card (#1655; docs/STORY_MEMORY.md, section 5).
What the DM has said about how this one feels about each player character and the party, in five
plain steps, with the reasons; and the buttons to set it. DMbot only reminds the DM: it never
decides how anyone acts. DM only, asked again on every press; never shown to players or put in a
transcript.
"""

from __future__ import annotations

import logging
from functools import partial

import discord

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory import standing
from dmbot.memory.models import CONFIRMED, DM, Entity, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
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


REASONS_SHOWN = 3  # a line holds the nearest few; the rest are counted, never cut mid-sentence


def standing_line(viewer: str, subject: str, result: standing.Standing) -> str:
    """One line: who feels what about whom, and the nearest reasons whole."""
    shown = result.reasons[:REASONS_SHOWN]
    more = len(result.reasons) - len(shown)
    parts = [f"**{_md(viewer)}** → **{_md(subject)}**: {result.shown}."]
    parts += [_md(r) for r in shown]
    if more > 0:
        parts.append(f"(+{more} more)")
    return " ".join(parts)


def standing_text(
    viewer: Entity,
    targets: list[Entity],
    index: standing.Index,
    note: str = "",
) -> str:
    """The reminder comes first and is never cut; a long list of characters is, by whole line."""
    lines = [note] if note else []
    lines.append(f"🤝 **{_md(viewer.name)}**: how they feel")
    lines.append(f"_{standing.REMINDER}_")
    if not targets:
        lines.append(
            "No player characters yet. You can still set how it feels about **the party**."
        )
    budget = MESSAGE_MAX - 100 - sum(len(x) + 1 for x in lines) - 40
    for n, target in enumerate(targets):
        line = "• " + standing_line(viewer.name, target.name, index.standing(viewer.id, target.id))
        if len(line) + 1 > budget:
            lines.append(f"…and {len(targets) - n} more.")
            break
        budget -= len(line) + 1
        lines.append(line)
    return "\n".join(lines)


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
    numbers: dict[int, int] = {}
    transcripts = getattr(_bot(interaction), "transcripts", None)
    if transcripts is not None:  # "session 4" in a deed's reason (#1732)
        try:
            sessions = await transcripts.sessions(campaign.guild_id, campaign.id, limit=100)
            numbers = {x.started_at: x.number for x in sessions}
        except Exception:
            log.exception("Couldn't number the sessions; the reasons show without them")
    index = await memory.standing_index(campaign.guild_id, campaign.id, numbers)
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
        self.add_item(
            _Button(self._set, label="Toward a character…", style=discord.ButtonStyle.primary)
        )
        if group:
            self.add_item(_Button(self._attitude, label="Toward a people…", style=grey))
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
        self.pick = _Select(self._picked, placeholder="Pick a character…", options=options)
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="↩ Back"))

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


class PeopleForm(discord.ui.Modal, title="Toward which kind of folk?"):
    people: discord.ui.TextInput[PeopleForm] = discord.ui.TextInput(
        label="Kind or people",
        placeholder="tabaxi, elves, outsiders",
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
            await show_standing(
                interaction, self.campaign_id, self.viewer.id, note="Nothing entered."
            )
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
        for name in standing.STEPS:  # five buttons: one tap saves
            self.add_item(_Button(partial(self._save, name), label=name))
        self.add_item(_Button(self._back, label="↩ Back", row=1))

    async def _save(self, step: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
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
        if self.attitude:
            note += f" It shows on any character who is one of the {whom}."
        await show_standing(interaction, self.campaign_id, self.viewer.id, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await show_standing(interaction, self.campaign_id, self.viewer.id)
