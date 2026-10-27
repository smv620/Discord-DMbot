"""The "📖 What I looked at" button under the DM's private rules answer (#1957), and the list it
opens: one line for each house rule or entry the answer was built from, each entry a button that
opens its `/dmbot rule` card (by the same "Name (kind)" value, so the right twin opens).

The list is kept by the sidebar (`sidebar.looked.Registry`, in memory) under a random key in the
button's id. Only the DM who asked may open it, and after a restart the key is gone, which the
button says in plain words. The buttons are registered at start-up (`DMBot.setup_hook`) so an old
answer's button still answers."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.sidebar import looked

log = logging.getLogger(__name__)

_KEY = r"(?P<key>[0-9a-f]{12})"


def view(key: str) -> discord.ui.View:
    """What goes under the answer: the one button."""
    built = discord.ui.View(timeout=None)
    built.add_item(LookedButton(key))
    return built


def _kept(interaction: discord.Interaction, key: str) -> looked.Kept | str:
    """The kept list if this person may open it, else the plain words to say."""
    sidebar = getattr(interaction.client, "sidebar", None)
    registry = getattr(sidebar, "looked", None)
    kept = registry.get(key) if registry is not None else None
    if not isinstance(kept, looked.Kept):
        return looked.GONE
    if interaction.user.id != kept.owner_id:
        return looked.NOT_YOURS
    return kept


class LookedButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:looked:{_KEY}",
):
    """📖 What I looked at: the list, privately, for the DM who asked."""

    def __init__(self, key: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=looked.BUTTON_LABEL,
                emoji=looked.BUTTON_EMOJI,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:looked:{key}",
            )
        )
        self.key = key

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LookedButton:
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        kept = _kept(interaction, self.key)
        if isinstance(kept, str):
            await interaction.response.send_message(kept, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        lines = discord.ui.View(timeout=None)
        shown = looked.button_labels(kept.items)
        buttons = [i for i, item in enumerate(kept.items) if item.value]  # a house rule has none
        for i in buttons[: looked.BUTTONS_MAX]:
            lines.add_item(OpenCard(self.key, i, shown[i]))
        await interaction.response.send_message(
            looked.text_of(kept.items),
            view=lines if lines.children else discord.utils.MISSING,
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class OpenCard(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:looked:open:{_KEY}:(?P<n>[0-9]{{1,2}})",
):
    """One line of the list as a button: that entry's `/dmbot rule` card, privately."""

    def __init__(self, key: str, n: int, label: str = "Open") -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:looked:open:{key}:{n}",
            )
        )
        self.key = key
        self.n = n

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenCard:
        return cls(match["key"], int(match["n"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.rule_lookup import look_up  # (rule_lookup imports the sidebar's neighbours)

        kept = _kept(interaction, self.key)
        if isinstance(kept, str):
            await interaction.response.send_message(kept, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        if self.n >= len(kept.items) or not kept.items[self.n].value:
            await interaction.response.send_message(
                looked.NO_CARD, ephemeral=True, allowed_mentions=NO_PINGS
            )
            return
        # The card is read again and the person checked again, as for any press of a button.
        await look_up(
            interaction,
            kept.campaign_id,
            kept.items[self.n].value,
            guild_id=kept.guild_id,
            paced=False,
        )
