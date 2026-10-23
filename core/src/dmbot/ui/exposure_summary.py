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

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory._changes import ALREADY_UNDONE
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.mentions import NO_PINGS
from dmbot.ui.claims_review import ClaimsReviewButton
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.names import RecapNamesButton, ReviewButton
from dmbot.ui.secret_aloud import NO_CAMPAIGN, NO_MEMORY, NOT_YOURS

log = logging.getLogger(__name__)

MAX_LISTED = 10  # items the message lists (and buttons it carries); the rest wait for next time
NAMES_LISTED = 5  # new names the message lists (as the separate post did)
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
    new_names: Sequence[str] = (),
    claims_waiting: int = 0,
    claim_lines: Sequence[str] = (),
    can_check: bool = True,
) -> str:
    """The message. `items`: one plain line each, in order (a name, or two names and how they are
    tied); `waiting`: how many more there are than listed. Short and plain: what was said, that the
    players can see it now, and what the button does. `new_names`: names DMbot heard for the first
    time this session (the 📝 Check new names list rides here so the DM gets one message).
    `claims_waiting`: how many things said at the table wait for the DM's check; `claim_lines`:
    the first few in words and who said them. **Every copy of this message is for the DM alone**
    (the DM screen while it is private, else a private message), which is why the words may be
    here: the posts anyone can read say the count only. `can_check`: the 🗣️ button is on this
    copy (a private message has no server for it). The words are dropped if the message would
    pass Discord's limit."""
    text = _compose(
        items, waiting, campaign_name, private, new_names, claims_waiting, claim_lines, can_check
    )
    if len(text) > MESSAGE_MAX and claim_lines:  # too long: the count alone says enough
        text = _compose(
            items, waiting, campaign_name, private, new_names, claims_waiting, (), can_check
        )
    return text


def _compose(
    items: Sequence[str],
    waiting: int,
    campaign_name: str,
    private: bool,
    new_names: Sequence[str],
    claims_waiting: int,
    claim_lines: Sequence[str],
    can_check: bool,
) -> str:
    lines: list[str] = []
    if items:
        where = (
            f" (campaign {discord.utils.escape_markdown(campaign_name)})"
            if private and campaign_name
            else ""
        )
        lines.append(f"🤫 **Said at the table, now shown to your players**{where}")
        lines += [
            f"{n}. {discord.utils.escape_markdown(_short(i))}" for n, i in enumerate(items, 1)
        ]
        if waiting > 0:
            lines.append(
                f"…and {waiting} more. They come in your next summary, and your players don't see "
                "them until then."
            )
        lines.append("Press ↩️ Recant for a slip, a misheard word, or a name you'll change.")
    if new_names:
        lines += ["", names_line(new_names)] if lines else [names_line(new_names)]
    if claims_waiting:
        if lines:
            lines.append("")
        lines.append(claims_line(claims_waiting))
        lines += [f"• {line}" for line in claim_lines]
        if len(claim_lines) < claims_waiting and claim_lines:
            lines.append(f"…and {claims_waiting - len(claim_lines)} more in the check.")
        if not can_check:
            lines.append("Open 🗣️ Check what was said on your DM screen to answer.")
    if private:
        lines.append(PRIVATE_NOTE)
    return "\n".join(lines)


def names_line(names: Sequence[str]) -> str:
    """The 🆕 list: up to five names and "and N more", like the separate post it replaces. The names
    come from the transcript anyone in the server can read, so listing them spoils nothing."""
    shown = ", ".join(
        f"**{discord.utils.escape_markdown(_short(n))}**" for n in names[:NAMES_LISTED]
    )
    more = f" and {len(names) - NAMES_LISTED} more" if len(names) > NAMES_LISTED else ""
    what = "new name" if len(names) == 1 else "new names"
    return (
        f"🆕 **{len(names)} {what} to check:** {shown}{more}. "
        "Say which are in your game, so DMbot spells them right."
    )


def claims_line(count: int) -> str:
    """The 🔗 line: how many things said at the table wait for the check, never what (the check
    itself says what, privately, with ✅ remember, 🤫 true but secret and ❌ not true)."""
    what = "thing" if count == 1 else "things"
    return (
        f"🔗 **{count} {what} said at the table to check.** Nothing is remembered until you say "
        "so: true, true but secret from your players, or not true."
    )


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


class SeenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:xs:(?P<campaign>[0-9a-f]{32}):(?P<guild>[0-9]{1,20})",
):
    """ "👍 Got it" on every variant of the summary (#2410). It does one small thing, taking itself
    and the other buttons off, and it carries the summary's mark: a message with no ↩️ Recant on it
    (only new names or things to check) is still found after a restart, and pulled back if the DM
    screen opens up to players, because the words of what was said are in it."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="👍 Got it",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:xs:{campaign_id}:{guild_id}",
                row=3,  # after the Recant rows and the check buttons
            )
        )
        self.campaign_id, self.guild_id = campaign_id, guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SeenButton:
        return cls(match["campaign"], int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer()
        bot = _bot(interaction)
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
        view = discord.ui.View.from_message(interaction.message, timeout=None)  # type: ignore[arg-type]
        view.remove_item(
            next(c for c in view.children if getattr(c, "custom_id", "") == self.item.custom_id)
        )
        await interaction.edit_original_response(view=view, allowed_mentions=NO_PINGS)


def summary_view(
    exposure_ids: Sequence[int],
    campaign_id: str,
    guild_id: int,
    *,
    new_names: bool = False,
    private: bool = False,
    claims: bool = False,
) -> discord.ui.View:
    """One ↩️ Recant button for each listed item, five to a row, numbered like the list; then the
    📝 Check new names button on its own row when the message lists new names."""
    view = discord.ui.View(timeout=None)
    for n, exposure_id in enumerate(exposure_ids[:MAX_LISTED], 1):
        button = RecantButton(exposure_id, campaign_id, guild_id, n)
        button.item.row = (n - 1) // PER_ROW
        view.add_item(button)
    if new_names:
        # A private message has no server, so its button carries the server (as the recap's does).
        check = RecapNamesButton(guild_id, campaign_id) if private else ReviewButton(campaign_id)
        check.item.row = 2  # after the (at most two) rows of Recant buttons
        view.add_item(check)
    if claims and not private:  # the check needs the server: only the screen's button has it
        check_claims = ClaimsReviewButton(campaign_id)
        check_claims.item.row = 2
        view.add_item(check_claims)
    view.add_item(SeenButton(campaign_id, guild_id))  # the mark every variant carries
    return view
