"""📖 Give DMbot your adventure, on the Shared story card (#1698; docs/STORY_MEMORY.md, section 6):
the DM gives one file at a time (PDF, Word, text), DMbot keeps its words for this campaign only,
and the card lists what it holds with a 🗑 Remove on each (one confirmation).

Only when the Shared story switch is on and a DM said here that they have the right to use the
material (#1692, CLAUDE.md IP rule): the button is not drawn otherwise, and every press checks it
again, so an old card can't be used after the switch went off. Only the campaign's DMs, checked
on every press. The file is read by the same process-with-a-time-limit the names screen uses
(`memory.document_reader`), and nothing but its words is kept.

**📖 Read it now** (#1714) is the one place the AI reads the text: once per file, only when a DM
presses it and says yes, into the story plan (`memory.story_plan`). The card shows how far a read
is, **🔄 Refresh** updates it, and **📋 See what was read** scrolls through what was found.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import STORY_GUIDE, STORY_OFF
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.dm_screen.story_mode import COULDNT, _dm_campaign, say
from dmbot.logs import set_log_context
from dmbot.memory import plan_reader
from dmbot.memory.adventure import (
    DOCUMENTS_MAX,
    FILE_MAX_BYTES,
    READ_REPLY_BYTES,
    READ_TIME_S,
    AdventureError,
    Document,
    adventure_text,
)
from dmbot.memory.document_reader import READING, read_document
from dmbot.memory.name_documents import DocumentError, kind_of_file
from dmbot.memory.story_plan import Counts, Listed, ReadState
from dmbot.waiting import (
    SHOW_ELAPSED_AFTER_S,
    SHOW_LAST_CHANGE_AFTER_S,
    ago_text,
    elapsed_text,
    keep_card_current,
)

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
_DOC = r"(?P<doc>[0-9a-f]{32})"
NO_MENTIONS = discord.AllowedMentions.none()
INTRO = "📖 **Your adventure**"
NOTE = (
    "DMbot keeps the text of your files, not the files, for this campaign only.\n"
    "Press **Read it now** and DMbot's AI reads a file once and lists its chapters, places and "
    "people. The list isn't used for anything yet, so your alerts don't change, and players "
    "never see it.\n"
    "None of this is in the campaign's backup: if you restore a backup, give the files again.\n"
    "Remove a file and DMbot forgets it and what it read."
)
EMPTY = "Nothing yet. Press Give a file to add one."
NOT_ON = (
    "Not yet. In Settings, turn on Shared story and say you have the right to use the material."
)
TOO_BIG = "That file is over 10 MB. Try a smaller file, or save a text copy (.txt)."
WRONG_TYPE = "DMbot can read PDF, Word (.docx) and text (.txt, .md) files. Save it as one of those."
NO_FILE = "No file was picked. Press Give a file again and pick one."
DOWNLOAD_FAILED = "DMbot couldn't download that file, and kept nothing. Try again."
ADD_LABEL = "Give a file"
FILE_TYPES = (".pdf", ".docx", ".txt", ".md", ".text")
CHARS_PER_PAGE = 2000  # for "about N pages"


def pages_of(characters: int) -> int:
    return max(1, round(characters / CHARS_PER_PAGE))


def _pages(characters: int) -> str:
    return "page" if pages_of(characters) == 1 else "pages"


NAME_SHOWN = 30  # characters of a file name on the card: twenty lines must fit one message
FULL_LINES = 8  # up to this many documents get who and when; more get the short line

READ_LABEL = "Read it now"
REFRESH_LABEL = "Refresh"
SEE_LABEL = "See what was read"
MATCH_LABEL = "Match names"
PAGE_ENTRIES = 6  # lines of the list on one page
NEEDS_REPLY = (
    "DMbot's AI isn't set up here, so it can't read your adventure. Ask whoever runs DMbot to "
    "set it up."
)
BUSY = "DMbot is already reading that file. Press Refresh to see how far it is."
NOTHING_TO_READ = (
    'Every file DMbot can read has been read. A file that says "give it again" needs its '
    "file given again first."
)
NOTHING_READ = "Nothing was read yet. Press Read it now first."


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def found_line(counts: Counts) -> str:
    return ", ".join(
        (
            _plural(counts.chapters, "chapter", "chapters"),
            _plural(counts.places, "place", "places"),
            _plural(counts.people, "person", "people"),
            _plural(counts.links, '"this needs that" note', '"this needs that" notes'),
        )
    )


STOPPED_WORDS = {
    "file_cap": (
        "This file is longer than DMbot can read in full. What it read is under See what was read."
    ),
    "month_cap": "DMbot has used up its reading for this month. It can carry on next month.",
    "unreachable": "DMbot's AI could not be reached. Press Read it now to try again.",
    "funds": "DMbot can't read right now. Try again later.",
    "time": "It took too long. Press Read it now to try again.",
    "off": "Shared story was turned off. Turn it on again, then press Read it now.",
    "plan": "This campaign's plan doesn't include reading adventures.",
    "error": "Something went wrong. Press Read it now to try again.",
    "price": (
        "DMbot stopped because the AI's price went up. Press **Read it now** later to carry on."
    ),
    "gone": (
        "That file was removed while DMbot was reading it."
        " Give it again, then press **Read it now**."
    ),
}
CARRY_ON = "Press Read it now to carry on."


# The longest silence a read can have and still be allowed to go on: every call may time out, with
# the pauses between tries, up to the failure that ends the read. Past this the card says nothing
# has changed (a read is silent for no longer than this unless it is stuck).
STALL_AFTER_S = (
    plan_reader.FAILURES_BEFORE_STOP * plan_reader.CALL_TIMEOUT_S
    + sum(plan_reader.PAUSES_S[: plan_reader.FAILURES_BEFORE_STOP - 1])
    + 60.0
)


@dataclass(frozen=True, slots=True)
class ReadAges:
    """How long a read has waited for its turn, been running, and sat without a call coming back
    (seconds). Numbers only: nothing of the file."""

    queued_s: float
    running_s: float
    still_s: float


def _hours(seconds: float) -> str:
    whole = int(seconds // 3600)
    return (
        f"{whole} hour{'' if whole == 1 else 's'}" if seconds % 3600 == 0 else elapsed_text(seconds)
    )


def age_words(ages: ReadAges, *, short: bool = False, waiting: bool = False) -> str:
    """How long a read has been waiting or going and when it last moved forward (#2564): "" for a
    short wait, then "running for 2 min", "last change 1 min ago", and past the stall limit a plain
    line that says nothing has changed and when DMbot stops by itself. `short` is the form for a
    card with many files."""
    if waiting:
        return (
            "" if ages.queued_s < SHOW_ELAPSED_AFTER_S else f"asked {ago_text(ages.queued_s)} ago"
        )
    stalled = ages.still_s >= STALL_AFTER_S
    if short:
        if stalled:
            return f"no change for {ago_text(ages.still_s)}"
        return (
            f"running {ago_text(ages.running_s)}"
            if ages.running_s >= SHOW_LAST_CHANGE_AFTER_S
            else ""
        )
    parts = []
    if ages.running_s >= SHOW_ELAPSED_AFTER_S:
        parts.append(f"running for {elapsed_text(ages.running_s)}")
    if stalled:
        parts.append(
            f"no change for {ago_text(ages.still_s)}; DMbot stops by itself after "
            f"{_hours(plan_reader.MAX_RUN_S)} at most, or sooner if the AI keeps failing"
        )
    elif ages.still_s >= SHOW_LAST_CHANGE_AFTER_S:
        parts.append(f"last change {ago_text(ages.still_s)} ago")
    return ", ".join(parts)


def read_line(
    state: ReadState | None,
    counts: Counts | None,
    running: bool,
    *,
    short: bool = False,
    waiting: bool = False,
    ages: ReadAges | None = None,
) -> str:
    """Where a file's reading stands, in plain words ("" when it has not been read). A stopped
    read says what to do next, which depends on why it stopped."""
    if state is None:
        return "" if short else "not read yet"
    if state.status == "done":
        found = found_line(counts or Counts())
        skipped = (
            f", {_plural(state.skipped_parts, 'part', 'parts')} couldn't be read"
            if state.skipped_parts
            else ""
        )
        return "read" if short else f"read: {found}{skipped}"
    if running and waiting:
        late = age_words(ages, short=short, waiting=True) if ages is not None else ""
        words = "waiting" if short else "waiting for a turn"
        return words + (f" ({late})" if late else "")
    if running:
        late = age_words(ages, short=short) if ages is not None else ""
        if short:
            return f"reading {state.percent}%" + (f" ({late})" if late else "")
        return f"reading… {state.percent}%" + (f" ({late})" if late else "")
    stopped = f"stopped at {state.percent}%"
    if short:
        return stopped
    if state.status == "reading":  # nothing is running: DMbot restarted
        return f"{stopped} because DMbot restarted. {CARRY_ON}"
    return f"{stopped}. {STOPPED_WORDS.get(state.stop_reason, CARRY_ON)}"


def card_text(
    documents: list[Document],
    states: dict[str, ReadState] | None = None,
    counts: dict[str, Counts] | None = None,
    running: frozenset[str] = frozenset(),
    waiting: frozenset[str] = frozenset(),
    ages: dict[str, ReadAges] | None = None,
) -> str:
    states, counts = states or {}, counts or {}
    ages = ages or {}
    lines = [INTRO, NOTE]
    if not documents:
        lines.append(EMPTY)
    for n, d in enumerate(documents, 1):
        name = discord.utils.escape_markdown(d.file_name[:NAME_SHOWN])
        size = f"about {pages_of(d.characters)} {_pages(d.characters)}"
        short = len(documents) > FULL_LINES
        if not d.has_text:
            size += ", give DMbot this file again to use it" if not short else ", give it again"
        else:
            status = read_line(
                states.get(d.id),
                counts.get(d.id),
                d.id in running,
                short=short,
                waiting=d.id in waiting,
                ages=ages.get(d.id),
            )
            if status:
                size += f", {status}"
        if not short:
            lines.append(
                f"{n}. **{name}** ({size}), added by <@{d.added_by}> on <t:{d.added_at}:d>"
            )
        else:
            lines.append(f"{n}. **{name}** ({size})")
    if len(documents) >= DOCUMENTS_MAX:
        lines.append(
            f"That is the most DMbot keeps here ({DOCUMENTS_MAX}). Remove one to add more."
        )
    return "\n".join(lines)


def next_to_read(
    documents: list[Document], states: dict[str, ReadState], running: frozenset[str]
) -> Document | None:
    """The first file (in the card's order) that has its text and is not read or being read."""
    for d in documents:
        state = states.get(d.id)
        stuck = state is not None and state.stop_reason == "file_cap"  # it can't go further
        if (
            d.has_text
            and d.id not in running
            and not stuck
            and (state is None or state.status != "done")
        ):
            return d
    return None


def card_view(
    campaign: Campaign,
    documents: list[Document],
    states: dict[str, ReadState] | None = None,
    *,
    can_read: bool = False,
    running: frozenset[str] = frozenset(),
    has_plan: bool = False,
    can_match: bool = False,
    can_guide: bool = False,
) -> discord.ui.View:
    """Remove on each file (rows 0 to 3, up to twenty), and the other buttons on the last row:
    Give a file, Read it now (only when the AI is set up and a file waits), Refresh (while a read
    is going or stopped) and See what was read (once something was read)."""
    states = states or {}
    view = discord.ui.View(timeout=None)
    for n, d in enumerate(documents[:DOCUMENTS_MAX], 1):
        view.add_item(RemoveButton(campaign.id, d.id, n))
    if len(documents) < DOCUMENTS_MAX:
        view.add_item(AddButton(campaign.id, row=4))
    if can_read and next_to_read(documents, states, running) is not None:
        view.add_item(ReadButton(campaign.id, row=4))
    if running or any(s.status != "done" for s in states.values()):
        view.add_item(RefreshButton(campaign.id, row=4))
    if has_plan:
        view.add_item(SeeButton(campaign.id, 0, row=4))
    if can_match:
        view.add_item(MatchButton(campaign.id, row=4))
    if can_guide:
        from dmbot.ui.guide import GuideButton  # (here: the menus import this module's helpers)

        view.add_item(GuideButton(campaign.id, row=4))
    return view


async def _allowed(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if the person is one of its DMs, the switch is on and a DM said here they
    have the right to the material; else they are told why and it is None."""
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return None
    store = interaction.client.campaigns  # type: ignore[attr-defined]
    try:
        said = campaign.story_mode != STORY_OFF and await store.story_confirmed(
            campaign.guild_id, campaign.id
        )
    except Exception:
        log.exception("Couldn't read the Shared story confirmation")
        await say(interaction, COULDNT)
        return None
    if not said:
        await say(interaction, NOT_ON)
        return None
    return campaign


def _store(interaction: discord.Interaction) -> Any:
    return getattr(interaction.client, "adventure", None)


def _plan(interaction: discord.Interaction) -> Any:
    return getattr(interaction.client, "plan", None)


def _running(interaction: discord.Interaction, guild_id: int) -> frozenset[str]:
    reads = getattr(interaction.client, "plan_reads", None) or {}
    return frozenset(doc for guild, doc in reads if guild == guild_id)


def _ages(interaction: discord.Interaction, guild_id: int) -> dict[str, ReadAges]:
    """For each read going now: how long it waited, has run, and sat without a call coming back
    (#2564)."""
    now = time.monotonic()
    clocks = getattr(interaction.client, "plan_clocks", None) or {}
    return {
        doc: ReadAges(now - c.asked, now - c.began, now - c.moved)
        for (guild, doc), c in clocks.items()
        if guild == guild_id
    }


# Cards being kept current, by message: a newer press on the same card takes over from the older
# loop, so two loops never fight over one message (#2564).
_watching: dict[int, int] = {}


def _still_reading(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Whether any of this campaign's files is being read (or waiting for a turn) right now."""
    reads = getattr(interaction.client, "plan_reads", None) or {}
    return any(guild == campaign.guild_id for guild, _doc in reads)


async def watch_card(interaction: discord.Interaction, campaign: Campaign) -> None:
    """After 📖 Read it now or 🔄 Refresh, keep the card current while a read goes (#2564): the
    elapsed time and last movement move without another press, for as long as the interaction's
    token allows (`waiting.EDIT_FOR_S`), then the card says it has stopped updating by itself. The
    end of a read is told on the DM screen as before. Never raises."""
    message = getattr(interaction, "message", None)
    key = message.id if message is not None else interaction.id
    generation = _watching[key] = _watching.get(key, 0) + 1
    if not _still_reading(interaction, campaign):
        return

    async def render() -> tuple[str, discord.ui.View]:
        return await _card(interaction, campaign)

    async def edit(text: str, view: Any) -> None:
        await interaction.edit_original_response(content=text, view=view)

    try:
        await keep_card_current(
            render,
            edit,
            lambda: _still_reading(interaction, campaign),
            current=lambda: _watching.get(key) == generation,
        )
    except Exception:
        log.exception("Couldn't keep the adventure card current")
    finally:
        if _watching.get(key) == generation:
            _watching.pop(key, None)


def _watch_in_background(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Start `watch_card` without holding the button's own answer."""
    work = watch_card(interaction, campaign)
    track = getattr(interaction.client, "track", None)
    if track is not None and track(work, "adventure-card") is not None:
        return
    if track is None:
        asyncio.get_running_loop().create_task(work)
    else:
        work.close()  # shutting down: the work was dropped


async def _card(
    interaction: discord.Interaction, campaign: Campaign
) -> tuple[str, discord.ui.View]:
    docs = await _store(interaction).documents(campaign.guild_id, campaign.id)
    plan = _plan(interaction)
    states: dict[str, ReadState] = {}
    counts: dict[str, Counts] = {}
    if plan is not None:
        try:
            states = await plan.states(campaign.guild_id, campaign.id)
            counts = await plan.counts(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Couldn't read the story plan's state")  # the card still shows the files
    running = _running(interaction, campaign.guild_id)
    waiting = frozenset(
        doc
        for guild, doc in getattr(interaction.client, "plan_waiting", ())
        if guild == campaign.guild_id
    )
    can_read = plan is not None and getattr(interaction.client, "plan_ai", None) is not None
    return (
        card_text(docs, states, counts, running, waiting, _ages(interaction, campaign.guild_id)),
        card_view(
            campaign,
            docs,
            states,
            can_read=can_read,
            running=running,
            has_plan=any(c.chapters + c.places + c.people for c in counts.values()),
            can_match=any(c.places + c.people for c in counts.values()),
            can_guide=campaign.story_mode == STORY_GUIDE
            and any(c.chapters for c in counts.values()),
        ),
    )


class AdventureButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:adv:{_ID}",
):
    """📖 Give DMbot your adventure, on the Shared story card: opens the list."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Give your adventure",
                emoji="📖",
                style=discord.ButtonStyle.primary,
                row=1,
                custom_id=f"dmbot:adv:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AdventureButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        if _store(interaction) is None:
            await interaction.response.send_message(COULDNT, ephemeral=True)
            return
        text, view = await _card(interaction, campaign)
        await interaction.response.send_message(
            text, view=view, ephemeral=True, allowed_mentions=NO_MENTIONS
        )


class AddButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advadd:{_ID}",
):
    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=ADD_LABEL,
                emoji="➕",
                style=discord.ButtonStyle.success,
                row=row,
                custom_id=f"dmbot:advadd:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AddButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.send_modal(AdventureForm(campaign))


class AdventureForm(discord.ui.Modal, title="Give DMbot your adventure"):
    file: discord.ui.Label[AdventureForm] = discord.ui.Label(
        text="Your adventure (PDF, Word or text)",  # 45 characters at most
        description="One file, up to 10 MB. DMbot keeps the text, not the file.",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=900)
        self.campaign = campaign

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("The adventure form failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(COULDNT, ephemeral=True)
        else:
            await interaction.response.send_message(COULDNT, ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        set_log_context(guild_id=interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)  # reading takes a while
        reply = interaction.followup.send
        # Checked again, now: the form may have been open for minutes.
        store = _store(interaction)
        campaigns = interaction.client.campaigns  # type: ignore[attr-defined]
        guild = interaction.guild
        fresh = await campaigns.get(guild.id, self.campaign.id) if guild else None
        if fresh is None or interaction.user.id not in fresh.dm_user_ids or store is None:
            await reply("Only this campaign's DMs can do that.", ephemeral=True)
            return
        upload = cast(discord.ui.FileUpload[AdventureForm], self.file.component)
        if not upload.values:
            await reply(NO_FILE, ephemeral=True)
            return
        attachment = upload.values[0]
        if attachment.size > FILE_MAX_BYTES:
            await reply(TOO_BIG, ephemeral=True)
            return
        if PurePath(attachment.filename).suffix.casefold() not in FILE_TYPES or (
            kind_of_file(attachment.filename) == "unknown"
        ):
            await reply(WRONG_TYPE, ephemeral=True)
            return
        try:
            raw = await attachment.read()
        except discord.HTTPException:
            await reply(DOWNLOAD_FAILED, ephemeral=True)
            return
        began = time.monotonic()
        try:
            async with READING:  # the same limit as the names screen
                text = await read_document(
                    attachment.filename,
                    raw,
                    parse=adventure_text,
                    limit_s=READ_TIME_S,
                    max_reply=READ_REPLY_BYTES,
                )
            await store.add(
                fresh.guild_id, fresh.id, interaction.user.id, attachment.filename, text
            )
        except (DocumentError, AdventureError) as exc:
            await reply(str(exc), ephemeral=True)
            return
        # Counts and seconds only: never the file's name or words.
        log.info(
            "Adventure text kept: %d characters in %.1f s", len(text), time.monotonic() - began
        )
        card, view = await _card(interaction, fresh)
        await reply(f"Kept.\n\n{card}", view=view, ephemeral=True, allowed_mentions=NO_MENTIONS)


class RemoveButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrm:{_ID}:{_DOC}",
):
    """🗑 Remove, one for each document: asks once."""

    def __init__(self, campaign_id: str, document_id: str, number: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"Remove {number}" if number else "Remove",
                emoji="🗑",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advrm:{campaign_id}:{document_id}",
            )
        )
        self.campaign_id, self.document_id = campaign_id, document_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveButton:
        return cls(match["campaign"], match["doc"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        docs = await _store(interaction).documents(campaign.guild_id, campaign.id)
        doc = next((d for d in docs if d.id == self.document_id), None)
        if doc is None:
            text, card = await _card(interaction, campaign)
            await interaction.edit_original_response(
                content=f"Already removed.\n\n{text}", view=card
            )
            return
        view = discord.ui.View(timeout=None)
        view.add_item(RemoveYesButton(campaign.id, doc.id))
        view.add_item(RemoveNoButton(campaign.id))
        name = discord.utils.escape_markdown(doc.file_name[:NAME_SHOWN])
        await interaction.edit_original_response(
            content=(
                f"Remove **{name}**? DMbot will forget the text it kept from it. "
                "You can add the file again later."
            ),
            view=view,
        )


class RemoveYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrmyes:{_ID}:{_DOC}",
):
    def __init__(self, campaign_id: str, document_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Yes, remove it",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:advrmyes:{campaign_id}:{document_id}",
            )
        )
        self.campaign_id, self.document_id = campaign_id, document_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveYesButton:
        return cls(match["campaign"], match["doc"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _dm_campaign(interaction, self.campaign_id)  # (removing needs only a DM)
        if campaign is None:
            return
        store = _store(interaction)
        note = "Removed. DMbot forgot it."
        try:
            await store.remove(
                campaign.guild_id, campaign.id, interaction.user.id, self.document_id
            )
        except AdventureError as exc:
            note = str(exc)
        except Exception:
            log.exception("Couldn't remove an adventure document")
            note = COULDNT
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=f"{note}\n\n{text}", view=view)
        _watch_in_background(interaction, campaign)


class RemoveNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrmno:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep it",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advrmno:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveNoButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=text, view=view)


class ReadButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advread:{_ID}",
):
    """📖 Read it now: asks once, naming the file, then the AI reads it (once, ever)."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=READ_LABEL,
                emoji="📖",
                style=discord.ButtonStyle.primary,
                row=row,
                custom_id=f"dmbot:advread:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReadButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the reads are several trips.
        await interaction.response.defer()
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        plan, bot = _plan(interaction), cast(Any, interaction.client)
        if plan is None or getattr(bot, "plan_ai", None) is None:
            await say(interaction, NEEDS_REPLY)
            return
        refusal = await bot.plan_gate("adventure", campaign.guild_id, campaign, interaction.user.id)
        if refusal:
            await say(interaction, refusal)
            return
        docs = await _store(interaction).documents(campaign.guild_id, campaign.id)
        states = await plan.states(campaign.guild_id, campaign.id)
        running = _running(interaction, campaign.guild_id)
        doc = next_to_read(docs, states, running)
        if doc is None:
            await say(interaction, BUSY if running else NOTHING_TO_READ)
            return
        name = discord.utils.escape_markdown(doc.file_name[:NAME_SHOWN])
        others = any(
            d.id != doc.id
            and d.has_text
            and (states.get(d.id) is None or states[d.id].status != "done")
            for d in docs
        )
        view = discord.ui.View(timeout=None)
        view.add_item(ReadYesButton(campaign.id, doc.id))
        view.add_item(ReadNoButton(campaign.id))
        await interaction.edit_original_response(
            content=(
                f"Read **{name}** now? DMbot sends its text to its AI, once, and lists the "
                "chapters, places and people. It can take a few minutes, longer for a big book. "
                "You can keep playing. It stops by itself at DMbot's reading limit.\n"
                "Nothing pops up when it's done: press **Refresh** to see how far it is."
                + (
                    "\nOther files wait; press Read it now again afterwards for the next one."
                    if others
                    else ""
                )
            ),
            view=view,
        )


class ReadYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advreadyes:{_ID}:{_DOC}",
):
    def __init__(self, campaign_id: str, document_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Yes, read it",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:advreadyes:{campaign_id}:{document_id}",
            )
        )
        self.campaign_id, self.document_id = campaign_id, document_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReadYesButton:
        return cls(match["campaign"], match["doc"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)  # (checked again, now)
        if campaign is None:
            return
        plan, bot = _plan(interaction), cast(Any, interaction.client)
        if plan is None or getattr(bot, "plan_ai", None) is None:
            await interaction.response.send_message(NEEDS_REPLY, ephemeral=True)
            return
        refusal = await bot.plan_gate("adventure", campaign.guild_id, campaign, interaction.user.id)
        if refusal:
            await interaction.response.send_message(refusal, ephemeral=True)
            return
        if (campaign.guild_id, self.document_id) in getattr(bot, "plan_reads", {}):
            await interaction.response.send_message(BUSY, ephemeral=True)
            return
        note = (
            "Reading started. Nothing pops up when it's done: press Refresh to see how far it is."
        )
        await interaction.response.defer()  # the checks below may take longer than Discord waits
        try:
            state = await plan.begin(
                campaign.guild_id, campaign.id, interaction.user.id, self.document_id
            )
            bot.start_plan_read(campaign.guild_id, campaign.id, self.document_id, state.next_chunk)
        except AdventureError as exc:
            note = str(exc)
        except Exception:
            log.exception("Couldn't start reading an adventure")
            note = COULDNT
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=f"{note}\n\n{text}", view=view)


class ReadNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advreadno:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Not now",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advreadno:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReadNoButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=text, view=view)


class RefreshButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrefresh:{_ID}",
):
    """🔄 Refresh: the card again, with how far each read has got."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=REFRESH_LABEL,
                emoji="🔄",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:advrefresh:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RefreshButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=text, view=view)
        _watch_in_background(interaction, campaign)


class BackToFilesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advback:{_ID}",
):
    """↩ Back to your files, from the list of what was read."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Back to your files",
                emoji="↩",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advback:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> BackToFilesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the card are several reads.
        await interaction.response.defer()
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        text, view = await _card(interaction, campaign)
        await interaction.edit_original_response(content=text, view=view)


class MatchButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advmatch:{_ID}",
):
    """🔗 Match names: which of the book's people and places are the same as the table's (#1715)."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=MATCH_LABEL,
                emoji="🔗",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:advmatch:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MatchButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.book_link import show_matches

        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the reads are several trips.
        await interaction.response.defer()
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        await show_matches(interaction, campaign.id)


def finished_text(file_name: str, state: ReadState, counts: Counts | None) -> str:
    """The line posted on the DM screen when a read ends."""
    name = discord.utils.escape_markdown(file_name[:NAME_SHOWN])
    if state.status == "done":
        found = found_line(counts or Counts())
        return (
            f"📖 DMbot finished reading **{name}**: {found}. Open **Shared story** in Settings, "
            "then **Give your adventure**, to see what it read."
        )
    words = STOPPED_WORDS.get(state.stop_reason, CARRY_ON)
    return f"📖 DMbot stopped reading **{name}** at {state.percent}%. {words}"


ICONS = {"chapter": "📖", "place": "📍", "person": "🧑", "link": "🔗"}
LINE_SUMMARY = 110  # characters of a line's summary on the list
LINE_QUOTE = 90
PAGE_CHARS = MESSAGE_MAX - 10  # a little under a whole message


def listing_text(items: list[Listed], offset: int, total: int) -> str:
    """One page of what was read. Everything in it came from the DM's own file, so it is data:
    shown with no mentions and no links of its own."""
    lines = [
        f"📋 **What DMbot read** ({offset + 1} to {offset + len(items)} of {total}). "
        "Everything here comes from your file. It is only a list: it changes nothing and players "
        "never see it."
    ]
    for item in items:
        title = discord.utils.escape_markdown(item.title[:120])
        what = discord.utils.escape_markdown(item.summary[:LINE_SUMMARY])
        if item.kind == "link":
            what = f"this needs that ({what})" if what else "this needs that"
        head = f"{ICONS.get(item.kind, '•')} **{title}**" + (f": {what}" if what else "")
        quote = discord.utils.escape_markdown(item.quote[:LINE_QUOTE])
        lines.append(f"{head}\n> “{quote}”" if quote else head)
    # Escaping can double a line, and the book's own words fill it: the page is cut to what a
    # message holds, and a web address in it is broken so it is not a link.
    return "\n".join(lines).replace("://", ":\u200b//")[:PAGE_CHARS]


class SeeButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advsee:{_ID}:(?P<offset>[0-9]{{1,6}})",
):
    """📋 See what was read (at offset 0), and the Back and More buttons of its pages."""

    def __init__(
        self, campaign_id: str, offset: int, *, row: int | None = None, label: str = SEE_LABEL
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="📋" if offset == 0 and label == SEE_LABEL else None,
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:advsee:{campaign_id}:{offset}",
            )
        )
        self.campaign_id, self.offset = campaign_id, offset

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SeeButton:
        return cls(match["campaign"], int(match["offset"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        # Answer Discord first (#1803): the permission check and the reads are several trips.
        await interaction.response.defer()
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        plan = _plan(interaction)
        if plan is None:
            await say(interaction, COULDNT)
            return
        items, total = await plan.listing(campaign.guild_id, campaign.id, self.offset, PAGE_ENTRIES)
        if not items:
            text, view = await _card(interaction, campaign)
            await interaction.edit_original_response(content=f"{NOTHING_READ}\n\n{text}", view=view)
            return
        view = discord.ui.View(timeout=None)
        if self.offset > 0:
            view.add_item(
                SeeButton(campaign.id, max(0, self.offset - PAGE_ENTRIES), label="◀ Back")
            )
        if self.offset + len(items) < total:
            view.add_item(SeeButton(campaign.id, self.offset + PAGE_ENTRIES, label="More ▶"))
        view.add_item(BackToFilesButton(campaign.id))
        await interaction.edit_original_response(
            content=listing_text(items, self.offset, total),
            view=view,
            # (no suppress_embeds: an edit after a defer can't carry it. `listing_text` already
            # breaks every web address, so nothing here can unfurl.)
            allowed_mentions=NO_MENTIONS,
        )


ADVENTURE_BUTTONS = (
    AdventureButton,
    AddButton,
    RemoveButton,
    RemoveYesButton,
    RemoveNoButton,
    ReadButton,
    ReadYesButton,
    ReadNoButton,
    RefreshButton,
    SeeButton,
    BackToFilesButton,
    MatchButton,
)
