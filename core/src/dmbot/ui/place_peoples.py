"""A place's culture and who lives there, on the DM's own screen (#4257, MapBot layers slice 3a;
Supervisor's ruling and the ux-critic's words on #4257). Culture is whose ways a place shows;
who lives there is who is there now: a dwarven town can be mostly human. Each says a lineage
("dwarven", "halflings") or the DM's own words, never a number; several sit side by side. The
words never say "people" (an entry kind), "species" or "lineage". For the campaign's DMs only,
checked on every press. Nothing here is read up from a bigger place."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import gate, place_peoples
from dmbot.memory import map_facts as mf
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _Select, _tell
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets
from dmbot.ui.place_world import (
    BACK_BUTTON,
    KEEP_BUTTON,
    NOT_SURE,
    PEOPLES_BUTTON,
    READ_MARK,
    STRENGTH,
    culture_words,
    people_words,
)

log = logging.getLogger(__name__)

BUTTON = PEOPLES_BUTTON
ONLY_DM = "Only the DM can change a place's culture or who lives there."
SOMETHING_ELSE = "other"  # a menu's value for "Something else…" (the DM types the words)
HELP = (
    "Culture = whose ways it shows (buildings, customs). Who lives here = who's there now. "
    "A dwarven town can be mostly humans."
    " To remove one, pick Not sure and set the rest again."
)
ONCE_HINT = "Once = only what they left behind, like ruins."
# This screen's hide/show covers every layer of the place (`show_map_facts`), so it says "all":
# a DM hiding a ruler mustn't show it by a press on the culture screen (ux-critic, #4257)
SHOW_ALL = "👁️ Show all to players"
HIDE_ALL = "🙈 Hide all from players"
STRENGTH_BUTTONS = {"strong": "Strongly", "some": "Somewhat", "traces": "A little", "once": "Once"}
SHARE_LABELS = {"all": "Almost all", "most": "Most", "many": "Many", "some": "Some", "few": "A few"}
Handler = Callable[[discord.Interaction], Awaitable[None]]


def _own(held: Mapping[str, mf.Held], layer: str) -> list[mf.Fact]:
    h = held.get(layer)
    return list(h.facts) if h is not None and h.from_id is None else []


def _focus(facts: Sequence[mf.Fact], value: str | None) -> mf.Fact | None:
    """The one the buttons and the second menu act on: the one just picked, else the DM's own
    first, else the first. Never a part's row ("the gnomish quarter"): the DM's set acts on the
    whole place, so a part's row is shown, kept or cleared, never reshaded (#4257)."""
    facts = [f for f in facts if not f.part]
    if value is not None:
        for f in facts:
            if f.value == value:
                return f
    if not facts:
        return None
    return min(facts, key=lambda f: f.status != mf.CONFIRMED)


def culture_options(focus: mf.Fact | None) -> list[discord.SelectOption]:
    chosen = focus.value if focus is not None else None
    out = [
        discord.SelectOption(label=adjective.capitalize(), value=key, default=key == chosen)
        for key, (adjective, _) in place_peoples.LINEAGES.items()
    ]
    out.append(
        discord.SelectOption(
            label="Something else…", value=SOMETHING_ELSE, default=chosen == mf.OTHER
        )
    )
    out.append(
        discord.SelectOption(
            label="Not sure", description="Clears all set here", value=NOT_SURE, default=not chosen
        )
    )
    return out


def people_options(focus: mf.Fact | None) -> list[discord.SelectOption]:
    chosen = focus.value if focus is not None else None
    out = [
        discord.SelectOption(label=plural.capitalize(), value=key, default=key == chosen)
        for key, (_, plural) in place_peoples.LINEAGES.items()
    ]
    out.append(
        discord.SelectOption(
            label="Someone else…", value=SOMETHING_ELSE, default=chosen == mf.OTHER
        )
    )
    out.append(
        discord.SelectOption(
            label="Not sure", description="Clears all set here", value=NOT_SURE, default=not chosen
        )
    )
    return out


def _how_many(focus: mf.Fact | None) -> str:
    """ "How many here are halflings?": names the people picked, so it can't read as a total."""
    if focus is None:
        return "How many live here?"
    return f"How many here are {place_peoples.words_of(focus.value, focus.words, plural=True)}?"[
        :150
    ]


def share_options(focus: mf.Fact) -> list[discord.SelectOption]:
    out = [
        discord.SelectOption(label=label, value=how, default=focus.how == how)
        for how, label in SHARE_LABELS.items()
    ]
    out.append(
        discord.SelectOption(
            label="Not sure how many", description="DMbot won't guess", value=NOT_SURE,
            default=not focus.how,
        )
    )  # fmt: skip
    return out


def screen_text(name: str, held: Mapping[str, mf.Held], *, hidden: bool, note: str = "") -> str:
    lines = [note, ""] if note else []
    lines.append(f"🏛️ **{_md(name)}: culture and who lives here**")
    now: list[str] = []
    culture, people = _own(held, mf.CULTURE), _own(held, mf.POPULATION)
    if culture:
        words = ", ".join(dict.fromkeys(culture_words(f) for f in culture))
        mark = READ_MARK if any(f.status == mf.READ for f in culture) else ""
        now.append(f"🏛️ **Culture:** {_md(words)}{mark}")
    if people:
        words = ", ".join(dict.fromkeys(people_words(f) for f in people))
        mark = READ_MARK if any(f.status == mf.READ for f in people) else ""
        now.append(f"🏘️ **Who lives here:** {_md(words)}{mark}")
    lines.append("Now: " + " · ".join(now) if now else "Nothing set yet.")
    lines.append(HELP)
    if culture:
        lines.append(ONCE_HINT)
    lines.append(
        "Hidden from players, with its land, climate and ruler."
        if hidden
        else "Players who can see this place see all of it: culture, who lives here, land, climate"
        " and ruler. Tap 🙈 to hide."
    )
    return "\n".join(lines)


async def show_peoples(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    note: str = "",
    *,
    culture: str | None = None,
    people: str | None = None,
) -> None:
    """Replace the card with the culture and who-lives-here screen. `culture` and `people`: the
    lineage just picked, so its strength or share is the one the buttons change."""
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
    own = await memory.map_facts(campaign.guild_id, campaign.id, [entity_id], gate.Viewer.the_dm())
    hidden = not any(f.table_knows for f in own.get(entity_id, ()))
    text = screen_text(place.name, held, hidden=hidden, note=note)
    view = PeoplesMenu(
        campaign_id, entity_id, place.name, held, hidden=hidden, culture=culture, people=people
    )
    await _replace(interaction, text, view)


class PeoplesMenu(_Menu):
    """Culture (row 0), how strong (row 1, once a culture is set), who lives here (row 2), how many
    (row 3, once someone is set), then hide or show, keep, and back (row 4)."""

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        name: str,
        held: Mapping[str, mf.Held],
        *,
        hidden: bool,
        culture: str | None = None,
        people: str | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.name = campaign_id, entity_id, name
        cultures, peoples = _own(held, mf.CULTURE), _own(held, mf.POPULATION)
        self.culture = _focus(cultures, culture)
        self.people = _focus(peoples, people)
        self.culture_pick = _Select(
            self._culture_picked, placeholder="Whose culture is it?",
            options=culture_options(self.culture), row=0,
        )  # fmt: skip
        self.add_item(self.culture_pick)
        if self.culture is not None:
            for how, label in STRENGTH_BUTTONS.items():
                style = (
                    discord.ButtonStyle.success
                    if how == self.culture.how
                    else discord.ButtonStyle.secondary
                )
                self.add_item(_Button(self._strength(how), label=label, style=style, row=1))
        self.people_pick = _Select(
            self._people_picked, placeholder="Who lives here?",
            options=people_options(self.people), row=2,
        )  # fmt: skip
        self.add_item(self.people_pick)
        if self.people is not None:
            self.share_pick: _Select | None = _Select(
                self._share_picked, placeholder=_how_many(self.people),
                options=share_options(self.people), row=3,
            )  # fmt: skip
            self.add_item(self.share_pick)
        else:
            self.share_pick = None
        self.hidden = hidden
        self.has_own = bool(cultures or peoples)
        self.add_item(_Button(self._shown, label=SHOW_ALL if hidden else HIDE_ALL, row=4))
        if any(f.status == mf.READ for f in (*cultures, *peoples)):
            self.add_item(
                _Button(self._keep, label=KEEP_BUTTON, style=discord.ButtonStyle.success, row=4)
            )
        self.add_item(_Button(self._back, label=BACK_BUTTON, row=4))

    async def _allowed(
        self, interaction: discord.Interaction
    ) -> tuple[Campaign, MemoryStore] | None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return None
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return None
        return campaign, memory

    async def set(
        self,
        interaction: discord.Interaction,
        layer: str,
        value: str | None,
        how: str,
        note: str,
        words: str = "",
    ) -> None:
        """One change of the DM's (one Undo), then the screen again with that lineage in focus."""
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
                campaign.guild_id, campaign.id, self.entity_id, layer, value, how=how,
                words=words, source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        focus = {"culture": value} if layer == mf.CULTURE else {"people": value}
        await show_peoples(interaction, self.campaign_id, self.entity_id, note, **focus)

    def _named(self) -> str:
        return f"**{_md(self.name)}**"

    def culture_note(self, value: str | None, how: str, words: str = "") -> str:
        if value is None:
            return f"🏛️ Noted: {self._named()}'s culture is left open."
        said = culture_words(_fact(mf.CULTURE, value, how, words))
        return f"🏛️ Noted: {self._named()}'s culture is {_md(said)}."

    def people_note(self, value: str | None, how: str, words: str = "") -> str:
        if value is None:
            return f"🏘️ Noted: who lives in {self._named()} is left open."
        said = people_words(_fact(mf.POPULATION, value, how, words))
        return f"🏘️ Noted: who lives in {self._named()} is {_md(said)}."

    async def _culture_picked(self, interaction: discord.Interaction) -> None:
        value = self.culture_pick.values[0]
        if value == SOMETHING_ELSE:
            await interaction.response.send_modal(OwnWordsForm(self, mf.CULTURE))
            return
        if value == NOT_SURE:
            await self.set(interaction, mf.CULTURE, None, "", self.culture_note(None, ""))
            return
        same = self.culture is not None and self.culture.value == value
        how = self.culture.how if same and self.culture is not None else "strong"
        await self.set(interaction, mf.CULTURE, value, how, self.culture_note(value, how))

    def _strength(self, how: str) -> Handler:
        async def press(interaction: discord.Interaction) -> None:
            focus = self.culture
            if focus is None:  # (the buttons only show with a culture: a stale screen)
                await show_peoples(interaction, self.campaign_id, self.entity_id)
                return
            await self.set(
                interaction, mf.CULTURE, focus.value, how,
                self.culture_note(focus.value, how, focus.words), focus.words,
            )  # fmt: skip

        return press

    async def _people_picked(self, interaction: discord.Interaction) -> None:
        value = self.people_pick.values[0]
        if value == SOMETHING_ELSE:
            await interaction.response.send_modal(OwnWordsForm(self, mf.POPULATION))
            return
        if value == NOT_SURE:
            await self.set(interaction, mf.POPULATION, None, "", self.people_note(None, ""))
            return
        same = self.people is not None and self.people.value == value
        how = self.people.how if same and self.people is not None else ""
        await self.set(interaction, mf.POPULATION, value, how, self.people_note(value, how))

    async def _share_picked(self, interaction: discord.Interaction) -> None:
        focus = self.people
        if focus is None or self.share_pick is None:
            await show_peoples(interaction, self.campaign_id, self.entity_id)
            return
        picked = self.share_pick.values[0]
        how = "" if picked == NOT_SURE else picked
        await self.set(
            interaction, mf.POPULATION, focus.value, how,
            self.people_note(focus.value, how, focus.words), focus.words,
        )  # fmt: skip

    async def _shown(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.place_world import NOTHING_TO_SHOW

        await _answer_first(interaction, in_place=True)
        allowed = await self._allowed(interaction)
        if allowed is None:
            return
        campaign, memory = allowed
        if not self.has_own:  # (nothing of its own to show: never claim it changed)
            await show_peoples(interaction, self.campaign_id, self.entity_id, NOTHING_TO_SHOW)
            return
        show = self.hidden
        try:  # (one 🙈 / 👁️ for every layer of the place, as on the 🗺️ screen)
            await memory.show_map_facts(
                campaign.guild_id, campaign.id, self.entity_id, show, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"👁️ Noted: players who can see {self._named()} now see everything you set for it, "
            "its land, climate and ruler too."
            if show
            else f"🙈 Noted: players can't see anything you set for {self._named()}, "
            "its land, climate and ruler included."
        )
        await show_peoples(interaction, self.campaign_id, self.entity_id, note)

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        allowed = await self._allowed(interaction)
        if allowed is None:
            return
        campaign, memory = allowed
        try:  # every one DMbot read becomes the DM's word, in one change (one Undo)
            await memory.keep_map_facts(
                campaign.guild_id, campaign.id, self.entity_id, source=DM,
                layers=(mf.CULTURE, mf.POPULATION),
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"✅ Noted: everything DMbot read about {self._named()} is right, "
            "its land, climate and ruler too."
        )
        await show_peoples(interaction, self.campaign_id, self.entity_id, note)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


def _fact(layer: str, value: str, how: str, words: str) -> mf.Fact:
    """A stand-in row for the words of a confirmation."""
    return mf.Fact("", "", layer, value, words, how, "", mf.CONFIRMED, mf.FROM_DM, False)


class OwnWordsForm(discord.ui.Modal):
    """ "Something else…": the DM's own words for a culture or who lives there ("duergar")."""

    def __init__(self, menu: PeoplesMenu, layer: str) -> None:
        culture = layer == mf.CULTURE
        super().__init__(title="Whose culture?" if culture else "Who lives here?")
        self.menu, self.layer = menu, layer
        self.words: discord.ui.TextInput[OwnWordsForm] = discord.ui.TextInput(
            label="Its name, like “duergar”"
            if culture
            else "Their name, like “duergar” or “half-elves”",
            max_length=mf.WORDS_MAX,
        )
        self.add_item(self.words)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        words = " ".join(str(self.words.value or "").split())
        if not words or not words.isprintable():
            await _tell(interaction, "Type a name, like “duergar”.")
            return
        menu = self.menu
        if self.layer == mf.CULTURE:
            how = "strong"
            note = menu.culture_note(mf.OTHER, how, words)
        else:
            how = ""
            note = menu.people_note(mf.OTHER, how, words)
        await menu.set(interaction, self.layer, mf.OTHER, how, note, words)


__all__ = ["BUTTON", "ONLY_DM", "STRENGTH", "PeoplesMenu", "show_peoples"]
