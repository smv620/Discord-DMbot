"""The buttons on a "Did they mean…?" question in the DM screen (#296).

Each answer button's ID carries the server, the question and the choice (a name's place
in the list, or "keep"). Questions live with the running session, so after a restart,
or once the session ended, a press just says the question is closed. Only the
campaign's DMs may answer; the bot does the rest (`DMBot.answer_name_question`).

Once answered, the message keeps an **↩️ Undo** button. Its ID carries the campaign and
the saved change, so it works after a restart; like answering, only the campaign's DMs
may press it.

Register with `bot.add_dynamic_items(NameQuestionButton, NameAnswerUndoButton)`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.transcript import questions
from dmbot.transcript.cleaner import MAX_OPTIONS

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
FAILED = "Something went wrong. Try again in a moment."


class NameQuestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:ask:(?P<guild>[0-9]{1,20}):(?P<question>[0-9a-f]{8}):(?P<pick>[0-2]|keep)",
):
    def __init__(self, guild_id: int, question_id: str, pick: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label or "?",
                style=discord.ButtonStyle.secondary
                if pick == questions.KEEP
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
        await interaction.response.defer()  # the answer edits this message
        try:
            answer, done, undo = await bot.answer_name_question(
                self.guild_id, self.question_id, self.pick, interaction.user.id
            )
        except Exception:
            log.exception("Couldn't save the DM's answer to a name question")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        if not done:  # still open (someone else pressed, or it's being saved)
            await interaction.followup.send(answer, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        view = undo_view(*undo) if undo is not None else None
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=answer, view=view, allowed_mentions=NO_PINGS
            )


class NameAnswerUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:askundo:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Takes back an answer to "Did they mean…?" (the saved spelling or keep rule)."""

    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:askundo:{campaign_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NameAnswerUndoButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        campaigns, memory = getattr(bot, "campaigns", None), getattr(bot, "memory", None)
        guild = interaction.guild
        campaign = (
            await campaigns.get(guild.id, self.campaign_id)
            if guild is not None and campaigns is not None
            else None
        )
        if campaign is None or memory is None:
            await _tell(interaction, questions.EXPIRED)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # DM-only, like answering
            await _tell(interaction, questions.UNDO_ONLY_DM)
            return
        heard = _quoted(interaction.message.content if interaction.message else "")
        await interaction.response.defer()
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except TooLateToUndo as exc:
            await interaction.followup.send(
                f"{exc} Fix the name on its card instead: `/dmbot names`.", ephemeral=True
            )
            return
        except MemoryRuleError:
            await interaction.followup.send(questions.UNDO_FAILED, ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't undo the DM's answer to a name question")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        lookup = getattr(bot, "lookup", None)
        if lookup is not None:
            lookup.mark_stale(campaign.guild_id, campaign.id)  # the next line sees it
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=questions.undone_text(heard), view=None, allowed_mentions=NO_PINGS
            )


_QUOTED = re.compile(r'"([^"]+)"')


def _quoted(answer: str) -> str | None:
    """The heard words in an answer message ('✅ Got it: from now on, "Marin" is…')."""
    found = _QUOTED.search(answer)
    return found.group(1) if found else None


async def _tell(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


def question_view(guild_id: int, asked: questions.Asked) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for place, (_, name) in enumerate(asked.options[:MAX_OPTIONS]):
        view.add_item(
            NameQuestionButton(guild_id, asked.id, str(place), questions.option_label(name))
        )
    view.add_item(
        NameQuestionButton(guild_id, asked.id, questions.KEEP, questions.keep_label(asked.heard))
    )
    return view


def undo_view(campaign_id: str, batch: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(NameAnswerUndoButton(campaign_id, batch))
    return view
