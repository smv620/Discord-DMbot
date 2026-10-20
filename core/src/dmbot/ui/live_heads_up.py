"""The first live heads-up on the DM screen (#2068, docs/STORY_MEMORY.md section 4): "⚠️ Heads-up:
Captain Rook died in Session 4. They were just voiced talking." with three answers. DMbot only
points; the DM decides what is true in their story, and nothing is written without their press.

DM screen only (it follows the screen's visibility like every post there): never a transcript,
`dmb-chat` or a players' channel. Only the campaign's own DM may press, asked again on every press.
The name is the one the table knows, never a secret name, and no line of speech is quoted.
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

import discord

from dmbot.memory.live_heads_up import HeadsUp, keep_quiet
from dmbot.memory.models import ALIVE, DM, MemoryRuleError
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.names import _md, _memory

log = logging.getLogger(__name__)

ALIVE_AFTER_ALL, NOT_THEM, IGNORE = "a", "n", "i"
NOT_THE_DM = "Only the DM of this campaign can answer this."
_DONE = {
    NOT_THEM: "👍 Okay. No more heads-ups about **{name}** this session.",
    IGNORE: "👍 Okay. No more heads-ups about **{name}** this session.",
}


def heads_up_text(heads_up: HeadsUp, session_number: int | None) -> str:
    """The heads-up in plain words. `session_number`: the session the entry died in, or None
    when that is not known (then it just says it died earlier)."""
    when = f"in Session {session_number}" if session_number is not None else "earlier"
    return f"⚠️ **Heads-up: {_md(heads_up.name)}** died {when}. They were just voiced talking."


class LiveHeadsUpButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:liveup:(?P<act>[ani]):(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32})",
):
    """One of the three answers; works after a restart (the campaign and the entry are in the
    button's ID)."""

    LABELS: ClassVar[dict[str, tuple[str, discord.ButtonStyle]]] = {
        ALIVE_AFTER_ALL: ("Alive after all", discord.ButtonStyle.primary),
        NOT_THEM: ("Not really them", discord.ButtonStyle.secondary),
        IGNORE: ("Ignore", discord.ButtonStyle.secondary),
    }

    def __init__(self, action: str, campaign_id: str, entity_id: str) -> None:
        label, style = self.LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:liveup:{action}:{campaign_id}:{entity_id}",
            )
        )
        self.action, self.campaign_id, self.entity_id = action, campaign_id, entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LiveHeadsUpButton:
        return cls(match["act"], match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.action, self.campaign_id, self.entity_id)


def heads_up_view(campaign_id: str, entity_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in (ALIVE_AFTER_ALL, NOT_THEM, IGNORE):
        view.add_item(LiveHeadsUpButton(action, campaign_id, entity_id))
    return view


async def answer(
    interaction: discord.Interaction, action: str, campaign_id: str, entity_id: str
) -> None:
    """The DM pressed a button. Only the campaign's DM, asked again now. "Not really them" and
    "Ignore" write nothing and keep the entry quiet for the rest of the session. "Alive after
    all" records a new dated condition (the history stays)."""
    bot, guild, memory = _bot(interaction), interaction.guild, _memory(interaction)
    if guild is None or memory is None:
        return
    campaign = await bot.campaigns.get(guild.id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_THE_DM)
        return
    table = bot.tables.get(guild.id)
    live = table.live_heads_up if table is not None and table.campaign_id == campaign_id else None
    entity = await memory.entity(guild.id, campaign_id, entity_id)
    name = _md(entity.name) if entity is not None else "them"
    if action in _DONE:
        if live is not None:
            keep_quiet(live, entity_id)
        await _finish(interaction, _DONE[action].format(name=name))
        return
    if action != ALIVE_AFTER_ALL or entity is None:
        await _finish(interaction, "That one is no longer there.")
        return
    try:
        await memory.set_condition(
            guild.id,
            campaign_id,
            entity_id,
            ALIVE,
            source=DM,
            from_session_at=table.started_at if table is not None else None,
        )
    except MemoryRuleError:
        log.info("Live heads-up: couldn't record alive")  # no names in the log
        await _tell(interaction, "DMbot couldn't record that. Try again in a moment.")
        return
    if live is not None:
        keep_quiet(live, entity_id)  # alive now, so it would not come again; quiet anyway
    await _finish(interaction, f"✅ Noted: **{name}** is alive again. The old record stays.")


async def _finish(interaction: discord.Interaction, text: str) -> None:
    """Replace the heads-up with its outcome and take its buttons away."""
    if interaction.response.is_done():
        await interaction.edit_original_response(content=text, view=None)
    else:
        await interaction.response.edit_message(content=text, view=None)
