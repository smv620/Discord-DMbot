"""The automatic stop's warning and its words (#2034). When nobody is in the voice channel, or
nobody has spoken for the campaign's quiet limit, DMbot says so on the DM screen with a
**Keep listening** button and stops the session itself five minutes later (see `idle_stop`).
Only the campaign's DM (or a server manager) may press the button; it carries the campaign and
the session, so it works after a restart and never acts on a newer session.

Register with `bot.add_dynamic_items(KeepListeningButton)`."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.dm_screen.buttons import may_change_screen
from dmbot.idle_stop import EMPTY, WARN_S
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
_WARN_MINUTES = WARN_S // 60
KEPT = "👍 Still listening."
CANCELLED = "👍 Someone is here again, so I'm still listening."
ONLY_DM = "Only this campaign's DM can keep DMbot listening."
STALE = "That session has already ended."


def quiet_label(minutes: int) -> str:
    return (
        "Off" if minutes == 0 else f"{minutes} min"
    )  # the button's label is 25 characters at most


def idle_warning_text(reason: str, quiet_minutes: int) -> str:
    why = "Nobody's here." if reason == EMPTY else f"Nobody has spoken for {quiet_minutes} minutes."
    return (
        f"⏸️ {why} I'll stop listening in {_WARN_MINUTES} minutes. "
        "Tap **Keep listening** if you're still playing."
    )


def stopped_text(reason: str, quiet_minutes: int) -> str:
    if reason == EMPTY:
        return "⏹ Stopped: nobody was here for 5 minutes."
    return f"⏹ Stopped: nobody spoke for {quiet_minutes} minutes."


def line(minutes: int) -> str:
    """The settings card's line."""
    if minutes == 0:
        return (
            "• **Stop when quiet:** Off. DMbot still stops by itself when nobody is in the "
            "voice channel for 5 minutes. Tap **Stop when quiet** to change it."
        )
    return (
        f"• **Stop when quiet:** after {minutes} minutes with nobody speaking, and after 5 "
        "minutes with nobody in the voice channel. DMbot warns you first. Tap **Stop when "
        "quiet** to change it."
    )


class KeepListeningButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:keepon:{_ID}:(?P<session>[0-9]{{1,12}})",
):
    """Keep listening, on the warning: the session carries on and both clocks start again."""

    def __init__(self, campaign_id: str, session: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep listening",
                emoji="🎙",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:keepon:{campaign_id}:{session}",
            )
        )
        self.campaign_id, self.session = campaign_id, session

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepListeningButton:
        return cls(match["campaign"], int(match["session"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        allowed = await may_change_screen(interaction, self.campaign_id, not_dm=ONLY_DM)
        if allowed is None:
            return
        _, guild, _ = allowed
        keep = getattr(interaction.client, "keep_listening", None)
        kept = bool(keep is not None and keep(guild.id, self.campaign_id, self.session))
        await interaction.response.edit_message(
            content=KEPT if kept else STALE, view=None, allowed_mentions=NO_PINGS
        )


def keep_view(campaign_id: str, session: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(KeepListeningButton(campaign_id, session))
    return view


__all__ = ["KeepListeningButton", "idle_warning_text", "keep_view", "line", "stopped_text"]
