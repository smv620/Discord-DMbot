"""📝 More names (#5055 B; Supervisor's ruling on GraphEngineer's design note): the names an
imported session's name scan heard past its first 10, waiting in `import_more_names`. One press, by
a DM of the campaign, makes them names to check as the scan does (`DMBot.add_more_names`), and the
usual name check takes it from there. From the ❓ Questions for you list; works after a restart (the
campaign is in the button's ID). Counts only: the names themselves are shown on the check's cards.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import Any

import discord

from dmbot.ui.dmbot_commands import _tell
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _memory, review_view

log = logging.getLogger(__name__)

LABEL = "📝 More names"
NOTHING_LEFT = "✅ No more names are waiting."
NOT_AVAILABLE = "Names aren't available right now. Try again in a minute."
WORKING = "📝 Adding the names from your imported sessions to the names to check…"
FAILED = "Something went wrong, so no names were added. They are still waiting: try again later."


def added_text(made: int, left: int) -> str:
    """What one press did, as counts: how many names now wait in the name check, and how many are
    still behind More names (one press makes at most `more_names.TAKE_MAX`). Pure."""
    wait = f"{left} more {'waits' if left == 1 else 'wait'}"
    if not made:
        return f"DMbot already knew those names. {wait}: press {LABEL} again."
    head = (
        f"📝 **{made} more {'name' if made == 1 else 'names'} to check** from your imported "
        "sessions. Press the button to say which are in your game."
    )
    return head + (f"\n{wait}: press {LABEL} again after these." if left else "")


def working_text(done: int, of: int, seconds: int) -> str:
    """The press's progress (the wait rule): how far, and how long it has been running. Pure."""
    return f"📝 Adding names to check: {done} of {of} ({seconds} s so far)…"


class MoreNamesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:morenames:add:(?P<campaign>[0-9a-f]{32})",
):
    """📝 More names, from the Questions message. Only a DM of the campaign may press it (asked
    again on every press)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:morenames:add:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MoreNamesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await add_more_names(interaction, self.campaign_id)


async def add_more_names(interaction: discord.Interaction, campaign_id: str) -> None:
    # A new private message (the Questions message is never replaced), answered first: a few
    # hundred names is a database step of a few seconds, so the DM sees it working (the wait rule).
    await interaction.response.defer(ephemeral=True, thinking=True)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    bot: Any = interaction.client
    began = time.monotonic()

    async def moved(done: int, of: int) -> None:  # (a failed edit never stops the press)
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=working_text(done, of, int(time.monotonic() - began))
            )

    try:
        await interaction.edit_original_response(content=WORKING)
        made, left = await bot.add_more_names(campaign.guild_id, campaign.id, moved)
    except Exception as exc:  # (the type only: the names are the campaign's words)
        log.warning("More names failed (%s)", type(exc).__name__)
        await interaction.edit_original_response(content=FAILED)
        return
    if not made and not left:  # (none waited, or DMbot knows every one by now)
        await interaction.edit_original_response(content=NOTHING_LEFT)
        return
    await interaction.edit_original_response(
        content=added_text(made, left), view=review_view(campaign.id) if made else None
    )


__all__ = ["LABEL", "MoreNamesButton", "add_more_names", "added_text", "working_text"]
