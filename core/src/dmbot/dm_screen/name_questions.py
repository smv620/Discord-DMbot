"""The buttons on a "Did they mean…?" question in the DM screen (#296).

Each button's ID carries the server, the question and the choice (a name's place in the
list, or "keep"). Questions live with the running session, so after a restart, or once
the session ended, a press just says the question expired. Only the campaign's DMs may
answer; the bot does the rest (`DMBot.answer_name_question`).

Register with `bot.add_dynamic_items(NameQuestionButton)` in `setup_hook`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.transcript import questions

log = logging.getLogger(__name__)

KEEP = "keep"
NO_PINGS = discord.AllowedMentions.none()
FAILED = "Something went wrong saving that. Try again in a moment."


class NameQuestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:ask:(?P<guild>[0-9]{1,20}):(?P<question>[0-9a-f]{8}):(?P<pick>[0-2]|keep)",
):
    def __init__(self, guild_id: int, question_id: str, pick: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary
                if pick == KEEP
                else discord.ButtonStyle.primary,
                custom_id=f"dmbot:ask:{guild_id}:{question_id}:{pick}",
            )
        )
        self.guild_id = guild_id
        self.question_id = question_id
        self.pick = pick

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NameQuestionButton:
        label = getattr(item, "label", None) or ""
        return cls(int(match["guild"]), match["question"], match["pick"], label)

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        if interaction.guild_id != self.guild_id or not hasattr(bot, "answer_name_question"):
            await _tell(interaction, questions.EXPIRED)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer, done = await bot.answer_name_question(
                self.guild_id, self.question_id, self.pick, interaction.user.id
            )
        except Exception:
            log.exception("Couldn't save the DM's answer to a name question")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        if done and interaction.message is not None:
            # The question is closed: its message says how, with no buttons left.
            with contextlib.suppress(discord.HTTPException):
                await interaction.message.edit(content=answer, view=None)
        await interaction.followup.send(answer, ephemeral=True, allowed_mentions=NO_PINGS)


async def _tell(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


def question_view(guild_id: int, asked: questions.Asked) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for place, (_, name) in enumerate(asked.options[: len("012")]):
        view.add_item(
            NameQuestionButton(guild_id, asked.id, str(place), questions.option_label(name))
        )
    view.add_item(NameQuestionButton(guild_id, asked.id, KEEP, questions.KEEP_LABEL))
    return view
