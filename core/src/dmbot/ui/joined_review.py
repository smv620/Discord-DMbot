"""The DM's check of the names DMbot joined by itself (#3602, slice 4b): "DMbot took The Ferry Lady
to be Old Tam." One at a time, privately, for a DM of the campaign only. A join is waiting until the
DM says yes (✅), or takes it back (↩️ Undo); until then players, the recap and table answers do not
use the new spelling. Nothing here shows a line anyone said: only the two names."""

from __future__ import annotations

import logging

import discord

from dmbot.memory._changes import NOT_FOUND
from dmbot.memory.models import MemoryRuleError, WaitingJoin
from dmbot.ui.dmbot_commands import _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

NOTHING_WAITING = "✅ Nothing to check right now. DMbot will say here when it joins a name itself."
GONE = "That one was already answered. Press **Check DMbot's joins** again to see the next."
ONLY_DMS = "Only a DM of this campaign can do that."
CHANGED = "That can't be undone any more: something changed since. Fix it from the name's card."


def question_text(j: WaitingJoin, count: int) -> str:
    """The join in plain words, with how many more wait."""
    more = f"\n_{count - 1} more after this._" if count > 1 else ""
    return (
        f"🤖 DMbot took **{_md(j.spelling)}** to be another name of **{_md(j.keep_name)}**.\n"
        f"Players won't see it until you say it's right.{more}"
    )


def summary_line(count: int) -> str:
    """The line for the after-session post and the names home: a count only."""
    noun = "name" if count == 1 else "names"
    return f"🤖 DMbot answered {count} {noun} itself: check {'it' if count == 1 else 'them'}."


async def start_joined_review(interaction: discord.Interaction, campaign_id: str) -> None:
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
    view = JoinedReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class JoinedReview(_Menu):
    """Yes / Undo / Later, and All fine. Later keeps the join waiting for another time."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.later: set[int] = set()
        self.shown: WaitingJoin | None = None

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        memory = _memory(interaction)
        guild = interaction.guild
        if memory is None or guild is None:
            return None
        waiting = [
            j
            for j in await memory.waiting_joins(guild.id, self.campaign_id)
            if j.batch not in self.later
        ]
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
        for handler, label, style in (
            (self._yes, "✅ Yes, that's right", discord.ButtonStyle.success),
            (self._undo, "↩️ Undo", discord.ButtonStyle.danger),
            (self._later, "⏳ Later", discord.ButtonStyle.secondary),
        ):
            self.add_item(_Button(handler, label=label, style=style))
        if len(waiting) > 1:
            self.add_item(
                _Button(self._all, label="✅ All fine", style=discord.ButtonStyle.secondary)
            )
        return question_text(self.shown, len(waiting))

    async def _next(self, interaction: discord.Interaction, done: str) -> None:
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _may_answer(self, interaction: discord.Interaction) -> bool:
        """Is this person a DM of the campaign (checked on every press)?"""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.guild is None:
            return False
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DMS)
            return False
        return True

    async def _yes(self, interaction: discord.Interaction) -> None:
        j = self.shown
        memory = _memory(interaction)
        if j is None or memory is None or not await self._may_answer(interaction):
            return
        assert interaction.guild is not None  # (`_may_answer` checked)
        try:
            await memory.confirm_join(interaction.guild.id, self.campaign_id, j.batch)
        except MemoryRuleError as exc:
            await _tell(interaction, GONE if str(exc) == NOT_FOUND else f"Couldn't do that. {exc}")
            return
        await self._after_change(interaction)
        await self._next(
            interaction, f"✅ **{_md(j.spelling)}** is now another name of **{_md(j.keep_name)}**."
        )

    async def _undo(self, interaction: discord.Interaction) -> None:
        j = self.shown
        memory = _memory(interaction)
        if j is None or memory is None or not await self._may_answer(interaction):
            return
        assert interaction.guild is not None
        try:
            await memory.undo(interaction.guild.id, self.campaign_id, j.batch)
        except MemoryRuleError:  # (TooLateToUndo is one: a name was changed since)
            await _tell(interaction, CHANGED)
            return
        await self._after_change(interaction)
        await self._next(
            interaction,
            f"↩️ Undone. **{_md(j.spelling)}** is a new name to check again, "
            f"not **{_md(j.keep_name)}**.",
        )

    async def _all(self, interaction: discord.Interaction) -> None:
        memory = _memory(interaction)
        if memory is None or not await self._may_answer(interaction):
            return
        assert interaction.guild is not None
        done = 0
        for j in await memory.waiting_joins(interaction.guild.id, self.campaign_id):
            try:
                await memory.confirm_join(interaction.guild.id, self.campaign_id, j.batch)
                done += 1
            except MemoryRuleError:
                continue  # (answered on another screen meanwhile)
        await self._after_change(interaction)
        await self._next(interaction, f"✅ {done} confirmed.")

    async def _later(self, interaction: discord.Interaction) -> None:
        if not await self._may_answer(interaction):
            return
        if self.shown is not None:
            self.later.add(self.shown.batch)
        await self._next(interaction, "⏳ Left for later.")

    async def _after_change(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is not None:
            changed(interaction, campaign)
