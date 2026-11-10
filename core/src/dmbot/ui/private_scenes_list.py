"""🤫 Private scenes (#2996 rule 5, slice 2): the times two player characters were alone together
while the party was split, as DMbot found them after each session (`memory/private_scene_store.py`).
DM-only. The one thing the DM does here is say a scene was **not private**: DMbot then never counts
it again, even when it reads the session again (Supervisor's ruling, 2026-11-10).

Every press is checked again: the person must still be a DM of the campaign, and who is recorded is
read again (a scene citing someone who stopped is left out). Ids and times only reach this list: the
two characters' names, the date, how long and how many lines, never a word that was said.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import discord

from dmbot.campaigns.models import Campaign
from dmbot.memory.private_scene_store import Kept, PrivateScenes
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _replace, _Select, _tell
from dmbot.ui.names import NOT_AVAILABLE, _campaign_for, _md, _memory, sees_secrets

log = logging.getLogger(__name__)

TITLE = "🤫 Private scenes"
INTRO = (
    "Times two player characters were alone together while the party was split. DMbot found them "
    "from the game clock and who spoke, and keeps no words. Only you see this list. If one wasn't "
    "really private, pick it and press Not private."
)
EMPTY = (
    "🤫 No private scenes yet. After each session, DMbot adds here any time two player characters "
    "were alone together."
)
DM_ONLY = "Only a DM of this campaign can see this list."
NOTHING_CHANGED = "Something went wrong, so nothing was changed. Try again in a moment."
SHOWN_MAX = 25  # one menu's choices


def notice(kept: int) -> str:
    """The after-session line in the DM Bot channel: counts only (anyone in the campaign may read
    that channel; the button checks the DM on every press). Pure."""
    if kept == 1:
        return (
            "🤫 DMbot noted 1 private scene between two player characters. Only a DM can open it."
        )
    return (
        f"🤫 DMbot noted {kept} private scenes between two player characters. Only a DM can open "
        "them."
    )


def when(scene: Kept) -> str:
    """The scene's date, how long and how many lines. Pure."""
    day = datetime.fromtimestamp(scene.session_started_at, UTC)
    minutes = max(1, round((scene.to_ms - scene.from_ms) / 60_000))
    return f"{day.day} {day:%b %Y}, about {minutes} min, {scene.line_count} lines"


async def _for_dm(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, when the one pressing is its DM now (checked on every press)."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return None  # (`_campaign_for` already said why)
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, DM_ONLY)
        return None
    return campaign


async def _names(
    interaction: discord.Interaction, campaign: Campaign, scenes: list[Kept]
) -> dict[str, str]:
    """Each character's name, by id (a merged one under the name it became)."""
    memory = _memory(interaction)
    out: dict[str, str] = {}
    for entity_id in {i for s in scenes for i in (s.first_id, s.second_id)}:
        entity = await memory.entity(campaign.guild_id, campaign.id, entity_id) if memory else None
        for _ in range(5):  # (a merge chain is short: follow it to the name it became)
            if memory is None or entity is None or not entity.merged_into:
                break
            entity = await memory.entity(campaign.guild_id, campaign.id, entity.merged_into)
        out[entity_id] = entity.name if entity is not None else "a character"
    return out


def pair(names: dict[str, str], scene: Kept) -> str:
    """ "Wren and Ash", in a steady order. Pure."""
    return " and ".join(sorted((names[scene.first_id], names[scene.second_id])))


async def show(
    interaction: discord.Interaction, campaign_id: str, note: str = "", *, in_place: bool = False
) -> None:
    """The list, newest session first, after the consent re-check."""
    await _answer_first(interaction, in_place=in_place)
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _for_dm(interaction, campaign_id)
    if campaign is None:
        return
    try:
        agreed = await _bot(interaction).consent.consenting(campaign.guild_id)
        scenes = await PrivateScenes(memory._db).listed(campaign.guild_id, campaign.id, agreed)
        names = await _names(interaction, campaign, scenes[:SHOWN_MAX])
    except Exception:
        log.exception("Couldn't read the private scenes list")
        await _tell(interaction, "DMbot couldn't read the list just now. Try again in a moment.")
        return
    head = f"{note}\n\n" if note else ""
    if not scenes:
        await _replace(interaction, head + EMPTY, None)
        return
    shown = scenes[:SHOWN_MAX]
    lines = [f"• **{_md(logic.shorten(pair(names, s), 90))}** · {when(s)}" for s in shown]
    more = f"\n…and {len(scenes) - len(shown)} older ones." if len(scenes) > len(shown) else ""
    text = f"{head}**{TITLE}**\n{INTRO}\n\n" + "\n".join(lines) + more
    await _replace(interaction, logic.shorten(text, 1990), SceneList(campaign.id, shown, names))


class SceneList(_Menu):
    def __init__(self, campaign_id: str, scenes: list[Kept], names: dict[str, str]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.scenes = {s.id: s for s in scenes}
        self.names = names
        self.pick = _Select(
            self._picked,
            placeholder="Pick a scene that wasn't private",
            options=[
                discord.SelectOption(
                    label=logic.shorten(pair(names, s), logic.OPTION_LABEL_MAX),
                    value=s.id,
                    description=logic.shorten(when(s), 100),
                )
                for s in scenes[:SHOWN_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        scene = self.scenes.get(str(self.pick.values[0])) if self.pick.values else None
        if scene is None:
            await show(interaction, self.campaign_id, in_place=True)
            return
        await _answer_first(interaction, in_place=True)
        await _replace(
            interaction,
            choice_text(pair(self.names, scene), scene),
            SceneActions(self.campaign_id, scene, pair(self.names, scene)),
        )


def choice_text(who: str, scene: Kept) -> str:
    """What the DM reads after picking one: each button in words. Pure."""
    return (
        f"**{_md(who)}**, {when(scene)}.\n"
        "🙅 **Not private**: it wasn't a private scene. DMbot won't count it again, even if it "
        "reads the session again.\n"
        "↩️ **Back**: keep it."
    )


class SceneActions(_Menu):
    def __init__(self, campaign_id: str, scene: Kept, who: str) -> None:
        super().__init__()
        self.campaign_id, self.scene, self.who = campaign_id, scene, who
        self.add_item(
            _Button(self._not_private, label="🙅 Not private", style=discord.ButtonStyle.danger)
        )
        self.add_item(_Button(self._back, label="↩️ Back", style=discord.ButtonStyle.secondary))

    async def _not_private(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        memory = _memory(interaction)
        if memory is None:
            await _tell(interaction, NOT_AVAILABLE)
            return
        campaign = await _for_dm(interaction, self.campaign_id)
        if campaign is None:
            return
        try:
            done = await PrivateScenes(memory._db).not_private(
                campaign.guild_id, campaign.id, self.scene.id, int(time.time())
            )
        except Exception:
            log.exception("Marking a private scene not private failed")
            await _tell(interaction, NOTHING_CHANGED)
            return
        note = (
            f"🙅 **{_md(self.who)}**: not private. DMbot won't count it again."
            if done
            else "That one was already gone."
        )
        await show(interaction, self.campaign_id, note, in_place=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show(interaction, self.campaign_id, in_place=True)


class PrivateScenesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:scenes:private:(?P<campaign>[0-9a-f]{32})",
):
    """On the after-session notice in the DM Bot channel: opens the list privately for a DM of the
    campaign. The channel is the campaign's people's, so it is checked on every press (as
    `met_list.MetListButton`). Works after a restart (the campaign is in the button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🤫 Open the list",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:scenes:private:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PrivateScenesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await show(interaction, self.campaign_id)  # (the DM check is the list's own, every press)


def notice_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PrivateScenesButton(campaign_id))
    return view
