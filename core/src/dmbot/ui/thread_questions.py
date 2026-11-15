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
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.memory import thread_parts as tp
from dmbot.memory.models import DM, HINTED, MemoryRuleError, TooLateToUndo
from dmbot.memory.thread_read import ReadBranch, ReadPart
from dmbot.memory.thread_suggestions import ThreadSuggestionStore, Waiting, unkeep_in
from dmbot.ui import logic
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
HINT_SAID = "just a hint: your lore leaves it open"
NOT_KEPT_SHORT = "name not kept yet, left out"
# Discord's limit on one message is 2,000 characters; a card over it can't be shown, and the queue
# would stick on it (the reviewer on #4500). Each line is cut short, and past this budget the list's
# last lines fold into "…and N more" (all of them stay in the menu below).
CARD_MAX = 1900
LINE_WORDS_MAX, TERMS_SHOWN = 80, 60
AFTER = (
    "Keep it, and it goes on your 🧵 Threads list and the website's Story Graph. Players see it "
    "only if your DM screen lets them."
)
NOTHING_LEFT = (
    "📜 **Story threads**: nothing to check. 🎉 If a lore upload finds more, it shows in "
    "❓ Questions for you."
)
GONE_NOW = "That one was already answered. Here are the ones still waiting."
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
    line: ReadPart | ReadBranch,
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
) -> str:
    """The card: the thread's name, kind and the state it is kept with, its aim, who is involved
    (the lines switched on), and the lore it came from with the quote DMbot checked. `on`: which
    people lines are kept."""
    from dmbot.memory.threads import STATE_WORDS

    count = f" ({at} of {of})" if of > 1 else ""
    state = STATE_WORDS.get(s.state, "").lower()
    kind = KIND_WORDS.get(s.kind, s.kind)
    lines = [
        f"{HEADING}{count}",
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
    if s.quote:
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
    return text[:2000]


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
        # ("keep", batch, name) or ("no", suggestion, name), for ↩ Undo
        self.undo: tuple[str, int | str, str] | None = None

    async def _load(self, interaction: discord.Interaction) -> str | None:
        """The next card's words (its controls drawn), or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = await ThreadSuggestionStore(memory._db).waiting(self.guild_id, self.campaign_id)
        left = [w for w in waiting if w.id not in self.skipped] or waiting
        self.clear_items()
        if not left:
            self.current = None
            if self.undo is not None:
                self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
            return None
        s = left[0]
        if self.current is None or self.current.id != s.id:
            self.name, self.on = "", frozenset(range(len(s.parts)))
            self.lines_on = frozenset(range(len(s.lines)))
        self.current = s
        self.names, self.waiting = {}, frozenset[str]()
        givers = [p.who_id for p in s.reads if p.who_id]
        givers += [e for b in s.branches for _, e in b.parts]
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
        return card_text(
            s, self.names, at, len(waiting), name=self.name, on=self.on, waiting=self.waiting,
            lines_on=self.lines_on,
        )  # fmt: skip

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
                else:
                    await store.unturn(g, c, str(ref))
                self.undo, said = None, undone_text(was)
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
            self.undo = None
            said = f"⚠️ {exc}"
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
