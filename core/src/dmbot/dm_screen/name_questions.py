"""The buttons on a "Did they mean…?" question in the DM screen (#296).

Each answer button's ID carries the server, the question and the choice (a name's place
in the list, "keep", or "type": **Type it…** opens a form for the name, #503).
Questions live with the running session, so after a restart, or once the session ended,
a press just says the question is closed. Only the campaign's DMs may answer; the bot
does the rest (`DMBot.answer_name_question`).

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
from dmbot.transcript import fix_notes, questions
from dmbot.transcript.cleaner import MAX_OPTIONS

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
FAILED = "Something went wrong. Try again in a moment."


class NameQuestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:ask:(?P<guild>[0-9]{1,20}):(?P<question>[0-9a-f]{8}):(?P<pick>[0-2]|keep|type)",
):
    def __init__(self, guild_id: int, question_id: str, pick: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label or "?",
                style=discord.ButtonStyle.secondary
                if pick in (questions.KEEP, questions.TYPE)
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
        if self.pick == questions.TYPE:
            why, heard = bot.can_type_answer(self.guild_id, self.question_id, interaction.user.id)
            if why is not None:
                await _tell(interaction, why)
                return
            await interaction.response.send_modal(
                TypeNameForm(self.guild_id, self.question_id, heard)
            )
            return
        await interaction.response.defer()  # the answer edits this message
        await _answer(interaction, self.guild_id, self.question_id, self.pick)


class TypeNameForm(discord.ui.Modal):
    """**Type it…**: the DM writes the name themselves (#503). The bot checks it with
    the names list's rules, and checks again that the question is still open, they're
    the DM, and the speaker is still recorded (the form may sit open a while)."""

    name: discord.ui.TextInput[TypeNameForm] = discord.ui.TextInput(
        label=questions.FORM_FIELD,
        placeholder=questions.FORM_HINT,
        max_length=questions.TYPED_MAX,
    )

    def __init__(self, guild_id: int, question_id: str, heard: str = "") -> None:
        super().__init__(title=questions.FORM_TITLE)
        self.guild_id, self.question_id = guild_id, question_id
        self.name.default = heard[: questions.TYPED_MAX] or None  # one letter to fix

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # the answer edits the question's message
        await _answer(interaction, self.guild_id, self.question_id, questions.TYPE, self.name.value)


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
        answer, done, undo = await bot.answer_name_question(
            guild_id, question_id, pick, interaction.user.id, typed
        )
    except Exception:
        log.exception("Couldn't save the DM's answer to a name question")
        await interaction.followup.send(FAILED, ephemeral=True)
        return
    if not done:  # still open (a typing mistake, someone else pressed, or being saved)
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
        answer = interaction.message.content if interaction.message else ""
        heard = _quoted(answer)
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
        line_back: bool | None = None
        if hasattr(bot, "answer_undone"):  # the line it fixed goes back too (#503)
            try:
                line_back = await bot.answer_undone(campaign.guild_id, campaign.id, self.batch)
            except Exception:
                log.exception("Couldn't put a line back after undoing an answer")
                line_back = False
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=questions.undone_text(
                    heard, new_name=questions.NEW_NAME in answer, line_kept=line_back is False
                ),
                view=None,
                allowed_mentions=NO_PINGS,
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
    view.add_item(NameQuestionButton(guild_id, asked.id, questions.TYPE, questions.TYPE_LABEL))
    view.add_item(
        NameQuestionButton(guild_id, asked.id, questions.KEEP, questions.keep_label(asked.heard))
    )
    return view


def undo_view(campaign_id: str, batch: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(NameAnswerUndoButton(campaign_id, batch))
    return view


class FixUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:fixundo:(?P<guild>[0-9]{1,20}):(?P<note>[0-9a-f]{8})",
):
    """Undo one name fix in the "✏️ Name fixes to check" message (#296). The notes
    live with the running session, so after a restart, or once it ended, a press says
    so. Only the campaign's DMs may press it; the bot does the rest
    (`DMBot.undo_fix`)."""

    def __init__(self, guild_id: int, note_id: str, label: str = "Undo") -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:fixundo:{guild_id}:{note_id}",
            )
        )
        self.guild_id = guild_id
        self.note_id = note_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FixUndoButton:
        return cls(int(match["guild"]), match["note"], getattr(item, "label", None) or "Undo")

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        if interaction.guild_id != self.guild_id or not hasattr(bot, "undo_fix"):
            await _tell(interaction, fix_notes.EXPIRED)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer, allow = await bot.undo_fix(self.guild_id, self.note_id, interaction.user.id)
        except Exception:
            log.exception("Couldn't undo a name fix")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        extra: dict[str, Any] = {}
        if allow is not None:  # a press by mistake can be taken back
            extra["view"] = AllowAgain(self.guild_id, *allow)
        await interaction.followup.send(answer, ephemeral=True, allowed_mentions=NO_PINGS, **extra)


class AllowAgain(discord.ui.View):
    """Under the private "Undone" reply: takes back the keep-as-heard rule it saved."""

    def __init__(self, guild_id: int, campaign_id: str, batch: int) -> None:
        super().__init__(timeout=600)
        self.guild_id, self.campaign_id, self.batch = guild_id, campaign_id, batch
        button: discord.ui.Button[AllowAgain] = discord.ui.Button(
            label=fix_notes.ALLOW_AGAIN, emoji="↪️", style=discord.ButtonStyle.secondary
        )
        button.callback = self._allow  # type: ignore[method-assign]
        self.add_item(button)

    async def _allow(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        try:
            answer = await bot.allow_fix_again(
                self.guild_id, self.campaign_id, self.batch, interaction.user.id
            )
        except Exception:
            log.exception("Couldn't take back a keep-as-heard rule")
            await _tell(interaction, FAILED)
            return
        self.stop()
        await interaction.response.edit_message(content=answer, view=None)


def fix_notes_view(guild_id: int, notes: list[fix_notes.Note]) -> discord.ui.View | None:
    """One Undo per fix not undone yet, with the fix's number for the whole session."""
    view = discord.ui.View(timeout=None)
    for note in notes:
        if not note.undone:
            view.add_item(FixUndoButton(guild_id, note.id, f"Undo {note.number}"))
    return view if view.children else None
