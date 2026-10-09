"""The **Put it back** buttons on the "🙈 Left out as off-topic" message in the DM
screen (#677).

Each button's ID carries the server and the run. The runs live with the running
session, so after a restart, or once the session ended, a press just says it's closed.
Only the campaign's DMs may press it; the bot does the rest (`DMBot.put_back`).

Register with `bot.add_dynamic_items(PutBackButton)`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.transcript import left_out

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
FAILED = "Something went wrong. Try again in a moment."


class PutBackButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:putback:(?P<guild>[0-9]{1,20}):(?P<run>[0-9a-f]{8})",
):
    def __init__(self, guild_id: int, run_id: str, label: str = left_out.PUT_BACK) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:putback:{guild_id}:{run_id}",
            )
        )
        self.guild_id = guild_id
        self.run_id = run_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PutBackButton:
        label = getattr(item, "label", None) or left_out.PUT_BACK
        return cls(int(match["guild"]), match["run"], label)

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        if interaction.guild_id != self.guild_id or not hasattr(bot, "put_back"):
            await interaction.response.send_message(left_out.EXPIRED, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await bot.put_back(self.guild_id, self.run_id, interaction.user.id)
        except Exception:
            log.exception("Couldn't put off-topic lines back")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        await interaction.followup.send(answer, ephemeral=True, allowed_mentions=NO_PINGS)


def left_out_view(guild_id: int, runs: list[left_out.Run]) -> discord.ui.View | None:
    """One Put it back per run not put back yet, with the run's number."""
    view = discord.ui.View(timeout=None)
    for run in runs:
        if not run.put_back:
            view.add_item(PutBackButton(guild_id, run.id, f"{left_out.PUT_BACK} {run.number}"))
    return view if view.children else None
