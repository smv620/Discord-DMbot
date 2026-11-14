"""The DM's card for who learned what (#2996, part 2 slice 1, PR 4 of 4): after a session, when
DMbot noticed a character learn something (a successful check the DM confirmed, or one character
telling another), the DM is asked once in their private chat, marked as from DMbot, and goes
through them one at a time with Keep and Not this.

Nothing is recorded until the DM presses Keep, and the press is the DM's own
`MemoryStore.learn_statement`, with one Undo, sent first as its own private message. The card says
what a Keep means for the table: "Keeping this lets Tran's player see it." for a player's character,
and "Tran was only guessing. Keeping this makes Tran sure of it." when they held it as a guess
(Supervisor's rulings on #2996: it is Tran who becomes sure; the fact was already true). The words
are read from DMbot's memory when the card is shown (the row holds ids only), DM-only, shown only
here. Every press is checked: the presser is one of the campaign's DMs
(the DM screen can be seen by players), and who is still recorded is asked again, so a proposal with
a speaker who has stopped is gone before it is shown. Logs carry counts, never the words.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.dm_questions import REVEALS, Ask
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.memory.reveal_proposals import GONE, STATEMENT_GONE, RevealProposals, Shown, Waiting
from dmbot.mentions import NO_PINGS
from dmbot.ui import walk_cards
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.walk import Walk

log = logging.getLogger(__name__)

ONLY_DMS = "Only a DM of this campaign can answer this."
NOT_AVAILABLE = "DMbot can't do that right now. Try again in a minute."
FAILED = "Couldn't save that. Try again in a minute."
NEXT_FAILED = "Couldn't show the next one. Press Look at them again in a minute."
UNDO_FAILED = "Couldn't undo that. Try again in a minute."
NONE_LEFT = "Nothing is waiting. DMbot will ask after a later session if it notices more."


def ask_for(guild_id: int, campaign_id: str, count: int) -> Ask:
    """The question for the DM's private message after a session."""
    many = "thing" if count == 1 else "things"
    text = (
        f"💡 DMbot noticed {count} {many} the characters learned. Look at each one and press Keep "
        "if it is right. Nothing is saved until you do."
    )
    line = f"{count} {many} the characters learned can be kept"
    return Ask(
        REVEALS,
        text,
        line,
        lambda: OpenButton(guild_id, campaign_id),
        lambda: OpenButton(guild_id, campaign_id),
        text,
        count=count,
    )


def card(shown: Shown, left: int) -> str:
    """One proposal, in plain words: who learned what, and how."""
    who = discord.utils.escape_markdown(shown.who)
    what = discord.utils.escape_markdown(shown.what)
    how = discord.utils.escape_markdown(shown.how)
    lines = [f"💡 **{who}** learned that {what}. How: {how}."]
    if shown.guessing:  # (Supervisor's wording, #2996)
        lines.append(f"{who} was only guessing. Keeping this makes {who} sure of it.")
    if shown.player:
        lines.append(f"Keeping this lets {who}'s player see it.")
    if left > 1:
        lines.append(f"_{left - 1} more after this one._")
    lines.append("Keep it? Nothing is saved until you press Keep.")  # (by the buttons)
    return "\n".join(lines)


def _view(guild_id: int, campaign_id: str, proposal: Waiting) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(KeepButton(guild_id, campaign_id, proposal.id))
    view.add_item(NotButton(guild_id, campaign_id, proposal.id))
    return view


class OpenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:rvask:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """In the collected question: opens the first waiting proposal."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Look at them",
                emoji="💡",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:rvask:{guild_id}:{campaign_id}",
            )
        )
        self.guild_id, self.campaign_id = guild_id, campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenButton:
        return cls(int(match["guild"]), match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await open_first(interaction, self.guild_id, self.campaign_id)


class KeepButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        r"dmbot:rvyes:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})"
    ),
):
    def __init__(self, guild_id: int, campaign_id: str, proposal_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:rvyes:{guild_id}:{campaign_id}:{proposal_id}",
            )
        )
        self.guild_id, self.campaign_id, self.proposal_id = guild_id, campaign_id, proposal_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.guild_id, self.campaign_id, self.proposal_id, keep=True)


class NotButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(r"dmbot:rvno:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})"),
):
    def __init__(self, guild_id: int, campaign_id: str, proposal_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Not this",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:rvno:{guild_id}:{campaign_id}:{proposal_id}",
            )
        )
        self.guild_id, self.campaign_id, self.proposal_id = guild_id, campaign_id, proposal_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NotButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.guild_id, self.campaign_id, self.proposal_id, keep=False)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        r"dmbot:rvundo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"
    ),
):
    """Takes the kept one back (the change log's one Undo)."""

    def __init__(self, guild_id: int, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:rvundo:{guild_id}:{campaign_id}:{batch}",
            )
        )
        self.guild_id, self.campaign_id, self.batch = guild_id, campaign_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(int(match["guild"]), match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await undo(interaction, self.guild_id, self.campaign_id, self.batch)


async def _checked(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> Any:
    """The checks every press makes before anything is shown or changed: the presser is one of the
    campaign's DMs and memory is there. Tells the presser why not and returns None; otherwise the
    bot. The interaction is already answered."""
    bot = _bot(interaction)
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return None
    if bot.memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return None
    return bot


async def _next(bot: Any, guild_id: int, campaign_id: str) -> tuple[Waiting, Shown, int] | None:
    """The first waiting proposal that can still be shown, and how many wait, after the consent
    re-check. One whose character or statement is gone since is turned down here and skipped."""
    agreed = await bot.consent.consenting(guild_id)
    reveals = RevealProposals(bot.memory)
    waiting = await reveals.waiting(guild_id, campaign_id, opted_in=agreed)
    for at, proposal in enumerate(waiting):
        shown = await reveals.describe(guild_id, campaign_id, proposal)
        if shown is not None:
            return proposal, shown, len(waiting) - at
        with contextlib.suppress(MemoryRuleError):  # (nothing left to learn: never asked again)
            await reveals.dismiss(guild_id, campaign_id, proposal.id, source=DM)
    return None


async def open_first(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = await _checked(interaction, guild_id, campaign_id)
    if bot is None:
        return
    found = await _next(bot, guild_id, campaign_id)
    if found is None:
        await _tell(interaction, NONE_LEFT)
        return
    proposal, shown, left = found
    await interaction.followup.send(
        card(shown, left),
        view=_view(guild_id, campaign_id, proposal),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


STATEMENT_GONE_WORDS = "That one is no longer in DMbot's memory, so there was nothing to keep."
ALREADY_ANSWERED = (
    "That one was already answered, or someone it came from has stopped being recorded."
)


def gone_words(exc: MemoryRuleError) -> str | None:
    """The card's words for a proposal that is gone (already answered, its speaker stopped, or its
    statement no longer in memory), or None for any other rule error."""
    if str(exc) == STATEMENT_GONE:  # (already turned down: say so and move on)
        return STATEMENT_GONE_WORDS
    if str(exc) == GONE:
        return ALREADY_ANSWERED
    return None


async def apply_answer(
    bot: Any, guild_id: int, campaign_id: str, proposal_id: str, *, keep: bool
) -> tuple[str, int | None]:
    """What Keep or Not this does: the words to show, and the change's batch for an Undo (a kept
    reveal that changed something). The one place the answer is made: the card's own buttons and
    the "Check everything" walk both come here. Consent is asked again for a Keep. Raises
    `MemoryRuleError`."""
    reveals = RevealProposals(bot.memory)
    if keep:
        agreed = await bot.consent.consenting(guild_id)
        batch = await reveals.keep(guild_id, campaign_id, proposal_id, opted_in=agreed, source=DM)
        log.info("Reveals: one kept by the DM")
        note = (
            "✅ Kept." if batch is not None else "Nothing to change: they already knew it for sure."
        )
        return note, batch
    await reveals.dismiss(guild_id, campaign_id, proposal_id, source=DM)
    log.info("Reveals: one turned down by the DM")
    return "Okay, not this one.", None


async def answer(
    interaction: discord.Interaction,
    guild_id: int,
    campaign_id: str,
    proposal_id: str,
    *,
    keep: bool,
) -> None:
    """Keep or Not this, then the next one in place of this card. Consent is asked again here: a
    proposal whose speaker has stopped is gone, and Keep says so instead of saving it."""
    await interaction.response.defer(ephemeral=True)
    bot = await _checked(interaction, guild_id, campaign_id)
    if bot is None:
        return
    batch: int | None = None
    try:
        note, batch = await apply_answer(bot, guild_id, campaign_id, proposal_id, keep=keep)
    except MemoryRuleError as exc:
        said = gone_words(exc)
        if said is None:
            await _tell(interaction, f"Couldn't do that. {exc}")
            return
        note = said
    except Exception:  # (the database was unreachable, say): the reply must not hang
        log.exception("Reveals: the answer could not be saved")
        await _tell(interaction, FAILED)
        return
    if batch is not None:  # first, so the Undo is there even if showing the next one fails
        undo_view = discord.ui.View(timeout=None)
        undo_view.add_item(UndoButton(guild_id, campaign_id, batch))
        await interaction.followup.send(
            "↩️ Changed your mind about the one you just kept?",
            view=undo_view,
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
    try:
        found = await _next(bot, guild_id, campaign_id)
    except Exception:
        log.exception("Reveals: the next one could not be loaded")
        await interaction.edit_original_response(
            content=f"{note}\n{NEXT_FAILED}", view=None, allowed_mentions=NO_PINGS
        )
        return
    if found is None:
        text, view = f"{note}\n{NONE_LEFT}", None
    else:
        proposal, shown, left = found
        text, view = f"{note}\n\n{card(shown, left)}", _view(guild_id, campaign_id, proposal)
    await interaction.edit_original_response(content=text, view=view, allowed_mentions=NO_PINGS)


async def undo(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, batch: int
) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = await _checked(interaction, guild_id, campaign_id)
    if bot is None:
        return
    try:
        await bot.memory.undo(guild_id, campaign_id, batch, source=DM)
    except TooLateToUndo as exc:  # (the store's own plain words)
        await _tell(interaction, f"Couldn't undo that. {exc}")
        return
    except Exception:
        log.exception("Reveals: the undo failed")
        await _tell(interaction, UNDO_FAILED)
        return
    # (back to before the Keep: a character who was guessing is guessing again, not unaware)
    await _tell(interaction, "↩️ Undone. Back to how it was before you pressed Keep.")


WALK_FINISHED = "✅ What the characters learned: checked."  # (the line a walk leaves)


@dataclass(frozen=True, slots=True)
class WalkItem:
    """A waiting proposal in a walk: the first one is described (its words are read from memory
    when it is shown); the others wait their turn with ids only. The reveals part has no Later
    button on purpose: a card that is skipped would leave the next item undescribed."""

    proposal: Waiting
    shown: Shown | None = None


async def waiting_reveals(bot: Any, guild_id: int, campaign_id: str) -> list[WalkItem]:
    """The proposals waiting for a yes, the first one described. One whose character or statement
    is gone since is turned down here and skipped, as `_next` does."""
    agreed = await bot.consent.consenting(guild_id)
    reveals = RevealProposals(bot.memory)
    waiting = await reveals.waiting(guild_id, campaign_id, opted_in=agreed)
    for at, proposal in enumerate(waiting):
        shown = await reveals.describe(guild_id, campaign_id, proposal)
        if shown is not None:
            return [WalkItem(proposal, shown), *(WalkItem(p) for p in waiting[at + 1 :])]
        with contextlib.suppress(MemoryRuleError):  # (nothing left to learn: never asked again)
            await reveals.dismiss(guild_id, campaign_id, proposal.id, source=DM)
    return []


async def count_reveals(bot: Any, guild_id: int, campaign_id: str) -> int:
    """How many wait, counted without changing anything. It can be a little higher than what the
    cards show: a proposal whose character or statement is gone is turned down when its card
    loads (as the question's own card does)."""
    if bot.memory is None:
        return 0
    agreed = await bot.consent.consenting(guild_id)
    return await RevealProposals(bot.memory).count_waiting(guild_id, campaign_id, opted_in=agreed)


def walk_kind(bot: Any, guild_id: int, campaign_id: str) -> walk_cards.Kind:
    """What the characters learned as a part of "✅ Check everything" (#4192): the same card, Keep
    and Not this and the Undo, one at a time."""

    def choice(label: str, style: discord.ButtonStyle, keep: bool) -> walk_cards.Choice:
        async def do(item: WalkItem) -> walk_cards.Answer:
            try:
                note, batch = await apply_answer(
                    bot, guild_id, campaign_id, item.proposal.id, keep=keep
                )
            except MemoryRuleError as exc:
                said = gone_words(exc)
                if said is None:
                    raise
                return walk_cards.Answer(said)
            undo = UndoButton(guild_id, campaign_id, batch) if batch is not None else None
            return walk_cards.Answer(note, undo)

        return walk_cards.Choice(label, style, do)

    def text(item: WalkItem, left: int) -> str:
        assert item.shown is not None  # (the first of the list is always described)
        return card(item.shown, left)

    return walk_cards.Kind(
        waiting=lambda: waiting_reveals(bot, guild_id, campaign_id),
        key=lambda item: item.proposal.id,
        text=text,
        choices=lambda: [
            choice("✅ Keep", discord.ButtonStyle.success, True),
            choice("Not this", discord.ButtonStyle.secondary, False),
        ],
        finished=WALK_FINISHED,
    )


async def start_walk_part(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, walk: Walk
) -> None:
    """The reveals part of a walk. The presser was checked as a DM when the walk began."""
    bot = _bot(interaction)
    if bot.memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await walk_cards.start(interaction, walk_kind(bot, guild_id, campaign_id), walk)
