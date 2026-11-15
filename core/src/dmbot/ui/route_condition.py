"""🚧 Condition: whether a road, pass or bridge is open, hard going, closed or gone (#4258, MapBot
layers slice 4a; Supervisor's ruling and the ux-critic's words on #4258). One screen, opened from a
route place's card or a known road's edit page: one menu of six choices, ✅ for DMbot's read, 🙈 /
👁️, and Back. A change in the story keeps what it was before, and the card says so ("Closed since
session 5 (was open)"). For the campaign's DMs only, checked on every press; players see a
condition only once the DM has kept it and not hidden it (`map_fact_store.conditions_for`)."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import discord

from dmbot.memory import gate, map_fact_store, route_condition
from dmbot.memory import map_facts as mf
from dmbot.memory.map_fact_store import Condition
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.store import MemoryStore
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

BUTTON = "🚧 Condition"
KEEP_BUTTON = "✅ Condition is right"
HIDE_BUTTON = "🙈 Hide from players"
SHOW_BUTTON = "👁️ Show to players"
BACK_BUTTON = "↩ Back"
HOLDER = "How is it now?"
ONLY_DM = "Only the DM can change a road's condition."
NOTHING_CHANGED = "Nothing changed: that's already its condition."
CLASH = "🚧 Someone changed that condition just now. Here it is; try again if it's still wrong."
NOT_SURE = "not_sure"  # (the menu's value for "Not sure": never a value of the list)
# The six choices (label, description), in the menu's order
CHOICES: dict[str, tuple[str, str]] = {
    route_condition.OPEN: ("Open", "Nothing in the way"),
    route_condition.HARD: ("Hard going", "Slow or risky, but you can get through"),
    route_condition.CLOSED: ("Closed", "Blocked or snowed in, for now"),
    route_condition.GONE: ("Gone", "Collapsed or washed away for good"),
    mf.OTHER: ("Something else…", "Say it in your own words"),
    NOT_SURE: ("Not sure", "Take the condition off"),
}
WORDS = {
    route_condition.OPEN: "open", route_condition.HARD: "hard going",
    route_condition.CLOSED: "closed", route_condition.GONE: "gone",
}  # fmt: skip


def words(f: mf.Fact) -> str:
    """The value in a line: "closed", "hard going", or the DM's own words as typed."""
    return f.words if f.value == mf.OTHER else WORDS.get(f.value, f.value)


def condition_line(c: Condition | None, numbers: Mapping[int, int], *, dm: bool) -> str:
    """The card's line, or "" when it has none: "🚧 **Condition:** Closed since session 5 (was
    open)". With no session number for when it changed (before any session), only "(was open)".
    DMbot's read is marked for the DM, as a length is."""
    if c is None:
        return ""
    now = words(c.now)
    text = _md(now[:1].upper() + now[1:])
    if c.before is not None:
        n = numbers.get(c.since_at) if c.since_at is not None else None
        since = f" since session {n}" if n is not None else ""
        text += f"{since} (was {_md(words(c.before))})"
    if dm and c.now.status == mf.READ:
        text += f" (DMbot read this. Players see it once you press {KEEP_BUTTON}.)"
    return f"\n🚧 **Condition:** {text}"


def ask_text(name: str, c: Condition | None) -> str:
    now = ""
    if c is not None:
        was = f" (was {_md(words(c.before))})" if c.before is not None else ""
        now = f" Now: {_md(words(c.now))}{was}."
    return (
        f"🚧 **Is {_md(name)} open?**{now} Pick how it is now. If it changes later in the story, "
        "pick again: DMbot remembers what it was before."
    )


def noted(name: str, value: str | None, own: str, before: str | None) -> str:
    """The note after a pick. The DM's own words don't always follow "is", so they get a colon."""
    if value is None:
        return f"🚧 Noted: no condition for **{_md(name)}**."
    if value == mf.OTHER:
        was = f"was {_md(before)}; now " if before else ""
        return f"🚧 Noted for **{_md(name)}**: {was}{_md(own)}."
    said = WORDS[value]
    if before:
        return f"🚧 Noted: **{_md(name)}** was {_md(before)}; now it's {said}."
    return f"🚧 Noted: **{_md(name)}** is {said}."


async def show(
    interaction: discord.Interaction,
    campaign_id: str,
    name: str = "",
    *,
    entity_id: str,
    relation_id: str = "",
    note: str = "",
) -> None:
    """Replace the card or the edit page with the condition screen. `entity_id` is the card to go
    back to (the route place itself, or the place whose road this is); `relation_id` names the
    road when it is a connection's."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    if not name:
        name = await _name(memory, campaign.guild_id, campaign.id, entity_id, relation_id)
        if not name:
            await _replace(interaction, GONE, None)
            return
    target = relation_id or entity_id
    got = await memory.route_conditions(
        campaign.guild_id, campaign.id, gate.Viewer.the_dm(),
        place_ids=[] if relation_id else [entity_id],
        relation_ids=[relation_id] if relation_id else [],
    )  # fmt: skip
    c = got.get(target)
    text = ask_text(name, c)
    await _replace(
        interaction,
        f"{note}\n\n{text}" if note else text,
        ConditionMenu(campaign_id, name, entity_id, relation_id, c),
    )


async def _name(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, relation_id: str
) -> str:
    """The route place's name, or "the road between A and B"; "" when it is gone."""
    if not relation_id:
        place = await memory.entity(guild_id, campaign_id, entity_id)
        return place.name if place is not None else ""
    road = await memory.relation(guild_id, campaign_id, relation_id)
    if road is None:
        return ""
    a = await memory.entity(guild_id, campaign_id, road.subject_id)
    b = await memory.entity(guild_id, campaign_id, road.object_id)
    return road_name(a.name, b.name) if a is not None and b is not None else ""


def road_name(a: str, b: str) -> str:
    return f"the road between {a} and {b}"


class ConditionMenu(_Menu):
    def __init__(
        self, campaign_id: str, name: str, entity_id: str, relation_id: str, c: Condition | None
    ) -> None:
        super().__init__()
        self.campaign_id, self.name = campaign_id, name
        self.entity_id, self.relation_id = entity_id, relation_id
        self.before = words(c.now) if c is not None else None
        self.value = c.now.value if c is not None else None
        self.read = c is not None and c.now.status == mf.READ
        self.pick = _Select(
            self._picked,
            placeholder=HOLDER,
            options=[
                discord.SelectOption(
                    label=label, description=about, value=key,
                    # (DMbot's read is left unmarked, as a size is: picking it says it's right)
                    default=key == self.value and not self.read and key != mf.OTHER,
                )
                for key, (label, about) in CHOICES.items()
            ],
        )  # fmt: skip
        self.add_item(self.pick)
        if self.read:
            self.add_item(_Button(self._keep, label=KEEP_BUTTON, row=1))
        if c is not None:
            shown = c.now.table_knows
            self.add_item(_Button(self._flip, label=HIDE_BUTTON if shown else SHOW_BUTTON, row=1))
            self.shown = shown
        self.add_item(_Button(self._back, label=BACK_BUTTON, row=1))

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await back(interaction, self.campaign_id, self.entity_id, self.relation_id)

    async def _picked(self, interaction: discord.Interaction) -> None:
        value = self.pick.values[0]
        if value == mf.OTHER:  # (a form must be the first answer: nothing is read before it)
            await interaction.response.send_modal(OwnWordsForm(self))
            return
        await self.save(interaction, None if value == NOT_SURE else value, "")

    async def save(self, interaction: discord.Interaction, value: str | None, own: str) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return
        if value is not None and value == self.value and value != mf.OTHER and not self.read:
            await _tell(interaction, NOTHING_CHANGED)
            return
        try:  # (one change, so one Undo)
            await memory.set_route_condition(
                campaign.guild_id, campaign.id, value, words=own,
                place_id="" if self.relation_id else self.entity_id,
                relation_id=self.relation_id, source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            note = CLASH if str(exc) == map_fact_store.GONE else f"🚧 Couldn't change it. {exc}"
            await self.redraw(interaction, note)
            return
        changed(interaction, campaign)
        # (a DMbot read the DM corrects was never part of the story: no "was" for it)
        before = self.before if self.value is not None and not self.read else None
        await self.redraw(interaction, noted(self.name, value, own, before))

    async def redraw(self, interaction: discord.Interaction, note: str) -> None:
        self.stop()
        await show(
            interaction, self.campaign_id, self.name, entity_id=self.entity_id,
            relation_id=self.relation_id, note=note,
        )  # fmt: skip

    async def _keep(self, interaction: discord.Interaction) -> None:
        await self._press(interaction, keep=True)

    async def _flip(self, interaction: discord.Interaction) -> None:
        await self._press(interaction, keep=False)

    async def _press(self, interaction: discord.Interaction, *, keep: bool) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        g, c, name = campaign.guild_id, campaign.id, _md(self.name)
        try:
            if keep:
                if self.relation_id:
                    await memory.keep_road_condition(g, c, self.relation_id, source=DM)
                else:
                    await memory.keep_map_facts(
                        g, c, self.entity_id, source=DM, layers=(mf.CONDITION,)
                    )
                note = f"✅ Noted: the condition DMbot read for **{name}** is right."
            else:
                show = not self.shown
                if self.relation_id:
                    await memory.show_road_condition(g, c, self.relation_id, show, source=DM)
                else:  # (a place's one 🙈 / 👁️ covers all it holds, as on its other screens)
                    await memory.show_map_facts(g, c, self.entity_id, show, source=DM)
                note = (
                    f"👁️ Noted: players who can see **{name}** see its condition."
                    if show
                    # (a place's one 🙈 covers all it holds: the ux-critic's point, the reviewer's)
                    else (
                        f"🙈 Noted: players can't see the condition of **{name}**."
                        if self.relation_id
                        else f"🙈 Noted: players can't see what you set for **{name}**."
                    )
                )
        except MemoryRuleError as exc:
            await self.redraw(interaction, f"🚧 Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        await self.redraw(interaction, note)


async def back(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, relation_id: str
) -> None:
    """Back to where the DM came from: the road's edit page, or the place's card."""
    if relation_id:
        from dmbot.ui.connection_edit import show_one

        await show_one(interaction, campaign_id, entity_id, relation_id)
        return
    from dmbot.ui.name_card import show_card

    await show_card(interaction, campaign_id, entity_id, replace=True)


class OwnWordsForm(discord.ui.Modal, title="Condition in your words"):
    """ "Something else…": the DM's own words, at most 60 characters."""

    text: discord.ui.TextInput[OwnWordsForm] = discord.ui.TextInput(
        label="How is it now?",
        placeholder="Half washed out at the ford",
        max_length=mf.WORDS_MAX,
    )

    def __init__(self, menu: ConditionMenu) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.menu = menu

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        own = " ".join(str(self.text.value or "").split())
        if not own or not own.isprintable():
            await _tell(interaction, "Type how it is, like “half washed out at the ford”.")
            return
        await self.menu.save(interaction, mf.OTHER, own)


__all__ = ["BUTTON", "GONE", "condition_line", "show"]
