"""The buttons on "Who was speaking just now?" in the DM screen (#53).

Each button's ID carries the server, the question and the choice (an NPC's place in the
list, "narrating", "skip", or "type": **Type it…** opens a form). Questions live with the
running session, so after a restart, or once the session ended, a press just says the
question is closed. Only the campaign's DMs may answer; the bot does the rest
(`DMBot.answer_speaker_question`).

Register with `bot.add_dynamic_items(SpeakerQuestionButton)`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.transcript import speaker_question as sq

log = logging.getLogger(__name__)

FAILED = "Something went wrong. Try again in a moment."


class SpeakerQuestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:who:(?P<guild>[0-9]{1,20}):(?P<question>[0-9a-f]{8}):(?P<pick>[0-2]|narrating|type|skip)",
):
    def __init__(self, guild_id: int, question_id: str, pick: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label or "?",
                row=1 if pick == sq.SKIP else 0,  # the way out sits on a row of its own
                style=discord.ButtonStyle.primary
                if pick not in (sq.TYPE, sq.SKIP)
                else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:who:{guild_id}:{question_id}:{pick}",
            )
        )
        self.guild_id = guild_id
        self.question_id = question_id
        self.pick = pick

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SpeakerQuestionButton:
        label = getattr(item, "label", None) or ""
        return cls(int(match["guild"]), match["question"], match["pick"], label)

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        if interaction.guild_id != self.guild_id or not hasattr(bot, "answer_speaker_question"):
            await _tell(interaction, sq.EXPIRED)
            return
        if self.pick == sq.TYPE:
            await interaction.response.send_modal(TypeForm(self.guild_id, self.question_id))
            return
        await interaction.response.defer()  # the answer edits this message
        await _answer(interaction, self.guild_id, self.question_id, self.pick)


class TypeForm(discord.ui.Modal):
    """**Type it…**: the DM writes the NPC's name. The bot checks it against the names a
    label may take, and checks again that the question is still open and they are the DM."""

    name: discord.ui.TextInput[TypeForm] = discord.ui.TextInput(
        label=sq.FORM_FIELD, placeholder=sq.FORM_HINT, max_length=sq.TYPED_MAX
    )

    def __init__(self, guild_id: int, question_id: str) -> None:
        super().__init__(title=sq.FORM_TITLE)
        self.guild_id, self.question_id = guild_id, question_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await _answer(interaction, self.guild_id, self.question_id, sq.TYPE, self.name.value)


async def _answer(
    interaction: discord.Interaction,
    guild_id: int,
    question_id: str,
    pick: str,
    typed: str | None = None,
) -> None:
    """Save an answer (already deferred): the question's message becomes the answer, or
    a private reply says why not."""
    bot: Any = interaction.client
    try:
        text, done = await bot.answer_speaker_question(
            guild_id, question_id, pick, interaction.user.id, typed
        )
    except Exception:
        log.exception("Couldn't save the DM's answer to a speaker question")
        await interaction.followup.send(FAILED, ephemeral=True)
        return
    if not done:  # still open (a typing mistake, someone else pressed, or being saved)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        return
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=None, allowed_mentions=NO_PINGS)


async def _tell(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


def question_view(guild_id: int, asked: sq.Asked) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(SpeakerQuestionButton(guild_id, asked.id, sq.NARRATING_PICK, sq.NARRATING_LABEL))
    for place, name in enumerate(asked.options):
        view.add_item(SpeakerQuestionButton(guild_id, asked.id, str(place), sq.option_label(name)))
    view.add_item(SpeakerQuestionButton(guild_id, asked.id, sq.TYPE, sq.TYPE_LABEL))
    view.add_item(SpeakerQuestionButton(guild_id, asked.id, sq.SKIP, sq.SKIP_LABEL))
    return view
