"""A place's land, climate and who rules it, on its card (#4256, MapBot layers slice 2): the card's
lines, and the DM's screen to set them (the ux-critic's words, posted on #4256 before code). DMbot
uses them when someone asks about travel or where the party is. The DM sees what DMbot read from
the notes, marked; players see only what the DM kept and didn't hide (`map_fact_store.facts_for`).
For the campaign's DMs only, checked on every press."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

import discord

from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _Select, _tell
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

BUTTON = "🗺️ Land, climate, ruler"
ONLY_DM = "Only the DM can change a place's land, climate or ruler."
NOT_SURE = "not_sure"  # a menu's value for "nobody has said" (never a word of a list)
PART_BUTTON = "Only part of it"
ALL_BUTTON = "All or most of it"
HIDE_BUTTON = "🙈 Hide from players"
SHOW_BUTTON = "👁️ Show to players"
TYPE_BUTTON = "✏️ Type a group"
BACK_BUTTON = "↩ Back"
READ_MARK = " (DMbot read this)"
GROUPS_SHOWN = 23  # (Discord's 25 a menu, less "Not sure" and room for the current ruler)

# The land menu (the SRD's Travel Terrain underneath, in plain words; the D&D word is in the
# description so a DM still finds it).
LAND: dict[str, tuple[str, str]] = {
    "arctic": ("Ice and snow", "Arctic, glaciers, tundra"),
    "coastal": ("Coast", "Shore, beach, sea cliffs"),
    "desert": ("Desert", "Sand or bare rock"),
    "forest": ("Forest", "Woods or jungle"),
    "grassland": ("Grassland", "Plains, farms, meadows"),
    "hill": ("Hills", "Rolling hills, moors"),
    "mountain": ("Mountains", "Peaks and passes"),
    "swamp": ("Swamp", "Marsh, bog, wetland"),
    "underdark": ("Underdark", "Deep caves below the world"),
    "urban": ("Town or city", "Streets and buildings"),
    "waterborne": ("Open water", "Travel by boat or ship"),
}
WARMTH = {"arctic": "Freezing", "cold": "Cold", "temperate": "Mild", "warm": "Warm", "hot": "Hot"}
CLIMATE_HINTS = {
    ("arctic", ""): "Arctic",
    ("temperate", ""): "Temperate",
    ("hot", "dry"): "Like a desert",
    ("hot", "wet"): "Like a jungle",
    ("cold", "wet"): "Like a rainy coast",
}
RULED = {"rules": "Ruled by", "claims": "Claimed by", "occupies": "Taken over by",
         "protects": "Protected by"}  # fmt: skip
HOW_BUTTONS = {"rules": "👑 Rules it", "claims": "🚩 Claims it", "occupies": "⚔️ Took it over",
               "protects": "🛡️ Protects it"}  # fmt: skip
NOTED_HOW = {"rules": "rules", "claims": "claims", "occupies": "has taken over",
             "protects": "protects"}  # fmt: skip


def land_words(f: mf.Fact) -> str:
    word = f.words if f.value == mf.OTHER else LAND.get(f.value, (f.value,))[0].lower()
    return f"partly {word}" if f.how == "partly" else word


def climate_words(f: mf.Fact) -> str:
    word = f.words if f.value == mf.OTHER else WARMTH.get(f.value, f.value).lower()
    return f"{word} and {f.how}" if f.how in ("dry", "wet") else word


def card_lines(
    held: Mapping[str, mf.Held], names: Mapping[str, str], *, dm: bool, place_name: str = ""
) -> str:
    """The card's lines, or "" when nobody knows any of it: "🗺️ Land: mountains · Climate: cold
    (same as Icewind Dale)" and "👑 Ruled by: Ten-Towns" on its own line (a group's name is long and
    the verb changes). A value DMbot read is the DM's alone, marked, with one hint to check it.
    `names`: entry ids to the names this viewer may see."""

    def where(h: mf.Held) -> str:
        if h.from_id is None:
            return ""
        bigger = names.get(h.from_id)
        return f" (same as {_md(bigger)})" if bigger else ""

    def mark(h: mf.Held) -> str:
        return READ_MARK if dm and any(f.status == mf.READ for f in h.facts) else ""

    parts: list[str] = []
    if (land := held.get(mf.TERRAIN)) is not None:
        words = " and ".join(dict.fromkeys(land_words(f) for f in land.facts))
        parts.append(f"**Land:** {_md(words)}{where(land)}{mark(land)}")
    if (climate := held.get(mf.CLIMATE)) is not None:
        words = " and ".join(dict.fromkeys(climate_words(f) for f in climate.facts))
        parts.append(f"**Climate:** {_md(words)}{where(climate)}{mark(climate)}")
    lines = [f"\n🗺️ {' · '.join(parts)}"] if parts else []
    if (rule := held.get(mf.CONTROL)) is not None:
        by_how: dict[str, list[str]] = {}
        for f in rule.facts:
            if f.value in names:
                by_how.setdefault(f.how or "rules", []).append(_md(names[f.value]))
        for how, groups in by_how.items():
            lines.append(
                f"\n👑 **{RULED.get(how, 'Ruled by')}:** {' and '.join(groups)}"
                f"{where(rule)}{mark(rule)}"
            )
    if dm and any(f.status == mf.READ for h in held.values() for f in h.facts):
        lines.append(f"\nTap {BUTTON} to check.")
    return "".join(lines)


def viewer_for(dm: bool) -> gate.Viewer:
    """The DM sees everything; anyone else sees what the whole table knows (the card has one
    players' view), the safe side."""
    return gate.Viewer.the_dm() if dm else gate.Viewer.the_party()


def _own(held: Mapping[str, mf.Held], layer: str) -> list[mf.Fact]:
    h = held.get(layer)
    return list(h.facts) if h is not None and h.from_id is None else []


def screen_text(
    name: str,
    held: Mapping[str, mf.Held],
    names: Mapping[str, str],
    *,
    climate_here: bool,
    hidden: bool,
    note: str = "",
) -> str:
    now = card_lines(held, names, dm=True).replace("\n", " ").strip()
    lines = [f"{note}\n" if note else "", f"🗺️ **What's {_md(name)} like?**"]
    lines.append(f"Now: {now}" if now else "Nothing set yet.")
    lines.append("DMbot uses this when someone asks about travel or where the party is.")
    lines.append(
        "Hidden from players."
        if hidden
        else "Players who can see this place see it too, unless you hide it."
    )
    if not climate_here:
        up = held.get(mf.CLIMATE)
        if up is not None and up.from_id is not None and up.from_id in names:
            bigger = _md(names[up.from_id])
            words = " and ".join(climate_words(f) for f in up.facts)
            lines.append(
                f"Climate comes from {bigger}: {words}. To change it, open {bigger}'s card."
            )
        else:
            lines.append("Set the climate on the region it's in.")
    if _own(held, mf.CONTROL):
        lines.append("Claims = says it's theirs. Took it over = holds it by force.")
    return "\n".join(x for x in lines if x)


async def show_world(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, note: str = ""
) -> None:
    """Replace the card with the land, climate and ruler screen."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    place = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if place is None:
        await _replace(interaction, GONE, None)
        return
    held = await memory.map_facts_up(
        campaign.guild_id, campaign.id, entity_id, gate.Viewer.the_dm()
    )
    entities = await memory.entities(campaign.guild_id, campaign.id)
    onto = await memory.ontology(campaign.guild_id, campaign.id)
    names = {e.id: e.name for e in entities}
    groups = sorted(  # (a campaign's own kind under "group" counts: is_a, never the word)
        (e for e in entities if onto.is_a(e.type, "faction") and e.status == "confirmed"),
        key=lambda e: e.name.casefold(),
    )
    own = await memory.map_facts(campaign.guild_id, campaign.id, [entity_id], gate.Viewer.the_dm())
    hidden = not any(f.table_knows for f in own.get(entity_id, ()))
    climate_here = mf.allowed_on(mf.CLIMATE, form=place.form, level=place.place_level)
    land_here = mf.allowed_on(mf.TERRAIN, form=place.form, level=place.place_level)
    text = screen_text(place.name, held, names, climate_here=climate_here, hidden=hidden, note=note)
    view = WorldMenu(
        campaign_id, entity_id, place.name, held, names,
        [(g.id, g.name) for g in groups], climate_here=climate_here, land_here=land_here,
        hidden=hidden,
    )  # fmt: skip
    await _replace(interaction, text, view)


def _same_as(held: Mapping[str, mf.Held], layer: str, names: Mapping[str, str]) -> str:
    h = held.get(layer)
    if h is None or h.from_id is None or h.from_id not in names:
        return ""
    return f"Same as {names[h.from_id]}"[:25]


def land_options(
    held: Mapping[str, mf.Held], names: Mapping[str, str]
) -> list[discord.SelectOption]:
    mine = {f.value for f in _own(held, mf.TERRAIN)}
    out = [
        discord.SelectOption(label=label, description=hint, value=key, default=key in mine)
        for key, (label, hint) in LAND.items()
    ]
    same = _same_as(held, mf.TERRAIN, names)
    out.append(
        discord.SelectOption(
            label=same or "Not sure",
            description="Use the bigger place's land" if same else "DMbot won't guess",
            value=NOT_SURE,
            default=not mine,
        )
    )
    return out


def climate_options(held: Mapping[str, mf.Held]) -> list[discord.SelectOption]:
    mine = {f"{f.value}:{f.how}" for f in _own(held, mf.CLIMATE)}
    out: list[discord.SelectOption] = []
    for key, word in WARMTH.items():
        for how in ("", "dry", "wet"):
            label = f"{word} and {how}" if how else word
            out.append(
                discord.SelectOption(
                    label=label,
                    description=CLIMATE_HINTS.get((key, how)),
                    value=f"{key}:{how}",
                    default=f"{key}:{how}" in mine,
                )
            )
    out.append(
        discord.SelectOption(
            label="Not sure", description="DMbot won't guess", value=NOT_SURE, default=not mine
        )
    )
    return out


def ruler_options(
    held: Mapping[str, mf.Held], names: Mapping[str, str], groups: Sequence[tuple[str, str]]
) -> tuple[list[discord.SelectOption], int]:
    """The current ruler first, then the campaign's groups by name, at most GROUPS_SHOWN, then "Not
    sure" (the bigger place's ruler, when it has one). Returns the options and how many groups
    were left out."""
    mine = [f.value for f in _own(held, mf.CONTROL)]
    order = [g for g in mine if g in names] + [g for g, _ in groups if g not in mine]
    shown = order[:GROUPS_SHOWN]
    out = [discord.SelectOption(label=names[g][:100], value=g, default=g in mine) for g in shown]
    same = _same_as(held, mf.CONTROL, names)
    out.append(
        discord.SelectOption(
            label=same or "Not sure",
            description="Use the bigger place's ruler" if same else "DMbot won't guess",
            value=NOT_SURE,
            default=not mine,
        )
    )
    return out, max(0, len(order) - len(shown))


class WorldMenu(_Menu):
    """Land (row 0), climate (row 1, a region or bigger), ruler (row 2), how it holds it (row 3,
    once a ruler is set), then part, hide or show, and back (row 4). Stays open after each pick."""

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        name: str,
        held: Mapping[str, mf.Held],
        names: Mapping[str, str],
        groups: Sequence[tuple[str, str]],
        *,
        climate_here: bool,
        land_here: bool,
        hidden: bool,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.name = campaign_id, entity_id, name
        self.names = names
        self.land = self.climate = None
        if land_here:
            self.land = _Select(
                self._land_picked, placeholder="What's the land like?",
                options=land_options(held, names), row=0,
            )  # fmt: skip
            self.add_item(self.land)
        if climate_here:
            self.climate = _Select(
                self._climate_picked, placeholder="How hot or cold is it?",
                options=climate_options(held), row=1,
            )  # fmt: skip
            self.add_item(self.climate)
        options, self.left_out = ruler_options(held, names, groups)
        self.ruler = _Select(
            self._ruler_picked, placeholder="Who rules it?", options=options, row=2
        )
        self.add_item(self.ruler)
        rulers = _own(held, mf.CONTROL)
        self.ruler_id = rulers[0].value if rulers else None
        if self.ruler_id is not None:
            now = rulers[0].how or "rules"
            for how, label in HOW_BUTTONS.items():
                style = discord.ButtonStyle.success if how == now else discord.ButtonStyle.secondary
                self.add_item(_Button(self._how(how), label=label, style=style, row=3))
        lands = _own(held, mf.TERRAIN)
        self.land_value = lands[0].value if len(lands) == 1 else None
        if self.land_value is not None:
            partly = lands[0].how == "partly"
            self.add_item(_Button(self._part, label=ALL_BUTTON if partly else PART_BUTTON, row=4))
            self.partly = partly
        self.hidden = hidden
        self.add_item(_Button(self._shown, label=SHOW_BUTTON if hidden else HIDE_BUTTON, row=4))
        if self.left_out:
            self.add_item(_Button(self._type_group, label=TYPE_BUTTON, row=4))
        self.add_item(_Button(self._back, label=BACK_BUTTON, row=4))

    async def _set(
        self, interaction: discord.Interaction, layer: str, value: str | None, how: str, note: str
    ) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return
        try:
            await memory.set_map_fact(
                campaign.guild_id, campaign.id, self.entity_id, layer, value, how=how, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        await show_world(interaction, self.campaign_id, self.entity_id, note)

    async def _land_picked(self, interaction: discord.Interaction) -> None:
        assert self.land is not None
        value = self.land.values[0]
        name = f"**{_md(self.name)}**"
        if value == NOT_SURE:
            await self._set(interaction, mf.TERRAIN, None, "", f"🗺️ Noted: no land set for {name}.")
            return
        words = LAND[value][0].lower()
        await self._set(
            interaction, mf.TERRAIN, value, "mostly", f"🗺️ Noted: {name} is mostly {words}."
        )

    async def _climate_picked(self, interaction: discord.Interaction) -> None:
        assert self.climate is not None
        value = self.climate.values[0]
        name = f"**{_md(self.name)}**"
        if value == NOT_SURE:
            await self._set(interaction, mf.CLIMATE, None, "", f"🌡️ Noted: no climate for {name}.")
            return
        warmth, how = value.split(":", 1)
        words = WARMTH[warmth].lower() + (f" and {how}" if how else "")
        await self._set(
            interaction, mf.CLIMATE, warmth, how,
            f"🌡️ Noted: {name} is {words}. Places inside it are too, unless you set them.",
        )  # fmt: skip

    async def _ruler_picked(self, interaction: discord.Interaction) -> None:
        value = self.ruler.values[0]
        name = f"**{_md(self.name)}**"
        if value == NOT_SURE:
            await self._set(
                interaction, mf.CONTROL, None, "", f"👑 Noted: not sure who rules {name}."
            )
            return
        group = f"**{_md(self.names.get(value, ''))}**"
        await self._set(interaction, mf.CONTROL, value, "rules", f"👑 Noted: {group} rules {name}.")

    def _how(self, how: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        """A press handler for each way of holding it."""

        async def press(interaction: discord.Interaction) -> None:
            group = f"**{_md(self.names.get(self.ruler_id or '', ''))}**"
            await self._set(
                interaction, mf.CONTROL, self.ruler_id, how,
                f"👑 Noted: {group} {NOTED_HOW[how]} **{_md(self.name)}**.",
            )  # fmt: skip

        return press

    async def _part(self, interaction: discord.Interaction) -> None:
        how = "mostly" if self.partly else "partly"
        words = LAND[self.land_value or ""][0].lower() if self.land_value in LAND else ""
        await self._set(
            interaction, mf.TERRAIN, self.land_value, how,
            f"🗺️ Noted: **{_md(self.name)}** is {how} {words}.",
        )  # fmt: skip

    async def _shown(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return
        show = self.hidden
        try:
            await memory.show_map_facts(
                campaign.guild_id, campaign.id, self.entity_id, show, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        name = f"**{_md(self.name)}**"
        note = (
            f"👁️ Noted: players who can see {name} see its land, climate and ruler."
            if show
            else f"🙈 Noted: players can't see {name}'s land, climate or ruler."
        )
        await show_world(interaction, self.campaign_id, self.entity_id, note)

    async def _type_group(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            TypeGroupForm(self.campaign_id, self.entity_id, self.name)
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


NO_GROUP = "No group has that name. Check the spelling on the group's own card."


class TypeGroupForm(discord.ui.Modal, title="Who rules it?"):
    """The ruler by name, when the campaign has more groups than the menu shows."""

    group: discord.ui.TextInput[TypeGroupForm] = discord.ui.TextInput(
        label="The group's name", placeholder="Example: the Lords' Alliance", max_length=100
    )

    def __init__(self, campaign_id: str, entity_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.name = campaign_id, entity_id, name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # after the wait, as on a press
            await _tell(interaction, ONLY_DM)
            return
        typed = " ".join(self.group.value.split()).casefold()
        onto = await memory.ontology(campaign.guild_id, campaign.id)
        found = [
            e
            for e in await memory.entities(campaign.guild_id, campaign.id)
            if onto.is_a(e.type, "faction")
            and e.status == "confirmed"
            and " ".join(e.name.split()).casefold() == typed
        ]
        if len(found) != 1:
            await _tell(interaction, NO_GROUP)
            return
        try:
            await memory.set_map_fact(
                campaign.guild_id, campaign.id, self.entity_id, mf.CONTROL, found[0].id,
                how="rules", source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        note = f"👑 Noted: **{_md(found[0].name)}** rules **{_md(self.name)}**."
        await show_world(interaction, self.campaign_id, self.entity_id, note)
