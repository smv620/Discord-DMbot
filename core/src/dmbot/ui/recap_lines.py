"""The DM's view of last time's recap in an imported session (#3298): "Session of 24 Nov 2026: 12
lines were read as last time's recap, not as this session's events", with 👀 Show them, which lists
those lines privately, and ↩️ Read them as play, which hands them back. The story reader leaves a
recap read aloud at the start of a session out of what it takes as this session's events (a recap
retells earlier sessions). The note itself is counts only, said once for each session and naming it
(#5232); the lines are shown only to a DM of the campaign, checked on every press, and only the
words of people recorded now. Only the campaign's DMs."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do that."
GONE = "I can't find this session any more. Nothing was changed."
NOTHING = "Nothing to put back: those lines were already read as play."
FAILED = "Something went wrong, so nothing was changed. Try again in a moment."
UNDO = "↩️ Read them as play"
SHOW = "👀 Show them"
NOTHING_TO_SHOW = "No lines of people recorded now are left to show."
_TEMPLATE = r"dmbot:recaplines:undo:(?P<campaign>[0-9a-f]{32}):(?P<session>[0-9a-f]{32})"
_SHOW_TEMPLATE = r"dmbot:recaplines:show:(?P<campaign>[0-9a-f]{32}):(?P<session>[0-9a-f]{32})"


def date_label(started_at: int) -> str:
    return dt.datetime.fromtimestamp(started_at, dt.timezone.utc).strftime("%d %b %Y")  # noqa: UP017


def note(count: int, started_at: int) -> str:
    """For the DM screen, after an imported session was read: which session, how many lines, never
    a line (the button shows them, to the DM only)."""
    lines = "1 line" if count == 1 else f"{count} lines"
    were = "was" if count == 1 else "were"
    it = "it" if count == 1 else "they"
    return (
        f"🔁 Session of {date_label(started_at)}: {lines} {were} read as last time's recap, not as "
        f"this session's events. Press {SHOW} to see which. If {it} {were} really play, press "
        f"{UNDO} and {it} will be read as play."
    )


MORE_ROOM = 40  # characters kept free for "and N more"
LINE_MAX = 200  # one line's words are cut here


def show_text(
    rows: Sequence[tuple[int, str]], names: Mapping[int, str], started_at: int, hidden: int = 0
) -> str:
    """The lines the note was about, for the DM alone (#5232): "speaker (when): words", in the
    order they were said, cut to Discord's limit with "and N more". `rows`: (speaker id, text, in
    order, already only people recorded now) with the time in the text prefix by the caller is not
    needed: the order is the story's. Pure."""
    if not rows:
        return NOTHING_TO_SHOW
    head = f"👀 Lines of the session of {date_label(started_at)} read as last time's recap:"
    out = [head]
    used = len(head)
    shown = 0
    for speaker, text in rows:
        said = " ".join(text.split())
        said = said if len(said) <= LINE_MAX else said[: LINE_MAX - 1].rstrip() + "…"
        line = f"> **{discord.utils.escape_markdown(names.get(speaker) or 'Someone')}:** " + (
            discord.utils.escape_markdown(discord.utils.escape_mentions(said))
        )
        if used + len(line) + 1 > MESSAGE_MAX - MORE_ROOM:
            break
        out.append(line)
        used += len(line) + 1
        shown += 1
    left = len(rows) - shown + hidden
    if left > 0:
        out.append(f"…and {left} more.")
    return "\n".join(out)


def done(count: int) -> str:
    lines = "1 line" if count == 1 else f"{count} lines"
    return (
        f"↩️ {lines} will be read as play in the next few minutes. A recap already written "
        "stays as it is: ask for it again to include them."
    )


class RecapLinesUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, campaign_id: str, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recaplines:undo:{campaign_id}:{session_id}",
            )
        )
        self.campaign_id, self.session_id = campaign_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapLinesUndoButton:
        return cls(match["campaign"], match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction)
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        if guild is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, NOT_YOURS)
            return
        try:
            count = await bot.undo_recap_lines(guild.id, campaign.id, self.session_id)
        except Exception as exc:
            log.warning("Recap lines: the Undo failed (%s)", type(exc).__name__)
            await _tell(interaction, FAILED)
            return
        await _tell(interaction, done(count) if count else NOTHING)


class RecapLinesShowButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_SHOW_TEMPLATE
):
    """👀 Show them: the lines the note is about, privately, to a DM of the campaign (#5232)."""

    def __init__(self, campaign_id: str, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=SHOW,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:recaplines:show:{campaign_id}:{session_id}",
            )
        )
        self.campaign_id, self.session_id = campaign_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapLinesShowButton:
        return cls(match["campaign"], match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction)
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        if guild is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, NOT_YOURS)
            return
        try:
            text = await bot.recap_lines_text(guild, campaign.id, self.session_id)
        except Exception as exc:  # (the type only: the lines are the campaign's words)
            log.warning("Recap lines: the lines could not be shown (%s)", type(exc).__name__)
            await _tell(interaction, FAILED)
            return
        await _tell(interaction, text)


def undo_view(campaign_id: str, session_id: str) -> discord.ui.View:
    """Both buttons of the note: 👀 Show them, then ↩️ Read them as play."""
    view = discord.ui.View(timeout=None)
    view.add_item(RecapLinesShowButton(campaign_id, session_id))
    view.add_item(RecapLinesUndoButton(campaign_id, session_id))
    return view


__all__ = [
    "RecapLinesShowButton",
    "RecapLinesUndoButton",
    "date_label",
    "done",
    "note",
    "show_text",
    "undo_view",
]
