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
MAX_PARTS = 2  # uploads named in one notice; the rest are counted together
NAME_SHOWN = 30  # an upload's name is cut to this many characters


def _plain(text: str) -> str:
    """One line with no markdown and no mention (an upload's name is the DM's own text)."""
    one_line = " ".join(text.split())
    return str(
        discord.utils.escape_markdown(discord.utils.escape_mentions(one_line), as_needed=False)
    )


def _short(text: str) -> str:
    plain = re.sub(r"<[^>]*>", "", _plain(text))  # (no channel, emoji or link markup)
    return plain if len(plain) <= NAME_SHOWN else plain[: NAME_SHOWN - 1].rstrip() + "…"


def notice(counts: Mapping[str, int]) -> str:
    """The text for the DM screen: the total first, then which uploads (at most two, each cut
    short) and what the button does."""
    total = sum(n for n in counts.values() if n > 0)
    if not total:
        return ""
    noun = "connection" if total == 1 else "connections"
    verb = "is" if total == 1 else "are"
    named = sorted(((k, n) for k, n in counts.items() if k and n > 0), key=lambda i: (-i[1], i[0]))
    if len(named) == 1 and named[0][1] == total:
        head = f"📜 {_short(named[0][0])}: {total} {noun} {verb} ready to check."
    else:
        shown = [f"{n} from {_short(k)}" for k, n in named[:MAX_PARTS]]
        rest = total - sum(n for _, n in named[:MAX_PARTS])
        if rest and shown:
            shown.append(f"{rest} more")
        head = f"📜 {total} {noun} {verb} ready to check" + (
            f" ({', '.join(shown)})." if shown else "."
        )
    return head + "\nPress the button to keep or skip each one. Nothing goes to players."


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
        from dmbot.ui.dmbot_commands import _tell  # (ui imports this package: late)
        from dmbot.ui.name_background import ONLY_DMS, ask_connections
        from dmbot.ui.names import _campaign_for

        # The DM screen can be seen by players (peek or open): only a DM of the campaign goes on,
        # checked on every press (#2651).
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        await ask_connections(interaction, self.campaign_id)


def view(campaign_id: str) -> discord.ui.View:
    out = discord.ui.View(timeout=None)
    out.add_item(ReadyButton(campaign_id))
    return out
