"""The DM's questions about who is who at the table (#1188): "When someone said Mike, Mia
answered. Is Mike a name for Mia?" and "Mia answered to Cerric. Does Mia play Cerric?".
One question at a time, privately, after a session. Nothing is true until the DM says so."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, PeopleSuggestion
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import _campaign_for, _md, _memory

log = logging.getLogger(__name__)

NOTHING_WAITING = "✅ Nothing to check. DMbot asks after a session, when it learns a name."
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


def waiting_text(count: int) -> str:
    """For the DM screen after a session: how many, never who."""
    what = "question" if count == 1 else "questions"
    return (
        f"🧑 **{count} quick {what} about who is who.** DMbot noticed who answers to "
        "which name. Nothing changes until you say so. Press the button; only you will see it."
    )


class PeopleReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:people:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart (the campaign is in the
    button's ID)."""

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


def people_review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PeopleReviewButton(campaign_id))
    return view


async def start_people_review(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, "Remembering names isn't switched on for this DMbot yet.")
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    view = PeopleReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
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

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.later: set[str] = set()  # left for another time this round
        self.shown: PeopleSuggestion | None = None

    def _name_of(self, interaction: discord.Interaction, s: PeopleSuggestion) -> str:
        guild = interaction.guild
        shown = _bot(interaction).name_of(guild.id, s.user_id) if guild else s.shown
        return s.shown if shown.startswith("<@") else shown

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """The next waiting question with its buttons set, or None when none is left."""
        memory = _memory(interaction)
        guild = interaction.guild
        if memory is None or guild is None:
            return None
        waiting = [
            s for s in await memory.people_suggestions(guild.id, self.campaign_id)
            if s.row_id not in self.later
        ]  # fmt: skip
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
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
        return question_text(self.shown, self._name_of(interaction, self.shown), len(waiting))

    async def _next(self, interaction: discord.Interaction, done: str) -> None:
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _check(self, interaction: discord.Interaction) -> tuple[Any, PeopleSuggestion] | None:
        """The store and the question being answered, if this person may answer and it is
        still waiting (it may have been answered on another screen)."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None or self.shown is None:
            return None
        return memory, self.shown

    async def _yes(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None or interaction.guild is None:
            return
        memory, s = got
        who = self._name_of(interaction, s)
        try:
            if s.kind == "name":
                await memory.accept_person_name(interaction.guild.id, self.campaign_id, s.row_id)
                done = f"✅ **{_md(s.name)}** is now a name for **{_md(who)}**."
            else:
                await memory.decide_plays(
                    interaction.guild.id, self.campaign_id, s.row_id, yes=True
                )
                done = f"✅ **{_md(who)}** plays **{_md(s.name)}**."
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        await self._next(interaction, done)

    async def _no(self, interaction: discord.Interaction) -> None:
        got = await self._check(interaction)
        if got is None or interaction.guild is None:
            return
        memory, s = got
        try:
            if s.kind == "name":
                await memory.reject_person_name(interaction.guild.id, self.campaign_id, s.row_id)
                done = f"🚫 **{_md(s.name)}** is not a player's name. DMbot won't ask again."
            else:
                await memory.decide_plays(
                    interaction.guild.id, self.campaign_id, s.row_id, yes=False
                )
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
        guild = interaction.guild
        if got is None or guild is None:
            return
        memory, s = got
        bot = _bot(interaction)
        if person.bot or person.id not in await bot.consent.agreed(guild.id):
            # Only people who agreed to be recorded here could have been at the table.
            await _tell(
                interaction,
                "That person hasn't agreed to be recorded here, so they can't be picked. "
                "Pick someone who has.",
            )
            return
        try:
            await memory.accept_person_name(guild.id, self.campaign_id, s.row_id, user_id=person.id)
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        shown = bot.name_of(guild.id, person.id)
        await self._next(interaction, f"✅ **{_md(s.name)}** is now a name for **{_md(shown)}**.")


__all__ = ["PeopleReviewButton", "people_review_view", "start_people_review", "waiting_text"]
