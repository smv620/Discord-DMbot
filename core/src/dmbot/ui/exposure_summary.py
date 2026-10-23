"""The after-session summary of secrets said at the table (#2410, slice 2; docs/PLAN.md, "Secrets
said out loud are revealed too, and the DM can recant"). One private message for the campaign's
DMs: each secret name or connection the table said this session, now shown to the players, with a
↩️ Recant button for a slip of the tongue, a misheard word, or a name the DM will change to keep
the mystery. It is on the DM screen while the screen is private and a private message to each DM
when players may look at it, like the secret-name question it replaces.

Nothing is shown to the players before this message is sent: the exposures are applied after it
went out, one change each, so ↩️ Recant undoes exactly that one (the change log's Undo). A press is
checked on every press: only a DM of the campaign. The buttons carry the exposure, the campaign
and the server, so they work after a restart and in a private message.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from typing import Any

import discord

from dmbot.memory._changes import ALREADY_UNDONE
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.secret_aloud import NO_CAMPAIGN, NO_MEMORY, NOT_YOURS

log = logging.getLogger(__name__)

MAX_LISTED = 10  # items the message lists (and buttons it carries); the rest wait for next time
PER_ROW = 5  # Discord: five buttons to a row
LABEL_MAX = 80  # characters of a name in the list
GONE = "DMbot doesn't remember that one any more."
BUSY = "DMbot is showing that one right now. Press again in a moment."
STUCK = (
    "Something went wrong while that one was being shown. Look at its name card: if players can "
    "see it, undo it there."
)
STALE_S = 120  # a claim older than this is a showing that died, not one in progress
NOTHING = "Nothing to take back: that one was never shown to your players."
ALREADY = "That one is already taken back."
SECRET_AGAIN = "↩️ Taken back: number {n} is secret again."
PRIVATE_NOTE = "(Sent to you alone, because your players can look at your DM screen.)"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def summary_text(
    items: Sequence[str],
    waiting: int,
    *,
    campaign_name: str = "",
    private: bool = False,
) -> str:
    """The message. `items`: one plain line each, in order (a name, or two names and how they are
    tied); `waiting`: how many more there are than listed. Short and plain: what was said, that the
    players can see it now, and what the button does."""
    where = (
        f" (campaign {discord.utils.escape_markdown(campaign_name)})"
        if private and campaign_name
        else ""
    )
    lines = [f"🤫 **Said at the table, now shown to your players**{where}"]
    lines += [f"{n}. {discord.utils.escape_markdown(_short(i))}" for n, i in enumerate(items, 1)]
    if waiting > 0:
        lines.append(
            f"…and {waiting} more. They come in your next summary, and your players don't see "
            "them until then."
        )
    lines.append("Press ↩️ Recant for a slip, a misheard word, or a name you'll change.")
    if private:
        lines.append(PRIVATE_NOTE)
    return "\n".join(lines)


class RecantButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:xr:(?P<exposure>[0-9]{1,18}):(?P<campaign>[0-9a-f]{32}):(?P<guild>[0-9]{1,20})",
):
    def __init__(self, exposure_id: int, campaign_id: str, guild_id: int, number: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"↩️ Recant {number}" if number else "↩️ Recant",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:xr:{exposure_id}:{campaign_id}:{guild_id}",
            )
        )
        self.exposure_id, self.campaign_id, self.guild_id = exposure_id, campaign_id, guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecantButton:
        return cls(int(match["exposure"]), match["campaign"], int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        # Answer Discord at once (it waits three seconds); the work and the replies follow.
        await interaction.response.defer()
        bot = _bot(interaction)
        # A press in a server must be in the server the message is about; in a private message the
        # server comes from the button.
        here = interaction.guild.id if interaction.guild is not None else self.guild_id
        campaign = (
            await bot.campaigns.get(self.guild_id, self.campaign_id)
            if here == self.guild_id
            else None
        )
        if campaign is None:
            await _tell(interaction, NO_CAMPAIGN)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        memory, exposures = bot.memory, getattr(bot, "exposures", None)
        if memory is None or exposures is None:
            await _tell(interaction, NO_MEMORY)
            return
        row = await exposures.one(campaign.guild_id, campaign.id, self.exposure_id)
        if row is None:
            await _tell(interaction, GONE)
            return
        if row.status == "recanted":
            await _tell(interaction, ALREADY)
            return
        if row.status == "settled":
            await _tell(interaction, NOTHING)
            return
        if row.status == "applying":
            claimed = row.claimed_at or 0
            if time.time() - claimed > STALE_S:
                # A showing that died (a restart between the claim and the record): let it go, so
                # it is never "busy" forever. Whether it was shown is for the name's card to say.
                await exposures.mark(campaign.guild_id, campaign.id, [row.id], "settled")
                await _tell(interaction, STUCK)
            else:
                await _tell(interaction, BUSY)
            return
        if row.status == "applied" and row.batch is not None:
            try:
                await memory.undo(campaign.guild_id, campaign.id, row.batch, source=DM)
            except TooLateToUndo as exc:
                await _tell(interaction, str(exc))
                return
            except MemoryRuleError as exc:
                # Undone already from the change log: it is taken back, so close it here too.
                # Anything else (changed again since) can't be put back on its own: say so.
                if str(exc) != ALREADY_UNDONE:
                    await _tell(interaction, str(exc))
                    return
            if bot.lookup is not None:
                bot.lookup.drop([campaign.id])
        if not await exposures.mark(campaign.guild_id, campaign.id, [row.id], "recanted"):
            await _tell(interaction, BUSY)  # shown meanwhile: press again
            return
        # Close this button on the message, and say so without a name (the message may be seen).
        view = discord.ui.View.from_message(interaction.message, timeout=None)  # type: ignore[arg-type]
        number = 0
        for item in view.children:
            if getattr(item, "custom_id", None) == self.item.custom_id:
                item.disabled = True  # type: ignore[attr-defined]
                item.label = (item.label or "").replace("↩️ Recant", "↩️ Taken back")  # type: ignore[attr-defined]
                number = int("".join(c for c in item.label if c.isdigit()) or 0)  # type: ignore[attr-defined]
        await interaction.edit_original_response(view=view, allowed_mentions=NO_PINGS)
        await interaction.followup.send(
            SECRET_AGAIN.format(n=number) if number else "↩️ Taken back.",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


def summary_view(exposure_ids: Sequence[int], campaign_id: str, guild_id: int) -> discord.ui.View:
    """One ↩️ Recant button for each listed item, five to a row, numbered like the list."""
    view = discord.ui.View(timeout=None)
    for n, exposure_id in enumerate(exposure_ids[:MAX_LISTED], 1):
        button = RecantButton(exposure_id, campaign_id, guild_id, n)
        button.item.row = (n - 1) // PER_ROW
        view.add_item(button)
    return view
