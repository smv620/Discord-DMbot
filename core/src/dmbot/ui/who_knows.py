"""The DM's "Who knows this?" page, shared by a connection and an other name (#2935): the three
answers (🤫 Only the DM, 👥 Only some characters, 👁️ Everyone at the table), the menu of the
campaign's player characters, and the words that say where the thing stands now.

The page for each kind of thing (`connection_edit.WhoKnows`, `name_card.WhoKnowsName`) adds what it
writes and where Back goes; every press there checks the campaign, the DM and the thing again.
"""

from __future__ import annotations

from collections.abc import Sequence

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import knowers, link_edits
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _Button, _Menu, _Select
from dmbot.ui.names import _md

MAX_PICK = 25  # one Discord menu holds this many; a table's characters are far fewer


async def player_characters(memory: MemoryStore, campaign: Campaign) -> list[tuple[str, str]]:
    """The campaign's confirmed player characters (id, name), by name, at most one menu's worth:
    the only ones the DM may say know something (#2935)."""
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    found = {
        e.id: e.name
        for e in lookup.entities.values()
        if e.role == PLAYER_CHARACTER and e.status == CONFIRMED and not e.merged_into
    }
    ordered = sorted(found.items(), key=lambda item: item[1].casefold())
    return ordered[:MAX_PICK]


def called(names: Sequence[str]) -> str:
    """ "Mara", "Mara and Tavin", "Mara, Tavin and Brann", escaped for Discord."""
    shown = [_md(n) for n in names]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def who_text(
    said: str,
    who: str,
    knowing: Sequence[str],
    names: dict[str, str],
    *,
    has_characters: bool,
    thing: str = "connection",
) -> str:
    """The page for "Who knows this?" (#2935): where the connection stands now, first, then the
    connection and how to change it. Players only ever see it if they can see the connection
    (Everyone); "known" here is about what the DM lets players see, not what the story says."""
    if who == link_edits.EVERYONE:
        now = f"**Players can see this {thing}.**"
    elif who == link_edits.SOME_CHARACTERS:
        mine = sorted(names[c] for c in knowing if c in names)
        now = (
            f"**Hidden from the party. Only {called(mine)} {'knows' if len(mine) == 1 else 'know'}"
            " it (and the DM).**"
            if mine
            else "**Hidden from the party. Only some characters know it (and the DM).**"
        )
    else:
        now = "**Hidden from players. Only the DM can see it.**"
    how = (
        "To pick characters, choose them in the list, then press 👥 Only some characters."
        if has_characters
        else "There are no player characters yet, so only 🤫 and 👁️ are offered."
    )
    return f"{now}\n{said}\nWho should see it? 🤫 Only the DM · 👁️ Everyone at the table. {how}"


class WhoKnowsMenu(_Menu):
    """🤫 Only the DM, 👥 Only some characters (a menu to pick them), 👁️ Everyone at the table. The
    owner of the page says what each answer writes (`_set`) and where Back goes (`_back`)."""

    def __init__(
        self, pcs: Sequence[tuple[str, str]], *, chosen: Sequence[str] = (), now: str = ""
    ) -> None:
        super().__init__()
        self.now = now
        self.opened_with = sorted(chosen)  # who knew it when this page opened
        self.names = dict(pcs)
        self.picked: list[str] = list(chosen)
        grey = discord.ButtonStyle.secondary
        green = discord.ButtonStyle.success  # where it stands now
        if self.names:
            self.pick = _Select(
                self._picked,
                placeholder="Pick who knows it…",
                min_values=1,
                max_values=min(len(self.names), knowers.MAX_KNOWERS),
                options=[
                    discord.SelectOption(
                        label=logic.shorten(name, logic.OPTION_LABEL_MAX),
                        value=entity_id,
                        default=entity_id in self.picked,
                    )
                    for entity_id, name in self.names.items()
                ],
                row=0,
            )
            self.add_item(self.pick)
        self.add_item(
            _Button(
                self._only_me, label="🤫 Only the DM", row=1,
                style=green if now == link_edits.ONLY_THE_DM else grey,
            )
        )  # fmt: skip
        if self.names:
            self.some = _Button(
                self._some, label="👥 Only some characters", row=1,
                style=green if now == link_edits.SOME_CHARACTERS else grey,
            )  # fmt: skip
            self.some.disabled = not self.picked  # nothing to save until someone is picked
            self.add_item(self.some)
        self.add_item(
            _Button(
                self._everyone, label="👁️ Everyone at the table", row=1,
                style=green if now == link_edits.EVERYONE else grey,
            )
        )  # fmt: skip
        self.add_item(_Button(self._back, label="Back", style=grey, row=2))

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.picked = list(self.pick.values)
        for option in self.pick.options:  # the page shows what is picked until 👥 is pressed
            option.default = option.value in self.picked
        self.some.disabled = not self.picked
        await interaction.response.edit_message(view=self)

    async def _only_me(self, interaction: discord.Interaction) -> None:
        await self._set(interaction, link_edits.ONLY_THE_DM)

    async def _some(self, interaction: discord.Interaction) -> None:
        await self._set(interaction, link_edits.SOME_CHARACTERS)

    async def _everyone(self, interaction: discord.Interaction) -> None:
        await self._set(interaction, link_edits.EVERYONE)

    async def _set(self, interaction: discord.Interaction, who: str) -> None:
        raise NotImplementedError

    async def _back(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError
