"""The DM's offers of how the recap narrator should say a name (#2171): "People say Cerric like
'Kerrick'. Use that for the narrator?" One at a time, privately, for a DM of the campaign only.
Nothing changes until the DM says yes or types their own; a no is remembered."""

from __future__ import annotations

import logging

import discord

from dmbot.memory.models import MemoryRuleError, SaidAsSuggestion
from dmbot.memory.store import SAID_AS_MAX
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _campaign_for,
    _failed,
    _md,
    _memory,
    changed,
    sees_secrets,
)

log = logging.getLogger(__name__)

NOTHING_WAITING = (
    "✅ Nothing to check. DMbot offers some after a session, when it keeps hearing a name "
    "another way."
)
GONE = "That one was already answered or changed. Press **Check how to say** again for the next."
ONLY_DMS = "Only a DM of this campaign can do that."


def question_text(s: SaidAsSuggestion, count: int) -> str:
    """The offer in plain words, with how many more wait."""
    more = f"\n_{count - 1} more after this._" if count > 1 else ""
    return (
        f"🔊 People say **{_md(s.name)}** like **{_md(s.heard)}**.\n"
        f"Use that for the narrator in recaps?{more}"
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
            (self._type, "✏️ Type it", discord.ButtonStyle.primary),
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

    async def _may_answer(
        self, interaction: discord.Interaction
    ) -> tuple[discord.Guild, SaidAsSuggestion] | None:
        """The offer being answered, if this person is a DM of the campaign (checked on every
        press)."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or self.shown is None or interaction.guild is None:
            return None
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DMS)
            return None
        return interaction.guild, self.shown

    async def _save(
        self, interaction: discord.Interaction, spelling: str | None, done: str
    ) -> None:
        got = await self._may_answer(interaction)
        memory = _memory(interaction)
        if got is None or memory is None:
            return
        guild, s = got
        try:
            await memory.answer_said_as(guild.id, self.campaign_id, s.entity_id, spelling)
        except MemoryRuleError as exc:
            await _tell(
                interaction, GONE if "found" in str(exc).lower() else f"Couldn't change it. {exc}"
            )
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
            s.heard,
            f"🔊 The narrator will say **{_md(s.name)}** like **{_md(s.heard)}**.",
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        if self.shown is None:
            return
        await self._save(
            interaction,
            None,
            f"👍 **{_md(self.shown.name)}** is said as written. DMbot won't ask again.",
        )

    async def _type(self, interaction: discord.Interaction) -> None:
        got = await self._may_answer(interaction)
        if got is None:
            return
        _, s = got
        await interaction.response.send_modal(TypeIt(self, s))

    async def _later(self, interaction: discord.Interaction) -> None:
        if self.shown is not None:
            self.later.add(self.shown.entity_id)
        await self._next(interaction, "⏳ Left for later.")


class TypeIt(discord.ui.Modal, title="How to say it"):
    said_as: discord.ui.TextInput[TypeIt] = discord.ui.TextInput(
        label="How the narrator says it", max_length=SAID_AS_MAX, required=True
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
            await _tell(interaction, "Type how it should be said, or press ❌ No.")
            return
        self.review.shown = self.s  # the offer this form was opened for
        await self.review._save(
            interaction,
            spelling,
            f"🔊 The narrator will say **{_md(self.s.name)}** like **{_md(spelling)}**.",
        )
