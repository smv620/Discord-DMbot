"""📂 Sources on the Homebrew menu (#2285): where the campaign's homebrew came from, one line for
each place, and a menu to download what came from one. DMs only, like the Homebrew menu itself;
private; nothing here changes an entry.

Linked entries list their published pages' addresses (public pages, so safe to show), as many as
fit; the file for a group is in the same format as Download file, so it can be loaded again.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import fit_message
from dmbot.mentions import NO_PINGS
from dmbot.rules import homebrew_groups
from dmbot.rules.homebrew_groups import Group
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Menu, _Select, _tell

log = logging.getLogger(__name__)

SOURCES_LABEL = "📂 Sources"
NONE_YET = "No homebrew yet. Add some and its source shows up here."
NOT_READY = "Couldn't load sources. Press 📂 Sources to try again."
COULDNT = "Couldn't make that file. Pick it again to retry."
GONE = "That source is empty now. Press 📂 Sources for the new list."
INTRO = "📂 **Where your homebrew came from**"
OUTRO = "Pick one below to get its entries as a text file. Only you can see this."
PICK_PLACEHOLDER = "📥 Get entries from…"
PAGES_SHOWN = 5  # most page addresses listed under a linked site
LABEL_MAX = 25  # a phone cuts a longer menu label


async def _load(interaction: discord.Interaction, campaign: Campaign) -> list[Group]:
    """The groups as they are now: the campaign's own entries (off ones too, as Download file
    has them) and its followed packs'."""
    bot: Any = _bot(interaction)
    own = await bot.homebrew.list(campaign.guild_id, campaign.id) if bot.homebrew else []
    reader = bot.homebrew_reader
    seen = await reader.list(campaign.guild_id, campaign.id) if reader is not None else []
    return homebrew_groups.group([*own, *(e for e in seen if e.source)])


def _text(groups: list[Group]) -> str:
    lines: list[str] = []
    for g in groups:
        lines.append(f"• {discord.utils.escape_markdown(g.line)}")
        if g.kind == "link":
            lines += [f"   <{page}>" for page in g.pages[:PAGES_SHOWN]]
            if len(g.pages) > PAGES_SHOWN:
                lines.append(f"   …and {len(g.pages) - PAGES_SHOWN} more pages")
    return fit_message("\n".join([INTRO, *lines, "", OUTRO]))


class SourcesView(_Menu):
    def __init__(self, campaign: Campaign, groups: list[Group]) -> None:
        super().__init__()
        self.campaign = campaign
        self.keys = [g.key for g in groups]  # what each menu value stands for
        self.pick = _Select(
            self._download,
            placeholder=PICK_PLACEHOLDER,
            options=[
                discord.SelectOption(
                    label=g.title if len(g.title) <= LABEL_MAX else g.title[: LABEL_MAX - 1] + "…",
                    value=str(i),
                    description=f"{g.kind.capitalize()} · {len(g.entries)} "
                    f"entr{'ies' if len(g.entries) != 1 else 'y'}",
                )
                for i, g in enumerate(groups)
            ],
        )
        self.add_item(self.pick)

    async def _download(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)  # a new private message; the list stays as it is
        try:
            groups = await _load(interaction, self.campaign)
        except Exception:
            log.exception("Couldn't read the homebrew for a sources file")
            await _tell(interaction, COULDNT)
            return
        value = int(self.pick.values[0])
        wanted = self.keys[value] if 0 <= value < len(self.keys) else None
        source = next((g for g in groups if g.key == wanted), None)
        if source is None:  # the last entry of it was removed meanwhile
            await _tell(interaction, GONE)
            return
        name, text = homebrew_groups.download(
            self.campaign.name, self.campaign.target_ruleset, source
        )
        try:
            await interaction.followup.send(
                f"📥 **{discord.utils.escape_markdown(source.title)}**: {len(source.entries)} "
                f"entr{'ies' if len(source.entries) != 1 else 'y'}.",
                file=discord.File(io.BytesIO(text.encode()), filename=name),
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
        except discord.HTTPException:
            log.warning("Couldn't send a homebrew sources file", exc_info=True)
            await interaction.followup.send(COULDNT, ephemeral=True)


async def open_sources(interaction: discord.Interaction, campaign: Campaign) -> None:
    """The 📂 Sources button: a new private message."""
    await _answer_first(interaction)
    try:
        groups = await _load(interaction, campaign)
    except Exception:
        log.exception("Couldn't read the homebrew for the sources list")
        await _tell(interaction, NOT_READY)
        return
    if not groups:
        await _tell(interaction, NONE_YET)
        return
    await _tell(interaction, _text(groups), view=SourcesView(campaign, groups))
