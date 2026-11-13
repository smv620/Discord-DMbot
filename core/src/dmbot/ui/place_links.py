"""The DM's own "next to" and "travel to" links between two places (#4173 slice 6d, the route menus;
Supervisor's yes and the ux-critic's words, 2026-11-13). From a place's 📏 Size and kind screen:
↔️ Next to… or 🛣️ Travel to… opens the usual name search; once another place is picked, one short
menu finishes a sentence that starts with the card's place ("Targos is… on the shore (lake)"), so a
one-way word is never saved the wrong way round. The kinds stay out of the full Connect to… menu
(25 choices). Saving is the usual `name_card.connect`: confirmed, one change, one Undo."""

from __future__ import annotations

import discord

from dmbot.memory import link_words, next_words, route_words
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _Button, _Menu, _Select
from dmbot.ui.names import _md

NEXT_TO, TRAVEL_TO = "next_to", route_words.ROUTE_TO
NEXT_TO_BUTTON = "↔️ Next to…"
TRAVEL_BUTTON = "🛣️ Travel to…"
NOT_A_PLACE = "**{name}** isn't a place. Type another name and pick a place."
ASK = "**{a}** is … **{b}**. Pick the words that fit."
ASK_WAY = "How do you get from **{a}** to **{b}**? Pick one."
NAME_IN_HINT = 32  # each name in an option's hint is cut to this, so a hint stays within 100
# (label, the stored word) in the order the menu shows them; "" is the plain kind
NEXT_CHOICES = (
    ("Next to", ""),
    ("Sharing a border with", next_words.BORDERS),
    ("On the shore (lake)", next_words.SHORE),
    ("On the coast (sea)", next_words.COAST),
    ("At the foot of", next_words.FOOT),
    ("On a road or river", next_words.ON),
)
TRAVEL_CHOICES = (
    ("By road", route_words.ROAD),
    ("By river", route_words.RIVER),
    ("By sea", route_words.SEA),
    ("By air", route_words.AIR),
    ("Through a portal", route_words.PORTAL),
    ("Across the wild", route_words.WILD),
    ("Not sure", ""),
)
NOT_SAID = "none"  # the menu's value for "" (an option's value can't be empty)


def asked(name: str, how: str) -> str:
    """The search form's question: "Targos is next to…" or "Travel from Targos to…"."""
    return f"Travel from {name} to…" if how == TRAVEL_TO else f"{name} is next to…"


def ask_words(a: str, b: str, how: str = NEXT_TO) -> str:
    """The message above the menu: the sentence it finishes, the card's place first."""
    return (ASK_WAY if how == TRAVEL_TO else ASK).format(a=_md(a), b=_md(b))


def hint(how: str, detail: str, a: str, b: str) -> str:
    """The option's description: the card's own sentence for it, with the card's place first and
    both names cut so it fits Discord's 100 characters."""
    a, b = logic.shorten(a, NAME_IN_HINT), logic.shorten(b, NAME_IN_HINT)
    if link_words.reads_both_ways(how, detail):
        words = f"{a} and {b} {link_words.pair_words(how, detail)}"
    else:
        words = f"{a} {link_words.link_words(how, detail)} {b}"
    return logic.shorten(words, 100)


def options(how: str, a: str, b: str) -> list[discord.SelectOption]:
    """The menu's choices, each with the card's own sentence for it as its hint."""
    choices = TRAVEL_CHOICES if how == TRAVEL_TO else NEXT_CHOICES
    return [
        discord.SelectOption(
            label=label, value=detail or NOT_SAID, description=hint(how, detail, a, b)
        )
        for label, detail in choices
    ]


class PlaceWords(_Menu):
    """How two places sit, or the way between them, picked after the other place. Saves nothing
    until a choice; ↩ Back saves nothing and goes back to the card."""

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        other_id: str,
        how: str,
        names: tuple[str, str],
        *,
        secret: bool = False,
        forced: bool = False,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.other_id, self.how = (
            campaign_id, entity_id, other_id, how,
        )  # fmt: skip
        self.secret, self.forced = secret, forced  # (checked again in `connect`)
        holder = f"Travel from {names[0]}…" if how == TRAVEL_TO else f"{names[0]} is…"
        self.pick = _Select(
            self._picked,
            placeholder=logic.shorten(holder, 150),
            options=options(how, names[0], names[1]),
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="↩ Back", row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import connect

        value = self.pick.values[0]
        self.stop()  # (stopped first: a second press does nothing)
        await connect(
            interaction, self.campaign_id, self.entity_id, self.how, self.other_id,
            secret=self.secret, forced=self.forced, detail="" if value == NOT_SAID else value,
        )  # fmt: skip

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)
