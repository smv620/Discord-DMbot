"""📋 More lists (#4103; Supervisor's ruling, 2026-11-10): one button on the Names home for the DM's
after-session lists that have no room of their own there (every row is full): 👣 Met but not named
and 🤫 Private scenes. Each list shows only while its switch is on, with how many wait; the button
itself is hidden while both switches are off. DM-only, checked on every press (each list checks
again when it opens).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from dmbot.campaigns.models import Campaign
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _replace, _tell
from dmbot.ui.names import NOT_AVAILABLE, _campaign_for, _memory, sees_secrets

log = logging.getLogger(__name__)

TITLE = "📋 More lists"
BUTTON = "📋 More lists"  # (its label with nothing waiting; the site's words name it)
INTRO = "Lists DMbot keeps for you after each session. Only DMs see them."
DM_ONLY = "Only a DM of this campaign can see these lists."


@dataclass(frozen=True, slots=True)
class Counts:
    """How many wait on each list; None for a list whose switch is off (not shown)."""

    met: int | None = None
    scenes: int | None = None

    @property
    def shown(self) -> bool:
        return self.met is not None or self.scenes is not None

    @property
    def total(self) -> int:
        return (self.met or 0) + (self.scenes or 0)


def _shown_count(n: int) -> str:
    return str(n) if n < 100 else "99+"


def button_label(counts: Counts) -> str:
    """The Names home's button: how many wait across the lists, when any do. Pure."""
    return f"{BUTTON} ({_shown_count(counts.total)})" if counts.total else BUTTON


def scenes_label(n: int) -> str:
    """Pure."""
    return f"🤫 Private scenes ({_shown_count(n)})"


async def count(interaction: discord.Interaction, campaign: Campaign) -> Counts:
    """How many wait on each list whose switch is on, read after the consent re-check (nothing is
    changed by counting). A list that can't be counted shows with 0: it still opens."""
    settings = _bot(interaction).settings
    met_on = bool(getattr(settings, "met_unnamed_on", False))
    scenes_on = bool(getattr(settings, "private_scenes_on", False))
    memory = _memory(interaction)
    if memory is None or not (met_on or scenes_on):
        return Counts()
    met = scenes = None
    try:
        agreed = await _bot(interaction).consent.consenting(campaign.guild_id)
    except Exception:
        log.exception("Couldn't read who is recorded for the lists' counts")
        agreed = frozenset()  # (nobody: every list counts 0, and still opens)
    if met_on:
        from dmbot.memory.met_store import MetStore

        try:
            met = await MetStore(memory._db).count(campaign.guild_id, campaign.id, opted_in=agreed)
        except Exception:
            log.exception("Couldn't count the met-but-not-named list")
            met = 0
    if scenes_on:
        from dmbot.memory.private_scene_store import PrivateScenes

        try:
            scenes = len(await PrivateScenes(memory._db).listed(
                campaign.guild_id, campaign.id, agreed))  # fmt: skip
        except Exception:
            log.exception("Couldn't count the private scenes")
            scenes = 0
    return Counts(met, scenes)


async def show(interaction: discord.Interaction, campaign_id: str) -> None:
    """The menu of lists, for a DM of the campaign now."""
    await _answer_first(interaction)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, DM_ONLY)
        return
    counts = await count(interaction, campaign)
    if not counts.shown:
        await _tell(interaction, "These lists are not turned on yet.")
        return
    await _replace(interaction, f"**{TITLE}**\n{INTRO}", MoreLists(campaign.id, counts))


class MoreLists(_Menu):
    def __init__(self, campaign_id: str, counts: Counts) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        grey = discord.ButtonStyle.secondary
        if counts.met is not None:
            from dmbot.ui.met_list import count_text

            self.add_item(_Button(self._met, label=count_text(counts.met), style=grey))
        if counts.scenes is not None:
            self.add_item(_Button(self._scenes, label=scenes_label(counts.scenes), style=grey))
        self.add_item(_Button(self._back, label="↩️ Back to Names", style=grey))

    async def _met(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.met_list import show as show_met

        self.stop()
        await show_met(interaction, self.campaign_id)

    async def _scenes(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.private_scenes_list import show as show_scenes

        self.stop()
        await show_scenes(interaction, self.campaign_id)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.names import show_home

        self.stop()
        await show_home(interaction, self.campaign_id)
