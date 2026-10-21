"""The name dropdown for 🧭 Connect to… and 🔗 Same as… on a name card (#2321).

After the DM says how two names are connected, they pick the other name from a list instead of
typing it. A switch orders the list **A to Z** or **Closest first**: nearest by connections from
this name (one connection away before two), then the rest A to Z. The list is paged, 25 to a
page, and **🔍 Type a name** keeps the old way for a campaign with many names. Nothing is
saved here: a pick goes through `choose_other`, the same step the typed search ends in.

Who sees which names: the names the viewer may see (`for_viewer`), and for the order only the
connections they may see (the DM's secret connections count only for a DM), so the order never
hints at a secret to a player.
"""

from __future__ import annotations

import discord

from dmbot.memory.closeness import alphabetical, by_closeness, distances
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import PERSON
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _Select
from dmbot.ui.name_card import (
    SAME,
    PickForm,
    _asked,
    _current,
    _kind,
    _names,
    choose_other,
    show_card,
)
from dmbot.ui.names import _md, sees_secrets

PAGE = 25  # what one Discord menu holds
CLOSEST = "closest"
AZ = "az"
# The button says what pressing it does; the message says what order the list is in now.
SORT_LABELS = {CLOSEST: "🔤 Sort A–Z", AZ: "🔗 Sort by closeness"}
SHOWING = {CLOSEST: "Showing nearest first.", AZ: "Showing A–Z."}
TYPE_LABEL = "🔍 Type a name"
NONE_YET = "There are no other names yet. Add one with ➕ Add a name on the names panel."


def candidates(names: CampaignLookup, entity_id: str) -> list[tuple[str, str]]:
    """`(id, name)` of every other confirmed name the viewer may see. The people at the table
    are not story names, so they are left out, as in a search."""
    return [
        (e.id, e.name)
        for e in names.entities.values()
        if e.id != entity_id and e.status == CONFIRMED and e.type != PERSON
    ]


def ordered(
    names: CampaignLookup, entity_id: str, mode: str, *, secrets: bool
) -> list[tuple[str, str]]:
    """The candidates in the chosen order. Closest first uses the connections the viewer may
    see: a DM's include secret ones, everyone else's only confirmed, public ones."""
    found = candidates(names, entity_id)
    if mode == AZ:
        return alphabetical(found)
    graph: dict[str, frozenset[str]] = dict(names.confirmed_neighbours)
    if secrets:  # a DM's order also counts the connections they keep secret
        for a, there in names.secret_neighbours.items():
            graph[a] = graph.get(a, frozenset()) | there
    return by_closeness(found, distances(graph, entity_id))


async def show_dropdown(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    how: str,
    *,
    secret: bool = False,
    mode: str = CLOSEST,
    page: int = 0,
    replacing: _Menu | None = None,
) -> None:
    """Draw the dropdown in place of the message that was pressed (or as a reply).
    `replacing`: the menu this takes over from; stopped once the new one is drawn, so a redraw
    that can't happen leaves the old one working."""
    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return
    campaign, name = found
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    entries = ordered(names, entity_id, mode, secrets=secrets)
    last = max((len(entries) - 1) // PAGE, 0)
    page = min(max(page, 0), last)
    shown = entries[page * PAGE : (page + 1) * PAGE]
    view = NameDropdown(
        campaign_id, entity_id, how, name, names, shown,
        secret=secret, mode=mode, page=page, last=last,
    )  # fmt: skip
    asked = f"**{_md(_asked(name, how))}**" + (" 🤫 Secret." if secret else "")
    text = (
        f"{asked} Pick the other name below. {SHOWING[mode]}" if shown else f"{asked}\n{NONE_YET}"
    )
    if mode == CLOSEST and shown and page == 0:
        text += f"\n_Nearest = joined to {_md(name)} by the fewest connections._"
    if last:
        text += f"\n_Page {page + 1} of {last + 1}. Too many to scroll? Press 🔍 Type a name._"
    await _replace(interaction, text, view)
    if replacing is not None:
        replacing.stop()


class NameDropdown(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        how: str,
        name: str,
        names: CampaignLookup,
        shown: list[tuple[str, str]],
        *,
        secret: bool,
        mode: str,
        page: int,
        last: int,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.how, self.name = campaign_id, entity_id, how, name
        self.secret, self.mode, self.page = secret, mode, page
        if shown:
            self.pick = _Select(
                self._picked,
                placeholder="Pick the same name…" if how == SAME else "Pick the name to connect…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(
                            f"{other_name} · {_kind(names.entities[other_id].kind_key)}",
                            logic.OPTION_LABEL_MAX,
                        ),
                        value=other_id,
                    )
                    for other_id, other_name in shown
                ],
            )
            self.add_item(self.pick)
        if page > 0:
            self.add_item(_Button(self._earlier, label="◀ Earlier", row=1))
        if page < last:
            self.add_item(_Button(self._later, label="Later ▶", row=1))
        if shown:  # nothing to order or type for when there are no other names
            self.add_item(_Button(self._switch, label=SORT_LABELS[mode], row=2))
            self.add_item(_Button(self._type, label=TYPE_LABEL, row=2))
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary, row=2))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await choose_other(
            interaction, self, self.pick.values[0], self.campaign_id, self.entity_id, self.how,
            secret=self.secret,
        )  # fmt: skip

    async def _again(
        self, interaction: discord.Interaction, *, mode: str | None = None, page: int | None = None
    ) -> None:
        # Not stopped first: if the redraw can't happen, this list keeps working.
        await show_dropdown(
            interaction, self.campaign_id, self.entity_id, self.how,
            secret=self.secret,
            mode=mode or self.mode,
            page=self.page if page is None else page,
            replacing=self,
        )  # fmt: skip

    async def _earlier(self, interaction: discord.Interaction) -> None:
        await self._again(interaction, page=self.page - 1)

    async def _later(self, interaction: discord.Interaction) -> None:
        await self._again(interaction, page=self.page + 1)

    async def _switch(self, interaction: discord.Interaction) -> None:
        # Back to the first page: the order changed, so the old page means nothing.
        await self._again(interaction, mode=AZ if self.mode == CLOSEST else CLOSEST, page=0)

    async def _type(self, interaction: discord.Interaction) -> None:
        if await _current(interaction, self.campaign_id, self.entity_id):
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(
                    self.campaign_id,
                    self.entity_id,
                    self.how,
                    _asked(self.name, self.how),
                    secret=self.secret,
                )
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)
