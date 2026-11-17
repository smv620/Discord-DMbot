"""MapBot's live note to the DM, and its one button (#4670, MapBot slice 4c-2; design §9, "When the
DM's word and a map disagree"; Supervisor's ruling on #4670): a private message when a distance or
direction the DM says at the table is far off a kept map's, with "🔕 Stop map checks" and nothing
else (one button at most, no map link: ruling 2). The words are `maps/measure_words.py`'s.

The note is the DM's alone, in their private chat with DMbot, never on the DM screen, in a channel
or in a transcript. The button turns the campaign's "Map checks" off; ⚙️ Settings turns it back on.
It is pressed in a private chat, so the button carries the server and the campaign, and who may
press it (a DM of that campaign) is checked on every press."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.logs import set_log_context
from dmbot.maps import disagree, said_phrases
from dmbot.maps import measure_words as words

log = logging.getLogger(__name__)

ONLY_DM = "Only a DM of this campaign can change map checks."
GONE = "That campaign isn't here any more. Nothing was changed."
FAILED = "Something went wrong. Map checks are still on. Press the button again."


def note_text(found: Any) -> str:
    """The note's words for one `maps.store.SaidOff`."""
    said, off = found.said, found.off
    if said.kind == said_phrases.FAR:
        return words.said_far(
            said.words, found.a_name, found.b_name, disagree.amount(off), off.map.title
        )
    return words.said_way(found.a_name, said.words, found.b_name, off.map_way, off.map.title)


def note_view(guild_id: int, campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(StopMapChecksButton(guild_id, campaign_id))
    return view


class StopMapChecksButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:mapstop:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """🔕 Stop map checks: the campaign's "Map checks" off, for every DM of it (§9)."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.STOP,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapstop:{guild_id}:{campaign_id}",
            )
        )
        self.guild_id, self.campaign_id = guild_id, campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopMapChecksButton:
        return cls(int(match["guild"]), match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=self.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        try:
            campaign = (
                await store.get(self.guild_id, self.campaign_id) if store is not None else None
            )
        except Exception:
            log.exception("Couldn't load a campaign for Stop map checks")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # checked on every press
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        assert store is not None  # (a campaign was loaded from it)
        try:
            await store.set_map_checks(self.guild_id, self.campaign_id, False)
        except Exception:
            log.exception("Couldn't turn map checks off")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        await interaction.response.send_message(words.STOPPED, ephemeral=True)
