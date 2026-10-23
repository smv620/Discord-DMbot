"""The words DMbot read in a picture of text, said in the table chat, with ✏️ Fix (#2450).

When someone who agreed posts a picture of writing (a handout, a note), DMbot replies in the same
chat with "Text in this image:" and the words, so the table can check them. **✏️ Fix** lets the
person who posted it, or a DM, correct the words; the transcript line then says the corrected
words (what DMbot first read is kept, as with every fix) and the change is listed with Undo in
the session's "Name fixes to check" message while the session runs.

The button carries the session, the poster and the line's start time, so it works after a
restart; the server comes from where it is pressed, so a made-up ID finds nothing. Only the poster
or one of the campaign's DMs may use it, checked on every press, and the poster's yes to pictures
must still stand. The new words are checked against the campaign's secret names: the transcript
is open to the whole server, so a secret identity must never be written into it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot import images
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.transcript.cleaner import text_names_a_secret
from dmbot.transcript.stream import escape
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

HEADING = "Text in this image:"
NOT_YOURS = "Only the person who posted this, or your DM, can fix it."
NOT_THERE = "I can't find that line yet. Try again in a minute."
NOT_HERE = "I can't do that here. Try it in your campaign's server."
NOT_TEXT = "That line has no words to fix."
STOPPED = "That player turned off picture reading, so nothing was changed."
# (the same words for anyone, so a refusal never tells a player that a secret name exists)
SECRET = "I can't put that in the transcript, so nothing was changed. Try different words, or ask your DM."
SAME = "That is what it already says."
CHANGED = "That line changed since you opened this. Press ✏️ Fix again."
NOT_WRITTEN = "I couldn't save that. Try again in a moment."
DONE = "✅ Fixed. The transcript says what you wrote."
UNDO_HERE = "Made a mistake? Press Undo in ✏️ Name fixes to check."
MODAL_TITLE = "Fix the words in this picture"
MODAL_LABEL = "What does it say?"

_TEMPLATE = r"dmbot:imgfix:(?P<session>[0-9a-f]{32}):(?P<user>[0-9]{1,20}):(?P<started>[0-9]{1,16})"


def reply_text(words: str) -> str:
    """The reply: the heading, then the words quoted, one `>` per line, shown exactly as read (no
    bold, links or pings) and cut to fit one Discord message."""
    quoted = "\n".join(f"> {escape(line)}" if line else ">" for line in words.split("\n"))
    return fit_message(f"{HEADING}\n{quoted}", limit=MESSAGE_MAX)


class FixTextButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    """✏️ Fix under the reply."""

    def __init__(self, session_id: str, user_id: int, started_ms: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="✏️ Fix",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:imgfix:{session_id}:{user_id}:{started_ms}",
            )
        )
        self.session_id, self.user_id, self.started_ms = session_id, user_id, started_ms

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FixTextButton:
        return cls(match["session"], int(match["user"]), int(match["started"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot, guild = _bot(interaction), interaction.guild
        if guild is None or bot.transcripts is None:
            await _tell(interaction, NOT_HERE)
            return
        who = interaction.user.id
        if who != self.user_id and not await _is_dm(bot, guild.id, self.session_id, who):
            await _tell(interaction, NOT_YOURS)  # (on every press)
            return
        if not bot.consent.has_image_consent(guild.id, self.user_id):
            await _tell(interaction, STOPPED)
            return
        found = await bot.transcripts.image_line(
            guild.id, self.session_id, self.user_id, self.started_ms
        )
        words = images.words_of_line(found[1]) if found is not None else None
        if found is None or words is None:
            await _tell(interaction, NOT_THERE if found is None else NOT_TEXT)
            return
        await interaction.response.send_modal(
            _Words(self.session_id, self.user_id, self.started_ms, found[1], words)
        )


async def _is_dm(bot: Any, guild_id: int, session_id: str, user_id: int) -> bool:
    """Is this person a DM of the campaign the session belongs to?"""
    session = await bot.transcripts.session(guild_id, session_id)
    if session is None:
        return False
    campaign = await bot.campaigns.get(guild_id, session.campaign_id)
    return campaign is not None and user_id in campaign.dm_user_ids


class _Words(discord.ui.Modal, title=MODAL_TITLE):
    def __init__(
        self, session_id: str, user_id: int, started_ms: int, line: str, words: str
    ) -> None:
        super().__init__()
        self.session_id, self.user_id, self.started_ms, self.line = (
            session_id,
            user_id,
            started_ms,
            line,
        )
        self.words: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=MODAL_LABEL,
            style=discord.TextStyle.paragraph,
            default=words[: images.TEXT_MAX],
            max_length=images.TEXT_MAX,
            required=True,
        )
        self.add_item(self.words)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await submit(
            interaction, self.session_id, self.user_id, self.started_ms, self.line,
            str(self.words.value),
        )  # fmt: skip


async def submit(
    interaction: discord.Interaction,
    session_id: str,
    user_id: int,
    started_ms: int,
    shown: str,
    words: str,
) -> None:
    """The corrected words: checked as on the press (the pressing person, the poster's yes, the
    line still the one shown), then written through the same path as every line fix, with Undo."""
    bot, guild = _bot(interaction), interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=False)
    if guild is None or bot.transcripts is None:
        await _tell(interaction, NOT_HERE)
        return
    who = interaction.user.id
    session = await bot.transcripts.session(guild.id, session_id)
    campaign = await bot.campaigns.get(guild.id, session.campaign_id) if session else None
    if campaign is None or (who != user_id and who not in campaign.dm_user_ids):
        await _tell(interaction, NOT_YOURS)
        return
    if not bot.consent.has_image_consent(guild.id, user_id):
        await _tell(interaction, STOPPED)
        return
    found = await bot.transcripts.image_line(guild.id, session_id, user_id, started_ms)
    if found is None:
        await _tell(interaction, NOT_THERE)
        return
    if found[1] != shown:
        await _tell(interaction, CHANGED)
        return
    line = images.line_with_words(shown, words)
    if line is None:
        await _tell(interaction, NOT_TEXT)
        return
    if line == shown:
        await _tell(interaction, SAME)
        return
    lookup = await bot.lookup.get(guild.id, campaign.id) if bot.lookup else None
    if lookup is None or text_names_a_secret(lookup, line):
        await _tell(interaction, SECRET)
        return
    try:
        written = await bot.apply_line_fix(
            guild.id, campaign.id, session_id, user_id, started_ms, line, image=True
        )
    except Exception:
        log.exception("Couldn't write a fix into a picture's line")
        written = False
    if not written:
        await _tell(interaction, NOT_WRITTEN)
        return
    running = any(
        t is not None and t.transcript_session_id == session_id for t in (bot.tables.get(guild.id),)
    )
    await _tell(interaction, DONE + (f"\n{UNDO_HERE}" if running else ""))


def fix_view(session_id: str, user_id: int, started_ms: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(FixTextButton(session_id, user_id, started_ms))
    return view
