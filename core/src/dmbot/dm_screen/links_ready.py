"""The DM-screen notice that waiting backstory connections became ready (#2650): "3 connections
from Tavin's backstory are ready to check", once, with a 📜 button that opens the Keep cards.

A connection waits until both its names are answered. Names are answered in many places (a name's
card, Add all, the website), so the bot watches for any change to a campaign's names and asks the
queue what became ready since it last said so (`BackstoryLinks.take_ready_notices`, which marks
them told). The notice holds counts and the upload's name and date, which the DM typed
themselves; never a name from the text or a quote.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import discord

_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Check the connections"
EMOJI = "📜"
MAX_PARTS = 5  # uploads named in one notice; the rest are counted together


def _plain(text: str) -> str:
    """One line with no markdown and no mention (an upload's name is the DM's own text)."""
    one_line = " ".join(text.split())
    return discord.utils.escape_markdown(discord.utils.escape_mentions(one_line), as_needed=False)


def notice(counts: Mapping[str, int]) -> str:
    """The line for the DM screen: one clause for each upload, most connections first."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    named = [(label, n) for label, n in ordered if label and n > 0]
    old = sum(n for label, n in counts.items() if not label)  # queued before uploads had names
    parts = [
        f"{n} connection{'' if n == 1 else 's'} from {_plain(label)}"
        for label, n in named[:MAX_PARTS]
    ]
    rest = sum(n for _, n in named[MAX_PARTS:]) + old
    if rest:
        parts.append(f"{rest} more connection{'' if rest == 1 else 's'}")
    total = sum(counts.values())
    if not parts:
        return ""
    return f"📜 {_join(parts)} {'is' if total == 1 else 'are'} ready to check."


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class ReadyButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:linksready:{_ID}",
):
    """📜 Check the connections: opens the Keep cards, privately. For the campaign's DMs."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                emoji=EMOJI,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:linksready:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReadyButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.name_background import ask_connections  # (ui imports this package: late)

        await ask_connections(interaction, self.campaign_id)


def view(campaign_id: str) -> discord.ui.View:
    out = discord.ui.View(timeout=None)
    out.add_item(ReadyButton(campaign_id))
    return out
