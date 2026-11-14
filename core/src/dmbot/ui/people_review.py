"""The DM's questions about who is who at the table (#1188): "When someone said Mike, Mia
answered. Is Mike a name for Mia?" and "Mia answered to Cerric. Does Mia play Cerric?".
One question at a time, privately, after a session. Nothing is true until the DM says so."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, PeopleSuggestion
from dmbot.ui.check_everything import add_stop, next_part, progress
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import _campaign_for, _md, _memory
from dmbot.walk import Walk

log = logging.getLogger(__name__)

NOTHING_WAITING = "✅ Nothing to check. DMbot asks after a session, when it learns a name."
FINISHED = "✅ Who is who: checked."  # (the line a "Check everything" walk leaves, #4192)
GONE = "That one was already answered. Press **Check who is who** again for the next one."


def question_text(s: PeopleSuggestion, who: str, count: int) -> str:
    """The question in plain words. `who` is the person's name as the DM knows it."""
    more = f"\n_{count - 1} more after this._" if count > 1 else ""
    if s.kind == "name":
        return (
            f"Someone said **{_md(s.name)}**, and **{_md(who)}** answered.\n"
            f"Is **{_md(s.name)}** another name for **{_md(who)}**?{more}"
        )
    return (
        f"**{_md(who)}** answered when someone said **{_md(s.name)}**.\n"
        f"Is **{_md(who)}** playing the character **{_md(s.name)}**?{more}"
    )


def waiting_text(count: int, *, private: bool = False) -> str:
    """After a session: how many, never who. `private` is the DM's own chat (#3210): it says what
    to do next and drops the line about who will see the button."""
    what = "question" if count == 1 else "questions"
    if private:
        return (
            f"🧑 **{count} quick {what} about who is who.** DMbot noticed who answers to "
            "which name. Nothing changes until you say so. Press the button."
        )
    return (
        f"🧑 **{count} quick {what} about who is who.** DMbot noticed who answers to "
        "which name. Nothing changes until you say so. Press the button; only you will see it."
    )


def waiting_line(count: int) -> str:
    """The short line for the combined after-session message."""
    what = "question" if count == 1 else "questions"
    return f"{count} quick {what} about who is who"


class PeopleReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:people:(?P<campaign>[0-9a-f]{32})",
):
    """In the DM's private chat after a session (or the DM screen if no DM can be reached); works
    after a restart (the campaign is in the button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🧑 Check who is who",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:names:people:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PeopleReviewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_people_review(interaction, self.campaign_id)


async def start_people_review(
    interaction: discord.Interaction, campaign_id: str, *, walk: Walk | None = None
) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, "Remembering names isn't switched on for this DMbot yet.")
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    view = PeopleReview(campaign.id, campaign.guild_id, walk=walk)
    text = await view.current_text(interaction)
    if text is None:
        if walk is not None:  # (answered meanwhile: on to the next part)
            await next_part(interaction, walk)
            return
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class _UserSelect(discord.ui.UserSelect[Any]):
    def __init__(self, on_pick: Any) -> None:
        super().__init__(placeholder="Pick who it is…")
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.on_pick(interaction, self.values[0])


class PeopleReview(_Menu):
    """Yes / Someone else… / Not a player / Later for a name; Yes / No / Later for a
    character. Later keeps the question waiting for the next time."""

    def __init__(self, campaign_id: str, guild_id: int, *, walk: Walk | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.walk = walk  # inside "Check everything" (#4192): a progress line and Stop for now
        self.first_total = 0  # how many waited when this review began, for "4 of 11"
        # The server comes with the question, not from the press: the question may be in the
        # DM's private chat, where a press has no server (#3210).
        self.guild_id = guild_id
        self.later: set[str] = set()  # left for another time this round
        self.shown: PeopleSuggestion | None = None

    def _name_of(self, interaction: discord.Interaction, s: PeopleSuggestion) -> str:
        shown = _bot(interaction).name_of(self.guild_id, s.user_id)
        return s.shown if shown.startswith("<@") or s.user_id < 0 else shown

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """The next waiting question with its buttons set, or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = [
            s for s in await memory.people_suggestions(self.guild_id, self.campaign_id)
            if s.row_id not in self.later
        ]  # fmt: skip
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
        self.first_total = max(self.first_total, len(waiting))
        if self.shown.kind == "name":
            buttons = [
                (self._yes, "✅ Yes, that's them", discord.ButtonStyle.success, 0),
                (self._other, "👤 Someone else…", discord.ButtonStyle.primary, 0),
                (self._no, "🚫 Not at the table", discord.ButtonStyle.secondary, 1),
                (self._later, "⏳ Later", discord.ButtonStyle.secondary, 1),
            ]
        else:
            buttons = [
                (self._yes, "✅ Yes", discord.ButtonStyle.success, 0),
                (self._no, "❌ No", discord.ButtonStyle.secondary, 0),
                (self._later, "⏳ Later", discord.ButtonStyle.secondary, 0),
            ]
        for handler, label, style, row in buttons:
            self.add_item(_Button(handler, label=label, style=style, row=row))
        add_stop(self, self.walk, row=2)
        done = self.first_total - len(waiting) + 1
        return progress(self.walk, done, self.first_total) + question_text(
            self.shown, self._name_of(interaction, self.shown), len(waiting)
        )

    async def _next(self, interaction: discord.Interaction, done: str) -> None:
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            final = FINISHED if self.walk is not None else NOTHING_WAITING
            await _replace(interaction, f"{done}\n\n{final}".strip(), None)
            if self.walk is not None:
                await next_part(interaction, self.walk)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _check(self, interaction: discord.Interaction) -> tuple[Any, PeopleSuggestion] | None:
        """The store and the question being answered, if this person may answer and it is
        still waiting (it may have been answered on another screen)."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None or self.shown is None:
            return None
        self.guild_id = campaign.guild_id  # (the campaign may have moved server since this opened)
        return memory, self.shown

    async def _yes(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None:
            return
        memory, s = got
        who = self._name_of(interaction, s)
        try:
            if s.kind == "name":
                await memory.accept_person_name(self.guild_id, self.campaign_id, s.row_id)
                done = f"✅ **{_md(s.name)}** is now a name for **{_md(who)}**."
            else:
                await memory.decide_plays(self.guild_id, self.campaign_id, s.row_id, yes=True)
                done = f"✅ **{_md(who)}** plays **{_md(s.name)}**."
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        await self._next(interaction, done)

    async def _no(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None:
            return
        memory, s = got
        try:
            if s.kind == "name":
                await memory.reject_person_name(self.guild_id, self.campaign_id, s.row_id)
                done = f"🚫 **{_md(s.name)}** is not a player's name. DMbot won't ask again."
            else:
                await memory.decide_plays(self.guild_id, self.campaign_id, s.row_id, yes=False)
                done = (
                    f"❌ **{_md(self._name_of(interaction, s))}** is not marked as playing "
                    f"**{_md(s.name)}**. DMbot won't ask again."
                )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        await self._next(interaction, done)

    async def _later(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None:
            return
        self.later.add(got[1].row_id)
        await self._next(interaction, "⏳ Left for later. DMbot will ask again next time.")

    async def _other(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None:
            return
        self.clear_items()
        self.add_item(_UserSelect(self._other_picked))
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
        await _replace(interaction, f"**Who is {_md(got[1].name)}?**", self)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self._next(interaction, "")

    async def _other_picked(
        self, interaction: discord.Interaction, person: discord.User | discord.Member
    ) -> None:
        got = await self._check(interaction)
        if got is None:
            return
        memory, s = got
        bot = _bot(interaction)
        if person.bot or person.id not in await bot.consent.agreed(self.guild_id):
            # Only people who agreed to be recorded here could have been at the table.
            await _tell(
                interaction,
                "That person hasn't agreed to be recorded here, so they can't be picked. "
                "Pick someone who has.",
            )
            return
        try:
            await memory.accept_person_name(
                self.guild_id, self.campaign_id, s.row_id, user_id=person.id
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        shown = bot.name_of(self.guild_id, person.id)
        await self._next(interaction, f"✅ **{_md(s.name)}** is now a name for **{_md(shown)}**.")


__all__ = ["PeopleReviewButton", "start_people_review", "waiting_line", "waiting_text"]
