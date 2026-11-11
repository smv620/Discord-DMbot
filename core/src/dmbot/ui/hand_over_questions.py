"""🔁 "You said Belleros has the Frost Brand now. Does Claude still have it?" (#4115; Supervisor's
ruling, 2026-11-10): two holders of a one-of-a-kind item, asked about once. "No, not any more" ends
the older holder's tie as history (with Undo); "Yes, both have it" is remembered; "Not now" leaves
it waiting. DMbot never ends a tie on its own. Shown in 📜 Check the connections after the
same-person questions, one at a time, in the DM's private message, so players never see it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import discord

from dmbot.memory.gate import Viewer
from dmbot.memory.hand_over import Question
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _Button, _Menu, _tell
from dmbot.ui.name_background import ONLY_DMS, campaign_for, markdown_safe, memory_of

log = logging.getLogger(__name__)

# The card's words (Supervisor's ruling on #4115, after the ux-critic): "have", never "own", so it
# never guesses how the item moved; the question's two answers are its buttons.
NO = "No, not any more"
BOTH = "Yes, both have it"
LATER = "Not now"
UNDO = "↩ Undo that"
GONE = "The last item changed since you opened this, so nothing was saved."
NOT_SAVED = "DMbot couldn't save that. Nothing changed. Press the button again in a moment."
NO_CAMPAIGN = "That campaign isn't here any more. Nothing was saved."


def the(item: str) -> str:
    """ "the Frost Brand", but never "the The Black Key"."""
    return item if item.lower().startswith(("the ", "a ", "an ")) else f"the {item}"


@dataclass(frozen=True, slots=True)
class Asked:
    """One question with the names the card shows (escaped for Discord)."""

    question: Question
    item: str
    older: str
    newer: str


def card_text(asked: Asked, place: str = "") -> str:
    """The question: the context first, then what each answer does."""
    a, b, item = asked.older, asked.newer, the(asked.item)
    return (
        f"🔁 You said **{b}** has **{item}** now. It's one of a kind.\n"
        f"**Does {a} still have it?**{place}\n"
        f"**{NO}:** {a}'s part becomes history.\n"
        f"**{BOTH}:** DMbot keeps both and won't ask again."
    )


def done_text(asked: Asked) -> str:
    return (
        f"✅ Done. **{asked.newer}** has **{the(asked.item)}** now. "
        f"**{asked.older}** used to have it."
    )


def both_text(asked: Asked) -> str:
    return (
        f"👍 OK. **{asked.older}** and **{asked.newer}** both keep **{the(asked.item)}**. "
        "DMbot won't ask about these two again. It's still one of a kind."
    )


def undone_text(asked: Asked) -> str:
    return (
        f"↩ Undone. **{asked.older}** has **{the(asked.item)}** again. DMbot will ask about it "
        "next time you press 📜 Check the connections."
    )


class HandOverQuestions(_Menu):
    """One question at a time: No, not any more / Yes, both have it / Not now."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.current: Asked | None = None
        self.note = ""  # what the last answer did, shown above the next question
        self.answered = 0
        self.total = 0
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        grey = discord.ButtonStyle.secondary
        self.add_item(_Button(self._no, label=NO, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._both, label=BOTH, style=grey))
        self.add_item(_Button(self._later, label=LATER, style=grey))

    async def _waiting(self, interaction: discord.Interaction) -> list[Asked]:
        memory = memory_of(interaction)
        campaign = await campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return []
        g, c = campaign.guild_id, campaign.id
        questions = await memory.hand_over_waiting(g, c, source=DM)
        ids = sorted({i for q in questions for i in (q.item_id, q.older_id, q.newer_id)})
        names = await memory.display_names(g, c, Viewer.the_dm(), ids) if ids else {}

        def shown(entity_id: str) -> str:
            return markdown_safe(names.get(entity_id) or "?")

        return [Asked(q, shown(q.item_id), shown(q.older_id), shown(q.newer_id)) for q in questions]

    def _set(self, waiting: list[Asked]) -> None:
        self.total = max(self.total, self.answered + len(waiting))
        self.current = waiting[0] if waiting else None

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self._set(await self._waiting(interaction))
        return self.current is not None

    def text(self) -> str:
        head = f"{self.note}\n\n" if self.note else ""
        if self.current is None:
            return f"{head}🔁 That's all the items for now."
        place = f" ({self.answered + 1} of {self.total})" if self.total > 1 else ""
        return head + card_text(self.current, place)

    async def _show(self, interaction: discord.Interaction, waiting: list[Asked]) -> None:
        self._set(waiting)
        if self.current is None:
            self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=self.text(), view=self if self.current else None, allowed_mentions=NO_PINGS
            )

    async def _answer(self, still_has: bool, interaction: discord.Interaction) -> None:
        shown = self.current  # the card this press was made on, taken before any wait
        await interaction.response.defer()
        async with self.busy:
            campaign = await campaign_for(interaction, self.campaign_id)
            memory = memory_of(interaction)
            if campaign is None or memory is None:
                await _tell(interaction, NO_CAMPAIGN)
                return
            if interaction.user.id not in campaign.dm_user_ids:  # on every press
                await _tell(interaction, ONLY_DMS)
                return
            if shown is None or self.current is not shown:  # answered by the first press
                return
            try:
                written = await memory.answer_hand_over(
                    campaign.guild_id, campaign.id, shown.question, still_has=still_has, source=DM
                )
            except MemoryRuleError:  # (it no longer waits: answered elsewhere, a tie changed)
                self.note = GONE
                await self._show(interaction, await self._waiting(interaction))
                return
            except Exception:  # (a database error: the card stays as it was)
                log.exception("A hand-over question could not be answered")
                await _tell(interaction, NOT_SAVED)
                return
            self.answered += 1
            log.info("A hand-over question was answered (%s)", "both" if still_has else "ended")
            if still_has:
                self.note = both_text(shown)
            else:
                self.note = ""
                undo = UndoHandOver(self.campaign_id, shown, written.batch)
                with contextlib.suppress(discord.HTTPException):
                    await interaction.followup.send(
                        done_text(shown), view=undo, ephemeral=True, allowed_mentions=NO_PINGS
                    )
            await self._show(interaction, await self._waiting(interaction))

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._answer(False, interaction)

    async def _both(self, interaction: discord.Interaction) -> None:
        await self._answer(True, interaction)

    async def _later(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:  # every press
            await _tell(interaction, ONLY_DMS)
            return
        left = len(await self._waiting(interaction))
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=(
                    f"⏭️ Saved for later. {left} still waiting: press 📜 Check the connections "
                    "when you are ready."
                ),
                view=None,
                allowed_mentions=NO_PINGS,
            )


class UndoHandOver(_Menu):
    """After "No, not any more": ↩ Undo that reopens the older holder's tie."""

    def __init__(self, campaign_id: str, asked: Asked, batch: int | None) -> None:
        super().__init__()
        self.campaign_id, self.asked, self.batch = campaign_id, asked, batch
        if batch is not None:
            self.add_item(_Button(self._undo, label=UNDO))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await campaign_for(interaction, self.campaign_id)
        memory = memory_of(interaction)
        if campaign is None or memory is None:
            await _tell(interaction, "That campaign isn't here any more. Nothing was undone.")
            return
        if self.batch is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        text = undone_text(self.asked)
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch, source=DM)
        except TooLateToUndo:
            text = "Too late to undo. Open the item's name card to change who has it."
        except MemoryRuleError:
            text = "Not undone, because something changed since. Check the item's name card."
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=text, view=None, allowed_mentions=NO_PINGS
            )


async def offer_hand_over(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The hand-over questions, one at a time, in a private message. Returns whether there was
    anything to ask."""
    try:
        view = HandOverQuestions(campaign_id)
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the hand-over questions")
        return False
