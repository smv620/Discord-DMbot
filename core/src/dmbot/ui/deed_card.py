"""The DM's card for deeds the session reader noticed (#3175 slice 4, PR 3): after a session, when
DMbot noticed things the party did that mattered to someone, the DM is asked once in their private
chat, marked as from DMbot, and goes through them one at a time with Record and Not this.

Nothing is recorded until the DM presses Record, and the press is the DM's own `add_deed`, with one
Undo. The collected after-session message gives each question one button, so it is "Look at them";
it opens the first waiting deed for the DM only (checked on every press: the DM screen can be seen
by players). The words are the model's short telling of what the party did, DM-only, shown only
here: never in a transcript, never to players. Every press asks again who is still recorded, and a
deed with a speaker who has stopped is gone before it is shown. Logs carry counts, never the words.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.dm_questions import DEEDS, Ask
from dmbot.memory.deed_proposals import GONE, DeedProposals, Waiting
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.memory.standing import DEED_VERB
from dmbot.mentions import NO_PINGS
from dmbot.ui import walk_cards
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.walk import Walk

log = logging.getLogger(__name__)

ONLY_DMS = "Only a DM of this campaign can answer this."
NOT_AVAILABLE = "DMbot can't do that right now. Try again in a minute."
FAILED = "Couldn't do that. Try again in a minute."
# What kind of deed it was, in words, so the DM sees what Record will keep (#3236)
KIND_WORDS = {
    "attack": "fight", "kill": "killing", "rescue": "rescue", "save": "saving",
    "steal": "theft", "help": "favour",
}  # fmt: skip
NONE_LEFT = "No deeds are waiting. DMbot will ask after a later session if it notices more."


def ask_for(guild_id: int, campaign_id: str, count: int) -> Ask:
    """The question for the DM's private message after a session."""
    many = "deed" if count == 1 else "deeds"
    text = (
        f"🛡️ DMbot noticed {count} {many} the party did that mattered to someone. "
        "Look at each one and press Record if it is right. Nothing is saved until you do."
    )
    line = f"{count} {many} the party did can be recorded"
    return Ask(
        DEEDS,
        text,
        line,
        lambda: OpenButton(guild_id, campaign_id),
        lambda: OpenButton(guild_id, campaign_id),
        text,
        count=count,
    )


def card(deed: Waiting, left: int) -> str:
    """One deed, in plain words: who did it, what, whom it mattered to and how it landed."""
    who = discord.utils.escape_markdown(deed.doer)
    text = discord.utils.escape_markdown(deed.text)
    viewers = ", ".join(f"**{discord.utils.escape_markdown(v)}**" for v in deed.viewers)
    landed = DEED_VERB.get(deed.effect, "touched")
    kind = f" (a {KIND_WORDS[deed.kind]})" if deed.kind in KIND_WORDS else ""
    more = f"\n_{left - 1} more after this one._" if left > 1 else ""
    return (
        f"🛡️ **{who}**: “{text}”{kind}\n"
        f"It mattered to {viewers} (it {landed} them). Record it?{more}\n"
        "Nothing is saved until you press Record."
    )


def _view(guild_id: int, campaign_id: str, deed: Waiting) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RecordButton(guild_id, campaign_id, deed.id))
    view.add_item(NotButton(guild_id, campaign_id, deed.id))
    return view


class OpenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:deedask:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """In the collected question: opens the first waiting deed."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Look at them",
                emoji="🛡️",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:deedask:{guild_id}:{campaign_id}",
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


class RecordButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:deedyes:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, guild_id: int, campaign_id: str, proposal_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Record",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:deedyes:{guild_id}:{campaign_id}:{proposal_id}",
            )
        )
        self.guild_id, self.campaign_id, self.proposal_id = guild_id, campaign_id, proposal_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecordButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.guild_id, self.campaign_id, self.proposal_id, record=True)


class NotButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:deedno:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, guild_id: int, campaign_id: str, proposal_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Not this",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:deedno:{guild_id}:{campaign_id}:{proposal_id}",
            )
        )
        self.guild_id, self.campaign_id, self.proposal_id = guild_id, campaign_id, proposal_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NotButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.guild_id, self.campaign_id, self.proposal_id, record=False)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:deedundo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Takes the recorded deed back (the change log's one Undo)."""

    def __init__(self, guild_id: int, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:deedundo:{guild_id}:{campaign_id}:{batch}",
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


async def _next(bot: Any, guild_id: int, campaign_id: str) -> tuple[Waiting, int] | None:
    """The first waiting deed and how many wait, after the consent re-check (a deed with a speaker
    who has stopped is deleted here, before it can be shown)."""
    agreed = await bot.consent.consenting(guild_id)
    waiting = await DeedProposals(bot.memory).waiting(guild_id, campaign_id, opted_in=agreed)
    return (waiting[0], len(waiting)) if waiting else None


async def open_first(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = await _checked(interaction, guild_id, campaign_id)
    if bot is None:
        return
    found = await _next(bot, guild_id, campaign_id)
    if found is None:
        await _tell(interaction, NONE_LEFT)
        return
    deed, left = found
    await interaction.followup.send(
        card(deed, left),
        view=_view(guild_id, campaign_id, deed),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


ALREADY_ANSWERED = (
    "That one was already answered, or someone it came from has stopped being recorded."
)


async def apply_answer(
    bot: Any, guild_id: int, campaign_id: str, proposal_id: str, *, record: bool
) -> tuple[str, int | None]:
    """What Record or Not this does: the words to show, and the change's batch for an Undo (a
    recorded deed). The one place the answer is made: the card's own buttons and the "Check
    everything" walk both come here. Consent is asked again for a record (a deed whose speaker has
    stopped is gone). Raises `MemoryRuleError` (the store's `GONE` when it is no longer waiting)."""
    deeds = DeedProposals(bot.memory)
    if record:
        agreed = await bot.consent.consenting(guild_id)
        batch = await deeds.record(guild_id, campaign_id, proposal_id, opted_in=agreed, source=DM)
        log.info("Deeds: one recorded by the DM")
        return "✅ Recorded.", batch
    await deeds.dismiss(guild_id, campaign_id, proposal_id, source=DM)
    log.info("Deeds: one turned down by the DM")
    return "Okay, not this one.", None


async def answer(
    interaction: discord.Interaction,
    guild_id: int,
    campaign_id: str,
    proposal_id: str,
    *,
    record: bool,
) -> None:
    """Record or Not this, then the next one in place of this card. Consent is asked again here:
    a deed whose speaker has stopped is gone, and Record says so instead of saving it."""
    await interaction.response.defer(ephemeral=True)
    bot = await _checked(interaction, guild_id, campaign_id)
    if bot is None:
        return
    note = ""
    batch: int | None = None
    try:
        note, batch = await apply_answer(bot, guild_id, campaign_id, proposal_id, record=record)
    except MemoryRuleError as exc:
        if str(exc) != GONE:
            await _tell(interaction, f"Couldn't do that. {exc}")
            return
        note = ALREADY_ANSWERED
    except Exception:  # (the database was unreachable, say): the reply must not hang
        log.exception("Deeds: the answer could not be saved")
        await _tell(interaction, FAILED)
        return
    if batch is not None:  # first, so the Undo is there even if showing the next one fails
        undo_view = discord.ui.View(timeout=None)
        undo_view.add_item(UndoButton(guild_id, campaign_id, batch))
        await interaction.followup.send(
            "↩️ Changed your mind about the one you just recorded?",
            view=undo_view,
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
    try:
        found = await _next(bot, guild_id, campaign_id)
    except Exception:
        log.exception("Deeds: the next one could not be loaded")
        await interaction.edit_original_response(
            content=f"{note}\n{FAILED}", view=None, allowed_mentions=NO_PINGS
        )
        return
    if found is None:
        text, view = f"{note}\n{NONE_LEFT}", None
    else:
        deed, left = found
        text, view = f"{note}\n\n{card(deed, left)}", _view(guild_id, campaign_id, deed)
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
        log.exception("Deeds: the undo failed")
        await _tell(interaction, FAILED)
        return
    await _tell(interaction, "↩️ Undone. That deed is not recorded.")


WALK_FINISHED = "✅ Deeds: checked."  # (the line a "Check everything" walk leaves)


async def waiting_deeds(bot: Any, guild_id: int, campaign_id: str) -> list[Waiting]:
    """The deeds waiting for a yes, after the consent re-check (see `_next`)."""
    agreed = await bot.consent.consenting(guild_id)
    return await DeedProposals(bot.memory).waiting(guild_id, campaign_id, opted_in=agreed)


async def count_deeds(bot: Any, guild_id: int, campaign_id: str) -> int:
    """How many wait, counted without changing anything."""
    if bot.memory is None:
        return 0
    agreed = await bot.consent.consenting(guild_id)
    return await DeedProposals(bot.memory).count_waiting(guild_id, campaign_id, opted_in=agreed)


def walk_kind(bot: Any, guild_id: int, campaign_id: str) -> walk_cards.Kind:
    """The deeds as a part of "✅ Check everything" (#4192): the same card, Record and Not this
    and the Undo, one deed at a time."""

    def choice(label: str, style: discord.ButtonStyle, record: bool) -> walk_cards.Choice:
        async def do(deed: Waiting) -> walk_cards.Answer:
            try:
                note, batch = await apply_answer(bot, guild_id, campaign_id, deed.id, record=record)
            except MemoryRuleError as exc:
                if str(exc) != GONE:
                    raise
                return walk_cards.Answer(ALREADY_ANSWERED)
            undo = UndoButton(guild_id, campaign_id, batch) if batch is not None else None
            return walk_cards.Answer(note, undo)

        return walk_cards.Choice(label, style, do)

    return walk_cards.Kind(
        waiting=lambda: waiting_deeds(bot, guild_id, campaign_id),
        key=lambda deed: deed.id,
        text=lambda deed, left: card(deed, left),
        choices=lambda: [
            choice("✅ Record", discord.ButtonStyle.success, True),
            choice("Not this", discord.ButtonStyle.secondary, False),
        ],
        finished=WALK_FINISHED,
    )


async def start_walk_part(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, walk: Walk
) -> None:
    """The deeds part of a walk. The presser was checked as a DM when the walk began."""
    bot = _bot(interaction)
    if bot.memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await walk_cards.start(interaction, walk_kind(bot, guild_id, campaign_id), walk)
