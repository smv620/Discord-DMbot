"""📜 Story threads (#4453, quests slice 1; design `docs/design/quests.md` §9.1 and §10.1): the
quests and leads a lore upload found, one card at a time, for the DM to keep or turn down as a
whole. ✅ Keep it puts the thread on the 🧵 Threads list and the Story Graph with its state and the
people the DM left on, in one change (↩ Undo right after); 🚫 is remembered, so DMbot never
suggests it again. Nothing is added by itself (§9.6). Since slice 3 (#4500) the card also lists
what's in it (steps, rewards, help, a time limit, branches), each one the DM can untick.

Asked only in the DM's private chat (the card quotes the DM's own lore and may spoil a backstory);
opened from ❓ Questions for you. Only a DM of the campaign can open it or answer, checked on every
press. Players never see a suggestion.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory import thread_parts as tp
from dmbot.memory import thread_progress as tprog
from dmbot.memory.gate import Viewer
from dmbot.memory.models import DM, HINTED, MemoryRuleError, TooLateToUndo
from dmbot.memory.thread_read import ReadBranch, ReadClue, ReadPart
from dmbot.memory.thread_suggestions import GONE, ThreadSuggestionStore, Waiting, unkeep_in
from dmbot.ui import logic
from dmbot.ui import thread_progress_card as pcard
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _md, _memory

log = logging.getLogger(__name__)

LABEL = "📜 Story threads"
HEADING = "📜 **A story thread DMbot found in your lore**"
HEADING_HEARD = "📜 **A story thread DMbot heard in a session**"  # (#4522, the ux-critic)
# (the ux-critic's words on #4453)
KEEP, RENAME, NOT_ONE, SKIP, UNDO = (
    "✅ Keep it", "✏️ Rename", "🚫 Not a story thread", "Skip ›", "↩ Undo",
)  # fmt: skip
# The kind, then the state it is kept with in the 🧵 Threads list's own words (`STATE_WORDS`), so
# the card never says one thing and the list another
KIND_WORDS = {"quest": "a quest", "hook": "a hook: a lead nobody has taken up yet"}
WHO_HEADING = "**Who's involved:**"
UNTICK = "Untick anyone who doesn't belong in it."
NOT_KEPT_YET = "name not kept yet, so it's left out: keep the name, then add them on the thread"
WHO_HOLDER = "Who's involved"
# Slice 3 (#4500): the thread's steps, rewards, help, time limit and branches, in the 📋 screen's
# own words (`ui/thread_parts`), each one the DM can untick before keeping
PARTS_HEADING = "**What's in it:**"
PARTS_UNTICK = "Untick anything that doesn't belong in it. The rest is kept with it."
PARTS_HOLDER = "What's in it"
BRANCH_WORD = "🌿 Branch"
CLUE_WORD = "🔎 Clue"  # (#4624 6b: the 🔎 Clues screen's icon)
HELD_BY = "held by"
HINT_SAID = "just a hint: your lore leaves it open"
NOT_KEPT_SHORT = "name not kept yet, left out"
# Discord's limit on one message (`MESSAGE_MAX`); a card over it can't be shown, and the queue
# would stick on it (the reviewer on #4500). Each line is cut short, and past this budget the list's
# last lines fold into "…and N more" (all of them stay in the menu below).
CARD_MAX = MESSAGE_MAX - 100  # (room for Discord's own trimming of what we can't count)
LINE_WORDS_MAX, TERMS_SHOWN = 80, 60
AFTER = (
    "Keep it, and it goes on your 🧵 Threads list and the website's Story Graph. Players see it "
    "only if your DM screen lets them."
)
NOTHING_LEFT = (
    "📜 **Story threads**: nothing to check. 🎉 When DMbot finds more in your lore or a session, "
    "they show in ❓ Questions for you."
)
# (#4908: a question can also be gone because its speaker stopped being recorded or its session's
# transcript was deleted, not only answered: one sentence true of both, in Supervisor's words)
GONE_NOW = "This question isn't waiting any more. Showing what is left."
TOO_LATE = "⚠️ Can't undo: it was changed since. Change it in 🧵 Threads."
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
QUOTE_SHOWN = 200
NAME_MAX = 100  # (as the 🧵 list's own form, `ui/threads.NewThreadForm`)


def kept_text(name: str) -> str:
    return f"✅ Kept: **{_md(name)}** is on your 🧵 Threads list."


def turned_down_text(name: str) -> str:
    return f"🚫 Turned down: **{_md(name)}**. DMbot won't suggest it again."


def undone_text(name: str) -> str:
    return f"↩ Undone: **{_md(name)}** is waiting again."


def line_for(
    line: ReadPart | ReadBranch | ReadClue,
    names: Mapping[str, str],
    waiting: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """One line of what's in a suggestion, as (its words, what it is): "100 gold" / "Reward from
    Hlin Trollbane, when the job's done". The kinds and states are the 📋 screen's words. A branch
    names its people, as the thread's own are named above it; a name the DM hasn't kept is said to
    be left out, as the keep leaves it out (`waiting`)."""
    from dmbot.ui.thread_parts import DUE_SAID, KIND_WORDS, LIMIT_SAID, STATE_SAID

    def named(entity_id: str | None) -> str:
        name = names.get(entity_id or "", "")
        return f"{name} ({NOT_KEPT_SHORT})" if name and entity_id in waiting else name

    words = logic.shorten(line.name if isinstance(line, ReadBranch) else line.words, LINE_WORDS_MAX)
    if isinstance(line, ReadClue):  # (#4624 6b: kept as a clue to the thread, hidden for now)
        holders = [named(e) for e in line.who_ids if e in names]
        return words, CLUE_WORD + (f" · {HELD_BY} {', '.join(holders)}" if holders else "")
    if isinstance(line, ReadBranch):
        what = f"{BRANCH_WORD}, {HINT_SAID}" if line.state == HINTED else BRANCH_WORD
        people = [f"{named(e)} ({part})" for part, e in line.parts if e in names]
        return words, what + (f" · {', '.join(people)}" if people else "")
    who = named(line.who_id)
    # (lent help and a gift say it the 🧵 card's way: "Lent by …", "Given by …", the ux-critic)
    if line.kind == tp.HELP and line.state == "lent":
        what = f"Lent by {who}" if who else "Lent"
        terms = logic.shorten(line.terms, TERMS_SHOWN)
        return words, what + (f", to return: {terms}" if terms else ", to give back")
    if line.kind == tp.REWARD_GIVEN:
        return words, (f"Given by {who}" if who else "Given") + ", not promised"
    what = KIND_WORDS[line.kind] + (f" from {who}" if who else "")
    if line.due:
        what += f", {DUE_SAID[line.due]}"
    if line.kind == tp.HELP:
        what += f", {STATE_SAID[line.state]}"
    if line.limit_kind:
        what += f", {LIMIT_SAID[line.limit_kind]}"
    return words, what


def card_text(
    s: Waiting,
    names: Mapping[str, str],
    at: int,
    of: int,
    *,
    name: str = "",
    on: frozenset[int],
    waiting: frozenset[str] = frozenset(),
    lines_on: frozenset[int] | None = None,
    heard: Sequence[str] = (),
) -> str:
    """The card: the thread's name, kind and the state it is kept with, its aim, who is involved
    (the lines switched on), and the lore it came from with the quote DMbot checked. `on`: which
    people lines are kept. A suggestion from a session (#4522) keeps no quote: `heard` is its
    lines as the transcript has them now, read when the card is drawn."""
    from dmbot.memory.threads import STATE_WORDS

    count = f" ({at} of {of})" if of > 1 else ""
    state = STATE_WORDS.get(s.state, "").lower()
    kind = KIND_WORDS.get(s.kind, s.kind)
    lines = [
        f"{HEADING if s.session_id is None else HEADING_HEARD}{count}",
        f"**{_md(name or s.name)}** · {kind}, {state} (you can change that after keeping it)",
    ]
    if s.aim:
        lines.append(f"What it's about: {_md(s.aim)}")
    # (the part words are the ones the thread's own card shows once kept: `threads.ROLES`)
    people = [(i, names[who], part) for i, (part, who) in enumerate(s.parts) if who in names]
    if people:
        lines.append(WHO_HEADING)
        for i, who, part in people:
            if s.parts[i][1] in waiting:  # (a name the DM hasn't kept: never kept with it)
                lines.append(f"• ~~{_md(who)} ({part})~~ ({NOT_KEPT_YET})")
            else:
                lines.append(
                    f"• {_md(who)} ({part})" if i in on else f"• ~~{_md(who)} ({part})~~ (left out)"
                )
        lines.append(UNTICK)
    parts: list[str] = []
    for i, line in enumerate(s.lines):
        words, what = line_for(line, names, waiting)
        said = f"{_md(words)} ({_md(what)})"
        kept = lines_on is None or i in lines_on
        parts.append(f"• {said}" if kept else f"• ~~{said}~~ (left out)")
    tail = []
    if s.session_id is not None:
        where = f"Heard in **{_md(s.upload)}**" if s.upload else "Heard in a session"
        tail.append(f"{where}:" if heard else f"{where}.")
        if heard:
            tail.append(f"> {_md(logic.shorten(' … '.join(heard), QUOTE_SHOWN))}")
    elif s.quote:
        tail.append(f"From **{_md(s.upload)}**:" if s.upload else "From your lore:")
        tail.append(f"> {_md(logic.shorten(s.quote, QUOTE_SHOWN))}")
    tail.append(AFTER)

    def joined(shown: list[str]) -> str:
        middle = [PARTS_HEADING, *shown, PARTS_UNTICK] if shown else []
        return "\n".join([*lines, *middle, *tail])

    text, shown = joined(parts), list(parts)
    while len(text) > CARD_MAX and shown:  # (past Discord's limit: the last lines fold)
        shown = shown[:-1]
        more = f"• …and {len(parts) - len(shown)} more: see the menu below."
        text = joined([*shown, more])
    return fit_message(text)


def session_label(started_at: int) -> str:
    """The card's "Heard in **the session of 14 Nov 2026**" (the session's start, UTC, as the
    recap list says it)."""
    from dmbot.ui.recap_again import date_label

    return f"the session of {date_label(started_at)}"


def threads_found_text(made: int, stopped: bool, label: str, changes: int = 0) -> str:
    """The one counts line in the DM Bot channel after a session (#4522, #3874): counts only, no
    thread's name (the channel is the campaign's people's), and the session by its date (an
    import may be an old one, so never "the last session"). `changes`: progress lines on threads
    the campaign has (5b, ruling D). Its 📜 button is for a DM."""
    many = "possible story thread" if made == 1 else "possible story threads"
    # (guesses, as "possible story threads" are; and never "you": the whole campaign reads it)
    moved = (
        "possible update to a story thread" if changes == 1 else "possible updates to story threads"
    )
    found = " and ".join(
        part
        for part in (f"{made} {many}" if made else "", f"{changes} {moved}" if changes else "")
        if part
    )
    if found:
        line = (
            f"📜 DMbot heard {found} in {label}. A DM can keep or turn down each one: "
            f"press {LABEL}."
        )
        return line + (" It stopped early, so it may have missed some." if stopped else "")
    return f"📜 DMbot stopped early reading {label} for story threads, so it may have missed some."


HEARD_MAX = 3  # lines shown under a session suggestion: its evidence, never the scene


async def _heard(
    interaction: discord.Interaction, guild_id: int, s: Waiting, agreed: Collection[int]
) -> list[str]:
    """A session suggestion's lines, read from its transcript now (#4522): found by when each
    began and who said it (never by place: deleting an earlier line moves the places), only lines
    still there, by one of its speakers who still agrees. Nothing when the transcript can't be
    read (the card still shows the suggestion; its evidence is only missing)."""
    transcripts = getattr(interaction.client, "transcripts", None)
    if s.session_id is None or not s.line_starts or transcripts is None:
        return []
    try:
        lines = await transcripts.lines(guild_id, s.session_id)
    except Exception as exc:
        log.warning("Story threads card: couldn't read a session's lines (%s)", type(exc).__name__)
        return []
    return heard_lines(lines, s, agreed)


def heard_lines(lines: Sequence[Any], s: Waiting, agreed: Collection[int]) -> list[str]:
    """Pure: the transcript lines a session suggestion points at, at most `HEARD_MAX`."""
    starts, speakers = set(s.line_starts), set(s.speaker_ids) & set(agreed)
    out: list[str] = []
    for line in lines:
        if line.started_ms in starts and line.user_id in speakers and line.text.strip():
            out.append(" ".join(line.text.split()))
            if len(out) >= HEARD_MAX:
                break
    return out


class ThreadQuestions(_Menu):
    """One suggestion at a time: Keep it, Rename, Not a story thread, Skip, and Undo right after."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id, self.guild_id = campaign_id, guild_id
        self.current: Waiting | None = None
        self.skipped: set[str] = set()
        self.name = ""  # the DM's own name for the current one, from ✏️ Rename
        self.on: frozenset[int] = frozenset()
        self.lines_on: frozenset[int] = frozenset()
        self.names: dict[str, str] = {}
        # ("keep", batch, name) or ("no", suggestion, name), for ↩ Undo; for progress (#4522 5b)
        # ("progress", batch, name) or ("progress_no", line ids, name)
        self.undo: tuple[Any, ...] | None = None
        # The second stage, once no new thread waits (#4522 5b part 3): one thread's progress
        self.progress: list[tprog.Line] = []
        self.progress_on: frozenset[str] = frozenset()
        self.progress_name = ""
        self.skipped_threads: set[str] = set()

    async def _load(self, interaction: discord.Interaction) -> str | None:
        """The next card's words (its controls drawn), or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        # (the consent sweep first: a session suggestion quoting someone who stopped never shows;
        # who counts is the campaign's `recorded_in`, so a speaker an import's uploader confirmed
        # counts here as in the reading, the reviewer on #4522)
        consent = getattr(interaction.client, "consent", None)
        agreed = (
            await consent.recorded_in(self.guild_id, self.campaign_id)
            if consent is not None
            else None
        )
        waiting = await ThreadSuggestionStore(memory._db).waiting(
            self.guild_id, self.campaign_id, opted_in=agreed
        )
        left = [w for w in waiting if w.id not in self.skipped] or waiting
        self.clear_items()
        if not left:
            self.current = None
            return await self._load_progress(interaction, agreed)
        s = left[0]
        if self.current is None or self.current.id != s.id:
            self.name, self.on = "", frozenset(range(len(s.parts)))
            self.lines_on = frozenset(range(len(s.lines)))
        self.current = s
        self.names, self.waiting = {}, frozenset[str]()
        givers = [p.who_id for p in s.reads if p.who_id]
        givers += [e for b in s.branches for _, e in b.parts]
        givers += [e for c in s.clues for e in c.who_ids]
        for who in [*(w for _, w in s.parts), *givers]:
            e = await memory.entity(self.guild_id, self.campaign_id, who)
            if e is not None:
                self.names[who] = e.name
                if e.status != "confirmed":
                    self.waiting |= {who}
        self.add_item(_Button(self._keep, label=KEEP, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._rename, label=RENAME, style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._not_one, label=NOT_ONE, style=discord.ButtonStyle.danger))
        if len(left) > 1:
            self.add_item(_Button(self._skip, label=SKIP, style=discord.ButtonStyle.secondary))
        if self.undo is not None:
            self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
        people = [
            (i, who)
            for i, (_, who) in enumerate(s.parts)
            if who in self.names and who not in self.waiting
        ]
        if people:
            self.who = _Select(
                self._who_picked,
                placeholder=WHO_HOLDER,
                min_values=0,
                max_values=len(people),
                options=[
                    discord.SelectOption(
                        label=logic.shorten(self.names[who], logic.OPTION_LABEL_MAX),
                        description=logic.shorten(s.parts[i][0], logic.OPTION_LABEL_MAX),
                        value=str(i),
                        default=i in self.on,
                    )
                    for i, who in people
                ],
                row=1,
            )
            self.add_item(self.who)
        if s.lines:
            self.what = _Select(
                self._lines_picked,
                placeholder=PARTS_HOLDER,
                min_values=0,
                max_values=len(s.lines),
                options=[
                    discord.SelectOption(
                        label=logic.shorten(words, logic.OPTION_LABEL_MAX),
                        description=logic.shorten(what, logic.DESCRIPTION_MAX),
                        value=str(i),
                        default=i in self.lines_on,
                    )
                    for i, (words, what) in enumerate(
                        line_for(x, self.names, self.waiting) for x in s.lines
                    )
                ],
                row=2,
            )
            self.add_item(self.what)
        at = waiting.index(s) + 1
        heard = await _heard(interaction, self.guild_id, s, agreed or ())
        return card_text(
            s, self.names, at, len(waiting), name=self.name, on=self.on, waiting=self.waiting,
            lines_on=self.lines_on, heard=heard,
        )  # fmt: skip

    async def _load_progress(
        self, interaction: discord.Interaction, agreed: Collection[int] | None
    ) -> str | None:
        """The second stage (#4522 5b part 3): the progress lines on one thread the campaign has,
        with the consent sweep first; None when none waits."""
        memory = _memory(interaction)
        if memory is None:
            return None
        lines = await tprog.ThreadProgressStore(memory._db).waiting(
            self.guild_id, self.campaign_id, opted_in=agreed
        )
        by_thread: dict[str, list[tprog.Line]] = {}
        for line in lines:
            by_thread.setdefault(line.thread_id, []).append(line)
        order = list(by_thread)
        todo = [t for t in order if t not in self.skipped_threads]
        if not todo:  # (every one skipped: start over, so Skip keeps working)
            self.skipped_threads.clear()
            todo = order
        if not todo:
            self.progress = []
            if self.undo is not None:
                self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
            return None
        thread_id = todo[0]
        # (at most `SHOWN` a card: a press answers only what the DM can see and untick; the rest
        # of a long list shows on the next card for the same thread)
        now = by_thread[thread_id][: pcard.SHOWN]
        ids = frozenset(x.id for x in now)
        before = frozenset(x.id for x in self.progress)
        # (a list that changed between presses keeps the DM's unticks; a new line starts ticked)
        self.progress_on = (self.progress_on & ids) | (ids - before)
        self.progress = now
        thread = await memory.entity(self.guild_id, self.campaign_id, thread_id)
        self.progress_name = thread.name if thread is not None else "a story thread"
        kind = thread.type if thread is not None else ""
        parts = await memory.parts_of_threads(
            self.guild_id, self.campaign_id, [thread_id], Viewer.the_dm()
        )
        words = {p.id: p.words for p in parts}
        # (a found clue's line names the clue and its finders: #4624 6c; ids never collide)
        for x in now:
            for entry_id in (x.change.clue_id, *x.change.finder_ids):
                if entry_id and entry_id not in words:
                    entry = await memory.entity(self.guild_id, self.campaign_id, entry_id)
                    if entry is not None:
                        words[entry_id] = entry.name
        green, grey = discord.ButtonStyle.success, discord.ButtonStyle.secondary
        self.add_item(_Button(self._keep_progress, label=pcard.KEEP, style=green))
        self.add_item(
            _Button(self._no_progress, label=pcard.NONE, style=discord.ButtonStyle.danger)
        )
        if len(todo) > 1:
            self.add_item(_Button(self._skip_progress, label=pcard.SKIP, style=grey))
        if self.undo is not None:
            self.add_item(_Button(self._undo, label=UNDO, style=grey))
        shown = now
        self.progress_menu = _Select(
            self._progress_picked,
            placeholder=pcard.HOLDER,
            min_values=0,
            max_values=len(shown),
            options=[
                discord.SelectOption(
                    label=logic.shorten(
                        pcard.line_text(x.change, words, kind), logic.OPTION_LABEL_MAX
                    ),
                    value=x.id,
                    default=x.id in self.progress_on,
                )
                for x in shown
            ],
            row=1,
        )
        self.add_item(self.progress_menu)
        heard = session_label(now[0].session_at) if now[0].session_at else ""
        return pcard.card_text(
            self.progress_name, now, words, self.progress_on,
            at=order.index(thread_id) + 1, of=len(order), heard=heard, kind=kind,
            sessions=len({x.session_id for x in now}),
        )  # fmt: skip

    async def _progress_picked(self, interaction: discord.Interaction) -> None:
        self.progress_on = frozenset(str(v) for v in self.progress_menu.values)
        await self._show(interaction, "")

    async def _skip_progress(self, interaction: discord.Interaction) -> None:
        if self.progress:
            self.skipped_threads.add(self.progress[0].thread_id)
        await self._show(interaction, "")

    async def _keep_progress(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "keep_progress")

    async def _no_progress(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "no_progress")

    async def _who_picked(self, interaction: discord.Interaction) -> None:
        self.on = frozenset(int(v) for v in self.who.values)
        await self._show(interaction, "")

    async def _lines_picked(self, interaction: discord.Interaction) -> None:
        self.lines_on = frozenset(int(v) for v in self.what.values)
        await self._show(interaction, "")

    async def _skip(self, interaction: discord.Interaction) -> None:
        if self.current is not None:
            self.skipped.add(self.current.id)
        await self._show(interaction, "")

    async def _rename(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RenameForm(self))

    async def _keep(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "keep")

    async def _not_one(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "no")

    async def _undo(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "undo")

    async def _show(self, interaction: discord.Interaction, said: str) -> None:
        await _answer_first(interaction, in_place=True)
        # (every press draws a card that quotes the DM's lore: who presses is checked each time)
        if await _dm_campaign(interaction, self.campaign_id) is None:
            return
        shown = await self._load(interaction)
        if shown is None:
            if self.undo is None:
                self.stop()
            text = f"{said}\n\n{NOTHING_LEFT}" if said else NOTHING_LEFT
            await _replace(interaction, text, self if self.undo is not None else None)
            return
        await _replace(interaction, f"{said}\n\n{shown}" if said else shown, self)

    async def _answer(self, interaction: discord.Interaction, what: str) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.guild_id = campaign.guild_id  # (the campaign may have moved to another server)
        g, c = self.guild_id, self.campaign_id
        store = ThreadSuggestionStore(memory._db)
        s, said = self.current, GONE_NOW
        try:
            if what == "undo" and self.undo is not None:
                how, ref, was = self.undo
                if how == "keep":  # (the thread goes and the suggestion waits, in one write)
                    await memory.undo(g, c, int(ref), after=unkeep_in(int(ref)))
                    said = undone_text(was)
                elif how == "progress":  # (the changes go and every line waits, in one write)
                    await memory.undo(g, c, int(ref), after=tprog.unkeep_in(int(ref)))
                    said = pcard.undone_text(was)
                elif how == "progress_no":
                    await tprog.ThreadProgressStore(memory._db).unturn(g, c, list(ref))
                    said = pcard.undone_text(was)
                else:
                    await store.unturn(g, c, str(ref))
                    said = undone_text(was)
                self.undo = None
            elif what == "keep_progress" and self.progress:
                on = [x.id for x in self.progress if x.id in self.progress_on]
                off = [x.id for x in self.progress if x.id not in self.progress_on]
                progress_store = tprog.ThreadProgressStore(memory._db)
                moved_batch: int | None = None
                if on:  # (the unticked ones are turned down inside the keep: one change)
                    moved = await memory.keep_thread_progress(g, c, on, source=DM, turn_down=off)
                    moved_batch = moved.batch
                    if moved_batch is not None:
                        self.undo = ("progress", moved_batch, self.progress_name)
                        said = pcard.kept_text(self.progress_name, moved.value)
                    else:
                        said = pcard.NONE_FIT
                if moved_batch is None and off:  # (nothing kept: unticked means it didn't happen)
                    await progress_store.turn_down(g, c, off)
                    self.undo = ("progress_no", tuple(off), self.progress_name)
                    if not on:
                        said = pcard.none_text(self.progress_name)
            elif what == "no_progress" and self.progress:
                ids = [x.id for x in self.progress]
                await tprog.ThreadProgressStore(memory._db).turn_down(g, c, ids)
                self.undo = ("progress_no", tuple(ids), self.progress_name)
                said = pcard.none_text(self.progress_name)
            elif what == "keep" and s is not None:
                name = self.name or s.name
                kept = await memory.keep_thread(
                    g, c, name, s.kind, s.state, aim=s.aim,
                    parts=[p for i, p in enumerate(s.parts) if i in self.on],
                    source=DM, suggestion_id=s.id,
                    leave_off=[i for i in range(len(s.lines)) if i not in self.lines_on],
                )  # fmt: skip
                self.undo = ("keep", kept.batch, name) if kept.batch is not None else None
                said = kept_text(name)
            elif what == "no" and s is not None:
                self.undo = None
                if await store.turn_down(g, c, s.id):
                    self.undo, said = ("no", s.id, s.name), turned_down_text(s.name)
        except TooLateToUndo:
            self.undo, said = None, TOO_LATE
        except MemoryRuleError as exc:
            # (a double press, an answer from elsewhere, or a forget that took it: the earlier Undo
            # stays offered; a gone one says the words true of all three, #4908)
            said = GONE_NOW if str(exc) == GONE else f"⚠️ {exc}"
        await self._show(interaction, said)


class RenameForm(discord.ui.Modal, title="Name this thread"):
    name: discord.ui.TextInput[RenameForm] = discord.ui.TextInput(label="Name", max_length=NAME_MAX)

    def __init__(self, menu: ThreadQuestions) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.menu = menu
        current = menu.current
        self.name.default = (menu.name or (current.name if current else ""))[:NAME_MAX] or None

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = " ".join(str(self.name.value or "").split())
        if name and name.isprintable():
            self.menu.name = name
        await self.menu._show(interaction, "")


class ThreadQuestionsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:threads:ask:(?P<campaign>[0-9a-f]{32})",
):
    """📜 Story threads, from the Questions message; works after a restart (the campaign is in the
    button's ID). The card opens privately, for a DM only."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:threads:ask:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ThreadQuestionsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_thread_questions(interaction, self.campaign_id)


async def start_thread_questions(interaction: discord.Interaction, campaign_id: str) -> None:
    # A new private message (the Questions message is never replaced).
    await interaction.response.defer(ephemeral=True, thinking=True)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    view = ThreadQuestions(campaign.id, campaign.guild_id)
    shown = await view._load(interaction)
    if shown is None:
        await _tell(interaction, NOTHING_LEFT)
        return
    await _send(interaction, shown, view)


__all__ = ["ThreadQuestions", "ThreadQuestionsButton", "card_text", "start_thread_questions"]
