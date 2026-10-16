"""The DM's after-session check of "Did they mean…?" questions nobody answered while the game
ran (#1572): "DMbot heard Mia say "Marin". Did they mean… [Maren] [Marron] [Type it…]
[Keep "Marin"]". One question at a time, privately, DMs only. Nothing changes until the DM says
so: a name picked fixes that one line of the cleaned transcript (never what was heard) and is
saved as the campaign's correction, so later sessions write it the same way; Keep as heard saves
the "don't change this" rule. The bot does the work (`DMBot.answer_waiting_question`)."""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.transcript import questions
from dmbot.transcript.cleaner import own_name
from dmbot.transcript.waiting_questions import Waiting
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.names import _campaign_for, _md

NOTHING_WAITING = "✅ All checked. DMbot will ask after a session if it isn't sure of a name."
ONLY_DMS = "Only the campaign's DM can check these. Ask your DM."
FAILED = "Something went wrong. Nothing was changed. Press the button again."


def waiting_text(count: int) -> str:
    """For the DM screen after a session: how many, never what was said."""
    what = "word needs" if count == 1 else "words need"
    return (
        f"❓ **{count} {what} a check.** DMbot wasn't sure which name was said during the game. "
        "Nothing changes until you pick. Only you will see it."
    )


class HeardReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:heard:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart (the campaign is in the
    button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="❓ Check names",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:names:heard:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HeardReviewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_heard_review(interaction, self.campaign_id)


def heard_review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(HeardReviewButton(campaign_id))
    return view


async def start_heard_review(interaction: discord.Interaction, campaign_id: str) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if interaction.user.id not in campaign.dm_user_ids:  # checked on every press
        await _tell(interaction, ONLY_DMS)
        return
    view = HeardReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class TypeHeardForm(discord.ui.Modal):
    """**Type it…**: the DM writes the name themselves. The bot checks it with the names list's
    rules, and again that they are the DM and the speaker is still recorded."""

    name: discord.ui.TextInput[TypeHeardForm] = discord.ui.TextInput(
        label=questions.FORM_FIELD,
        placeholder=questions.FORM_HINT,
        max_length=questions.TYPED_MAX,
    )

    def __init__(self, review: HeardReview, heard: str) -> None:
        super().__init__(title=questions.FORM_TITLE)
        self.review = review
        self.name.default = heard[: questions.TYPED_MAX] or None  # one letter to fix

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.review.answer(interaction, questions.TYPE, self.name.value)


class HeardReview(_Menu):
    """One name per button, Type it…, Keep as heard, Later. Later leaves the question waiting
    for the next time."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.later: set[int] = set()  # left for another time this round
        self.shown: Waiting | None = None

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """The next waiting question with its buttons set, or None when none is left."""
        bot: Any = _bot(interaction)
        guild = interaction.guild
        store = bot.heard_questions
        if store is None or guild is None or bot.lookup is None:
            return None
        campaign = await bot.campaigns.get(guild.id, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return None  # checked at every redraw: a DM role taken away mid-review ends it
        waiting = [
            w
            for w in await store.waiting(guild.id, self.campaign_id)
            if w.id not in self.later and bot.consent.has_consent(guild.id, w.speaker_id)
        ]  # (a speaker who stopped being recorded is never shown)
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = first = waiting[0]
        lookup = await bot.lookup.get(guild.id, self.campaign_id)
        offered: set[str] = set()
        for number, entity_id in enumerate(first.option_ids):
            name = own_name(lookup, entity_id)
            # A name removed, or made secret, since is not offered, nor one that would look like
            # another button (two entries called the same).
            if name is not None and name.casefold() not in offered:
                offered.add(name.casefold())
                self.add_item(
                    _Button(
                        self._picker(number),
                        label=questions.option_label(name),
                        style=discord.ButtonStyle.primary,
                    )
                )
        self.add_item(_Button(self._type, label=questions.TYPE_LABEL, row=1))
        self.add_item(_Button(self._keep, label=questions.keep_label(first.heard), row=1))
        self.add_item(_Button(self._later, label="⏳ Later", row=1))
        who = bot.name_of(guild.id, first.speaker_id)
        speaker = "a player" if who.startswith("<@") else _md(who)
        more = f"\n{len(waiting) - 1} more after this." if len(waiting) > 1 else ""
        return (
            f'❓ **DMbot heard {speaker} say "{questions._short(_md(first.heard))}".** '
            f"Did they mean…\nNot sure? Press ⏳ Later.{more}"
        )

    def _picker(self, number: int) -> Any:
        async def pick(interaction: discord.Interaction) -> None:
            await self.answer(interaction, str(number))

        return pick

    async def answer(
        self, interaction: discord.Interaction, pick: str, typed: str | None = None
    ) -> None:
        """Save an answer and move to the next question; a private note says why not when it
        can't be saved."""
        shown = self.shown
        guild = interaction.guild
        if shown is None or guild is None:
            await _tell(interaction, FAILED)
            return
        try:
            answer, done = await _bot(interaction).answer_waiting_question(
                guild.id, shown.id, pick, interaction.user.id, typed
            )
        except Exception:
            await _tell(interaction, FAILED)
            raise
        if not done:  # a typing mistake, or not the DM: the question stays
            await _tell(interaction, answer)
            return
        await self._next(interaction, answer)

    async def _next(self, interaction: discord.Interaction, done: str) -> None:
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)

    async def _type(self, interaction: discord.Interaction) -> None:
        if self.shown is not None:
            await interaction.response.send_modal(TypeHeardForm(self, self.shown.heard))

    async def _keep(self, interaction: discord.Interaction) -> None:
        await self.answer(interaction, questions.KEEP)

    async def _later(self, interaction: discord.Interaction) -> None:
        if self.shown is not None:
            self.later.add(self.shown.id)
        await self._next(
            interaction, "⏳ Left for later. DMbot will ask again when you check next."
        )


__all__ = ["HeardReviewButton", "heard_review_view", "start_heard_review", "waiting_text"]
