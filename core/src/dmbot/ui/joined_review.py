"""The DM's check of the names DMbot joined by itself (#3602, slice 4b): "DMbot took The Ferry Lady
to be Old Tam." One at a time, privately, for a DM of the campaign only. A join is waiting until the
DM says yes (✅), or takes it back (↩️ Undo); until then players, the recap and table answers do not
use the new spelling. Nothing here shows a line anyone said: only the two names."""

from __future__ import annotations

import logging

import discord

from dmbot.campaigns import Campaign
from dmbot.memory._changes import ALREADY_UNDONE, NOT_FOUND
from dmbot.memory.models import MemoryRuleError, WaitingJoin
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

NOTHING_WAITING = "✅ Nothing to check right now."
GONE = "That one is already done. Press **Check guesses** again for the next."
ONLY_DMS = "Only a DM of this campaign can do that."
CHANGED = "Can't undo this one: the name was changed since. Open the name's card to fix it."


def question_text(j: WaitingJoin, count: int) -> str:
    """The join in plain words, with how many more wait."""
    more = f"\n_{count - 1} more after this._" if count > 1 else ""
    return (
        f"🤖 DMbot guessed that **{_md(j.spelling)}** is **{_md(j.keep_name)}**.\n"
        f"Only you know this for now. Players and recaps still treat them as two names until "
        f"you press Yes.{more}"
    )


def summary_line(count: int) -> str:
    """The line for the after-session post and the names home: a count only."""
    noun = "name" if count == 1 else "names"
    it = "it" if count == 1 else "them"
    return f"🤖 DMbot matched {count} {noun} on its own. Please check {it}."


COULDNT_LOAD = "Couldn't load DMbot's guesses just now. Try again in a moment."
NOTHING_LEFT = "Nothing was left to confirm: they were all answered already."


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
    try:
        text = await view.current_text(interaction, campaign.guild_id)
    except Exception:
        log.exception("Couldn't read the names DMbot matched on its own")
        await _tell(interaction, COULDNT_LOAD)
        return
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class JoinedReview(_Menu):
    """Yes / Undo / Later, and All are right. Later keeps the guess waiting for another time."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.later: set[int] = set()
        self.shown: WaitingJoin | None = None

    async def current_text(self, interaction: discord.Interaction, guild_id: int) -> str | None:
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = [
            j
            for j in await memory.waiting_joins(guild_id, self.campaign_id)
            if j.batch not in self.later
        ]
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
        for handler, label, style in (
            (self._yes, "✅ Yes, that's right", discord.ButtonStyle.success),
            (self._undo, "↩️ Undo", discord.ButtonStyle.secondary),
            (self._later, "⏳ Later", discord.ButtonStyle.secondary),
        ):
            self.add_item(_Button(handler, label=label, style=style))
        if len(waiting) > 1:
            self.add_item(
                _Button(self._all, label="✅ All are right", style=discord.ButtonStyle.secondary)
            )
        return question_text(self.shown, len(waiting))

    async def _next(self, interaction: discord.Interaction, guild_id: int, done: str) -> None:
        try:
            text = await self.current_text(interaction, guild_id)
        except Exception:
            log.exception("Couldn't read the names DMbot matched on its own")
            self.stop()
            await _replace(interaction, f"{done}\n\n{COULDNT_LOAD}".strip(), None)
            return
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _may_answer(self, interaction: discord.Interaction) -> Campaign | None:
        """The campaign when this person is a DM of it (checked on every press), else None after
        saying why. The server is the campaign's own, so it works from a private chat too."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return None
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DMS)
            return None
        return campaign

    async def _yes(self, interaction: discord.Interaction) -> None:
        j = self.shown
        memory = _memory(interaction)
        campaign = await self._may_answer(interaction)
        if j is None or memory is None or campaign is None:
            return
        await _answer_first(interaction, in_place=True)
        try:
            await memory.confirm_join(campaign.guild_id, self.campaign_id, j.batch)
        except MemoryRuleError as exc:
            if str(exc) == NOT_FOUND:  # answered on another screen: move on to the next
                await self._next(interaction, campaign.guild_id, GONE)
            else:
                await _tell(interaction, f"Couldn't do that. {exc}")
            return
        changed(interaction, campaign)
        await self._next(
            interaction,
            campaign.guild_id,
            f"✅ **{_md(j.spelling)}** is now **{_md(j.keep_name)}**. Players can see it.",
        )

    async def _undo(self, interaction: discord.Interaction) -> None:
        j = self.shown
        memory = _memory(interaction)
        campaign = await self._may_answer(interaction)
        if j is None or memory is None or campaign is None:
            return
        await _answer_first(interaction, in_place=True)
        try:
            await memory.undo(campaign.guild_id, self.campaign_id, j.batch)
        except MemoryRuleError as exc:  # (TooLateToUndo is one: a name was changed since)
            if str(exc) in (NOT_FOUND, ALREADY_UNDONE):  # answered on another screen
                await self._next(interaction, campaign.guild_id, GONE)
            else:
                await _tell(interaction, CHANGED)
            return
        changed(interaction, campaign)
        await self._next(
            interaction,
            campaign.guild_id,
            f"↩️ Undone. **{_md(j.spelling)}** is no longer **{_md(j.keep_name)}**. "
            "It goes back to your list of names to check.",
        )

    async def _all(self, interaction: discord.Interaction) -> None:
        memory = _memory(interaction)
        campaign = await self._may_answer(interaction)
        if memory is None or campaign is None:
            return
        await _answer_first(interaction, in_place=True)
        done = 0
        try:
            for j in await memory.waiting_joins(campaign.guild_id, self.campaign_id):
                if j.batch in self.later:
                    continue  # (the DM pressed Later on it: it stays for another time)
                try:
                    await memory.confirm_join(campaign.guild_id, self.campaign_id, j.batch)
                    done += 1
                except MemoryRuleError:
                    continue  # (answered on another screen meanwhile)
        finally:
            if done:
                changed(interaction, campaign)  # (even if something broke part-way)
        if not done:
            await self._next(interaction, campaign.guild_id, NOTHING_LEFT)
            return
        noun = "name is" if done == 1 else "names are"
        await self._next(interaction, campaign.guild_id, f"✅ {done} {noun} now matched.")

    async def _later(self, interaction: discord.Interaction) -> None:
        campaign = await self._may_answer(interaction)
        if campaign is None:
            return
        await _answer_first(interaction, in_place=True)
        if self.shown is not None:
            self.later.add(self.shown.batch)
        await self._next(
            interaction, campaign.guild_id, "⏳ Left for later. It stays on your list."
        )
