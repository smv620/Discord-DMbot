"""Watch the story, on the DM screen (#1729; docs/STORY_MEMORY.md, section 6, "5d slice 5").

After a session, or when the DM sets a condition: if the adventure needs someone alive and the
table says they are dead, one short message on the DM screen, at most five lines. Each line has
**👍 I know** (it is not shown again) and **📋 Show the book's words** (the adventure's words,
only to the DM who pressed). Nothing says what to do: DMbot points and the DM decides. Only the
campaign's DMs may press, asked again every time; never in a transcript or any player channel.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.memory.story_watch import Clash, Found
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.names import _md, sees_secrets

log = logging.getLogger(__name__)

ONLY_DM = "Only the DM can do that."
KNOWN = "👍 Okay. DMbot won't mention that one again."
GONE = "That one has gone: the story moved on."
KNOW_LABEL = "👍 I know"
WORDS_LABEL = "📋 Show the book's words"


def line_text(clash: Clash, numbers: Mapping[int, int]) -> str:
    """One clash in plain words: what the adventure needs and what the table says."""
    number = numbers.get(clash.session_started_at) if clash.session_started_at is not None else None
    when = f" (Session {number})" if number is not None else ""
    return (
        f"📖 Your adventure: **{_md(clash.book_name)}** needs **{_md(clash.book_needs)}** "
        f"{_md(clash.need)}. At your table **{_md(clash.table_name)}** is {clash.condition}{when}."
    )


def message_text(found: Found, numbers: Mapping[int, int]) -> str:
    many = len(found.shown) > 1
    lines = [
        f"{n}. {line_text(c, numbers)}" if many else line_text(c, numbers)
        for n, c in enumerate(found.shown, start=1)
    ]
    if found.more:
        lines.append(f"+{found.more} more")
    return "\n".join(lines)


class KnowButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:watch:know:(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, campaign_id: str, divergence_id: str, number: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"{KNOW_LABEL} ({number})" if number else KNOW_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:watch:know:{campaign_id}:{divergence_id}",
                row=max(number - 1, 0),
            )
        )
        self.campaign_id, self.divergence_id = campaign_id, divergence_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KnowButton:
        return cls(match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        got = await _dm_and_watch(interaction, self.campaign_id)
        if got is None:
            return
        campaign, watch = got
        done = await watch.know(campaign.guild_id, campaign.id, self.divergence_id)
        await _tell(interaction, KNOWN if done else GONE)


class WordsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:watch:words:(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, campaign_id: str, divergence_id: str, number: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"{WORDS_LABEL} ({number})" if number else WORDS_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:watch:words:{campaign_id}:{divergence_id}",
                row=max(number - 1, 0),
            )
        )
        self.campaign_id, self.divergence_id = campaign_id, divergence_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WordsButton:
        return cls(match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        got = await _dm_and_watch(interaction, self.campaign_id)
        if got is None:
            return
        campaign, watch = got
        words = await watch.words(campaign.guild_id, campaign.id, self.divergence_id)
        await _tell(interaction, GONE if words is None else f"📖 “{_md(words)}”")


async def _dm_and_watch(interaction: discord.Interaction, campaign_id: str) -> Any:
    """The campaign and the watch, only for a DM of that campaign (asked again every press)."""
    guild = interaction.guild
    watch = getattr(_bot(interaction), "watch", None)
    if guild is None or watch is None:
        await _tell(interaction, GONE)
        return None
    campaign = await _bot(interaction).campaigns.get(guild.id, campaign_id)
    if campaign is None or not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    return campaign, watch


def watch_view(campaign_id: str, found: Found) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    many = len(found.shown) > 1
    for n, clash in enumerate(found.shown, start=1):
        number = n if many else 0
        view.add_item(KnowButton(campaign_id, clash.id, number))
        view.add_item(WordsButton(campaign_id, clash.id, number))
    return view


__all__ = ["KnowButton", "WordsButton", "message_text", "watch_view"]
