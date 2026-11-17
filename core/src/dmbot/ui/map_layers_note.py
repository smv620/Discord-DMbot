"""The DM's private note after a map's layers were read (#4259, MapBot slice 8a; design §7.4, §7.6
and §9; Supervisor's rulings on #4259; the ux-critic's words). One map's reads are one note, with
counts and one ↩ Undo, sent privately to each DM of the campaign, never in the DM Bot channel or the
DM screen: it names places. With no session running it goes at once; during a session it waits
and joins the after-session notices (§7.3: nothing alerts during play). Each map's note is sent
once. World notes is a link in the words, never a second button (§9: one button at most).

Counts name the layer for each place ("land for 3 places"), never "3 lands": DMbot only noted
something about places the DM already has. A disagreement with what the DM kept changes nothing
(it changes nothing and writes no row: no note for it alone). Words and counts only, no picture."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.logs import set_log_context
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import ALREADY_UNDONE, MAP_PICTURE_GONE
from dmbot.memory.models import MemoryRuleError, TooLateToUndo

log = logging.getLogger(__name__)

UNDO = "↩ Undo"
NOT_UNDONE = "Not undone: some of these were changed since. Change them in 🌍 World notes."
PICTURE_GONE = "Not undone: that map's picture was deleted since."
TOO_OLD = "Not undone: that was too long ago. Change them in 🌍 World notes."
ALREADY = "Already undone (by you or another DM)."
GONE = "That campaign is gone, so there's nothing to undo."
ONLY_DM = "Only a DM of this campaign can undo this."
FAILED = "Something went wrong. Nothing was changed. Press the button again."
# (the layer's word for one place's row, in World notes' own order)
COUNTS = (
    (mf.TERRAIN, "land for"), (mf.CLIMATE, "climate for"), (mf.CONTROL, "ruler for"),
    (mf.CULTURE, "culture for"), (mf.POPULATION, "who lives there for"),
)  # fmt: skip
_SAYS_MAP = re.compile(r"\b(map|chart|plan)$", re.I)


def map_label(title: str, created_at: int) -> str:
    """'the Ten-Towns map'; a title that says map already as it is ('the Ten-Towns Map'); with no
    title, 'a map posted on 14 Nov'."""
    title = re.sub(r"^the\s+", "", " ".join(title.split()), flags=re.I)
    if not title:
        when = time.strftime("%d %b", time.gmtime(created_at)).lstrip("0")
        return f"a map posted on {when}"
    return f"the {title}" if _SAYS_MAP.search(title) else f"the {title} map"


def _world_notes(site: str) -> str:
    return f"[🌍 World notes](<{site}/account/names>)" if site else "🌍 World notes"


def note_text(
    label: str,
    written: Mapping[str, int],
    counts: Mapping[str, int],
    site: str = "",
) -> str | None:
    """The note's words, or None when the map added nothing (a difference alone writes no row, so
    there is nothing to check or undo, and no note). `written`: new rows by layer; `counts`: the
    store's (`corrected`, `noted`)."""
    parts: list[str] = []
    for layer, words in COUNTS:
        n = written.get(layer, 0)
        if n:
            places = (" place" if n == 1 else " places") if not parts else ""
            parts.append(f"{words} {n}{places}")
    replaced, disagrees = counts.get("corrected", 0), counts.get("noted", 0)
    where = _world_notes(site)
    if not parts:
        return None
    text = f"🗺️ DMbot read {label}: {', '.join(parts)}. They're waiting in {where} for you to check."
    if replaced:
        verb = "replaces" if replaced == 1 else "replace"
        text += f" {replaced} of them {verb} what DMbot read in your lore."
    if disagrees:
        verb = "differs" if disagrees == 1 else "differ"
        text += f" {disagrees} {verb} from what's already there, and that stays as it is."
    return text


def undone_text(label: str) -> str:
    """The rows' own batch only: where DMbot placed places on the map stays (#4700)."""
    return f"↩ Undone. Nothing from {label} is in World notes now. The map itself is still kept."


def note_view(guild_id: int, campaign_id: str, batch: int | None, label: str) -> Any:
    """↩ Undo when there is something to take back (a note of disagreements only has none)."""
    if batch is None:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(UndoMapLayersButton(guild_id, campaign_id, batch))
    return view


class UndoMapLayersButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:mapundo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """↩ Undo on a map's note: the read's layer rows taken back (their own batch: the map's pins
    stay, #4700). Pressed in a private chat, so it carries the server; a DM of that campaign
    only, checked on every press."""

    def __init__(self, guild_id: int, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapundo:{guild_id}:{campaign_id}:{batch}",
            )
        )
        self.guild_id, self.campaign_id, self.batch = guild_id, campaign_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoMapLayersButton:
        return cls(int(match["guild"]), match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=self.guild_id, campaign_id=self.campaign_id)
        client = interaction.client
        store, memory = getattr(client, "campaigns", None), getattr(client, "memory", None)
        try:
            campaign = await store.get(self.guild_id, self.campaign_id) if store else None
        except Exception:
            log.exception("Couldn't load a campaign for a map note's Undo")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if memory is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # checked on every press
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await memory.undo(self.guild_id, self.campaign_id, self.batch)
        except TooLateToUndo:  # (its history is gone: pruned, or a backup was loaded)
            await interaction.followup.send(TOO_OLD, ephemeral=True)
            return
        except MemoryRuleError as exc:  # (the store's reason says which: `_changes`' words)
            # (picture gone: defensive, as the rows' batch holds no map row since #4700)
            words = {ALREADY_UNDONE: ALREADY, MAP_PICTURE_GONE: PICTURE_GONE}.get(str(exc))
            await interaction.followup.send(words or NOT_UNDONE, ephemeral=True)
            return
        except Exception:
            log.exception("A map note's Undo failed")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        message = interaction.message
        label = _label_in(message.content if message is not None else "")
        try:
            await interaction.edit_original_response(content=undone_text(label), view=None)
        except discord.HTTPException:
            await interaction.followup.send(undone_text(label), ephemeral=True)


def _label_in(text: str) -> str:
    """The map's words back out of the note ('DMbot read the Ten-Towns map: …')."""
    m = re.search(r"DMbot read (.+): (?:land|climate|ruler|culture|who) ", text)
    return m.group(1) if m else "that map"
