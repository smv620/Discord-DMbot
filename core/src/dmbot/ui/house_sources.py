"""📂 Sources on the house rules list (#2285): where the campaign's rules came from, one line for
each place, and a menu to download what came from one. Private; anyone who may read the list may
look and download, as with the list itself. Nothing here changes a rule.

The linked file also gets **🔗 Open the file** when its address fits a button. That address is
already shown in the table's chat when an answer comes from a house rule (owner, 2026-10-14), so
the list adds no new reveal.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.mentions import NO_PINGS
from dmbot.rules import house_sources
from dmbot.rules.house_sources import Source
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Menu,
    _Select,
    _tell,
)

log = logging.getLogger(__name__)

SOURCES_LABEL = "📂 Sources"
OPEN_LABEL = "🔗 Open the file"
NONE_YET = "No house rules yet, so there are no sources to show."
NOT_READY = "Sources aren't available right now. Please try again in a moment."
COULDNT = "Couldn't make that file just now. Try again in a moment."
GONE = "That source has no rules any more. Press 📂 Sources again to see the list."
INTRO = "📂 **Where your house rules came from**"
OUTRO = (
    "Pick one below to download its rules as a text file. Only you can see this message. "
    "An uploaded file itself isn't kept: the rules come from DMbot's copy."
)
PICK_PLACEHOLDER = "📥 Download the rules from…"
LINK_BUTTON_MAX = 512  # what Discord allows in a link button


async def _load(interaction: discord.Interaction, campaign: Campaign) -> tuple[list[Source], str]:
    """The sources as they are now, and the linked file's address ("" when it can't be a
    button)."""
    bot: Any = _bot(interaction)
    rules = await bot.all_house_rules(campaign.guild_id, campaign.id)
    publishers: dict[str, str] = {}
    if bot.rule_sets is not None:
        try:
            follows = await bot.rule_sets.following(campaign.guild_id, campaign.id)
            publishers = {f.name: f.publisher for f in follows if f.kind == "house_rules"}
        except Exception:
            log.exception("Couldn't read the followed sets for the sources list")
    link = ""
    links = getattr(bot, "house_file_links", None)
    if links is not None and any(r.source == "linked" for r in rules):
        try:
            found = await links.get(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Couldn't read the linked file for the sources list")
            found = None
        if (
            found is not None
            and found.link.startswith("https://")
            and len(found.link) <= LINK_BUTTON_MAX
        ):
            link = found.link
    return house_sources.group(rules, publishers), link


def _text(sources: list[Source]) -> str:
    lines = [f"• {discord.utils.escape_markdown(s.line)}" for s in sources]
    return "\n".join([INTRO, *lines, "", OUTRO])


class SourcesView(_Menu):
    def __init__(self, campaign: Campaign, sources: list[Source], link: str) -> None:
        super().__init__()
        self.campaign = campaign
        options = [
            discord.SelectOption(
                label=s.title[:100],
                value=s.key[:100],
                description=f"{len(s.rules)} rule{'s' if len(s.rules) != 1 else ''}",
            )
            for s in sources
        ]
        self.pick = _Select(self._download, placeholder=PICK_PLACEHOLDER, options=options)
        self.add_item(self.pick)
        if link:
            self.add_item(discord.ui.Button(label=OPEN_LABEL, url=link, row=1))

    async def _download(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)  # a new private message; the list stays as it is
        sources, _ = await _load(interaction, self.campaign)
        source = next((s for s in sources if s.key[:100] == self.pick.values[0]), None)
        if source is None:  # the last rule of it was removed meanwhile
            await _tell(interaction, GONE)
            return
        name, text = house_sources.download(self.campaign.name, source)
        try:
            await interaction.followup.send(
                f"📥 **{discord.utils.escape_markdown(source.title)}**: {len(source.rules)} "
                f"rule{'s' if len(source.rules) != 1 else ''}.",
                file=discord.File(io.BytesIO(text.encode()), filename=name),
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
        except discord.HTTPException:
            log.warning("Couldn't send a sources file", exc_info=True)
            await interaction.followup.send(COULDNT, ephemeral=True)


async def open_sources(interaction: discord.Interaction, campaign: Campaign) -> None:
    """The 📂 Sources button: a new private message, for anyone who may read the list."""
    await _answer_first(interaction)
    try:
        sources, link = await _load(interaction, campaign)
    except Exception:
        log.exception("Couldn't read the house rules for the sources list")
        await _tell(interaction, NOT_READY)
        return
    if not sources:
        await _tell(interaction, NONE_YET)
        return
    view = SourcesView(campaign, sources, link)
    await _tell(interaction, _text(sources), view=view)
