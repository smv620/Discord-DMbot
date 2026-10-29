"""The DM's offers of how the recap narrator should say a name (#2171): "People say Cerric like
'Kerrick'. Use that for the narrator?" One at a time, privately, for a DM of the campaign only.
Nothing changes until the DM says yes or types their own; a no is remembered."""

from __future__ import annotations

import logging

import discord

from dmbot.memory._changes import NOT_FOUND
from dmbot.memory.models import MemoryRuleError, SaidAsSuggestion
from dmbot.memory.store import SAID_AS_MAX
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _send,
    _tell,
)
from dmbot.ui.name_card import _spoken_recaps
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

NOTHING_WAITING = (
    "✅ Nothing to check right now. DMbot will offer a name here when it hears it said "
    "a different way."
)
GONE = "That one was already answered. Press **Check how to say** again to see the next."
ONLY_DMS = "Only a DM of this campaign can do that."
NOT_ON = "Spoken recaps aren't switched on for this campaign, so there is nothing to say aloud."


def question_text(s: SaidAsSuggestion, count: int) -> str:
    """The offer in plain words, with how many more wait."""
    more = f"\n_{count - 1} more after this._" if count > 1 else ""
    return (
        f"🔊 At your table, **{_md(s.name)}** sounds like **{_md(s.heard)}**.\n"
        f"Should the narrator say it that way in recaps?{more}"
    )


async def start_said_as_review(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, "Remembering names isn't switched on for this DMbot yet.")
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return
    if not await _spoken_recaps(interaction, campaign):  # the plan may have lapsed since the offer
        await _tell(interaction, NOT_ON)
        return
    view = SaidAsReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class SaidAsReview(_Menu):
    """Yes / No / Type it / Later. Later keeps the offer waiting for another time."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.later: set[str] = set()
        self.shown: SaidAsSuggestion | None = None

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        memory = _memory(interaction)
        guild = interaction.guild
        if memory is None or guild is None:
            return None
        waiting = [
            s
            for s in await memory.said_as_suggestions(guild.id, self.campaign_id)
            if s.entity_id not in self.later
        ]
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
        for handler, label, style in (
            (self._yes, "✅ Yes", discord.ButtonStyle.success),
            (self._no, "❌ No", discord.ButtonStyle.secondary),
            (self._type, "✏️ Type my own", discord.ButtonStyle.primary),
            (self._later, "⏳ Later", discord.ButtonStyle.secondary),
        ):
            self.add_item(_Button(handler, label=label, style=style))
        return question_text(self.shown, len(waiting))

    async def _next(self, interaction: discord.Interaction, done: str) -> None:
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _may_answer(self, interaction: discord.Interaction) -> bool:
        """Is this person a DM of the campaign (checked on every press and every submit)?"""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.guild is None:
            return False
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DMS)
            return False
        if not await _spoken_recaps(interaction, campaign):  # checked again: a plan can lapse
            await _tell(interaction, NOT_ON)
            return False
        return True

    async def _save(
        self,
        interaction: discord.Interaction,
        s: SaidAsSuggestion,
        spelling: str | None,
        done: str,
    ) -> None:
        """Answer offer `s` (the one the press or form was for, not whatever is shown now)."""
        memory = _memory(interaction)
        if memory is None or interaction.guild is None or not await self._may_answer(interaction):
            return
        guild_id = interaction.guild.id
        try:
            await memory.answer_said_as(guild_id, self.campaign_id, s.entity_id, spelling)
        except MemoryRuleError as exc:
            if str(exc) == NOT_FOUND:  # answered on another screen, or the name went meanwhile
                await _tell(interaction, GONE)
            else:
                await _tell(interaction, f"Couldn't change it. {exc}")
            return
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is not None and spelling is not None:
            changed(interaction, campaign)
        await self._next(interaction, done)

    async def _yes(self, interaction: discord.Interaction) -> None:
        if self.shown is None:
            return
        s = self.shown
        await self._save(
            interaction,
            s,
            s.heard,
            f"🔊 The narrator will say **{_md(s.name)}** like **{_md(s.heard)}**.",
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        if self.shown is None:
            return
        await self._save(
            interaction,
            self.shown,
            None,
            f"👍 The narrator will say **{_md(self.shown.name)}** as it's spelled. "
            "DMbot won't ask again.",
        )

    async def _type(self, interaction: discord.Interaction) -> None:
        if self.shown is None or not await self._may_answer(interaction):
            return
        await interaction.response.send_modal(TypeIt(self, self.shown))

    async def _later(self, interaction: discord.Interaction) -> None:
        if not await self._may_answer(interaction):
            return
        if self.shown is not None:
            self.later.add(self.shown.entity_id)
        await self._next(interaction, "⏳ Left for later.")


class TypeIt(discord.ui.Modal, title="How to say it"):
    said_as: discord.ui.TextInput[TypeIt] = discord.ui.TextInput(
        label="How the narrator says it",
        placeholder="Type it as it sounds, like kerrick",
        max_length=SAID_AS_MAX,
        required=True,
    )

    def __init__(self, review: SaidAsReview, s: SaidAsSuggestion) -> None:
        super().__init__(title=logic.shorten(f"How to say {s.name}", 45), timeout=VIEW_TIMEOUT_S)
        self.review = review
        self.s = s
        self.said_as.default = s.heard[:SAID_AS_MAX]

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        spelling = " ".join(self.said_as.value.split())
        if not spelling:
            await _tell(interaction, "Type how it should sound, or press ❌ No.")
            return
        await self.review._save(
            interaction,
            self.s,  # the offer this form was opened for
            spelling,
            f"🔊 The narrator will say **{_md(self.s.name)}** like **{_md(spelling)}**.",
        )
