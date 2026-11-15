"""📜 Story threads (#4453, quests slice 1; design `docs/design/quests.md` §9.1 and §10.1): the
quests and leads a lore upload found, one card at a time, for the DM to keep or turn down as a
whole. ✅ Keep it puts the thread on the 🧵 Threads list and the Story Graph with its state and the
people the DM left on, in one change (↩ Undo right after); 🚫 is remembered, so DMbot never
suggests it again. Nothing is added by itself (§9.6).

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

from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.memory.thread_suggestions import ThreadSuggestionStore, Waiting
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
WHO_HOLDER = "Who's involved"
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


def card_text(
    s: Waiting, names: Mapping[str, str], at: int, of: int, *, name: str = "", on: frozenset[int]
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
            lines.append(
                f"• {_md(who)} ({part})" if i in on else f"• ~~{_md(who)} ({part})~~ (left out)"
            )
        lines.append(UNTICK)
    if s.quote:
        lines.append(f"From **{_md(s.upload)}**:" if s.upload else "From your lore:")
        lines.append(f"> {_md(logic.shorten(s.quote, QUOTE_SHOWN))}")
    lines.append(AFTER)
    return "\n".join(lines)


class ThreadQuestions(_Menu):
    """One suggestion at a time: Keep it, Rename, Not a story thread, Skip, and Undo right after."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id, self.guild_id = campaign_id, guild_id
        self.current: Waiting | None = None
        self.skipped: set[str] = set()
        self.name = ""  # the DM's own name for the current one, from ✏️ Rename
        self.on: frozenset[int] = frozenset()
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
        self.current = s
        self.names = {}
        for _, who in s.parts:
            e = await memory.entity(self.guild_id, self.campaign_id, who)
            if e is not None:
                self.names[who] = e.name
        self.add_item(_Button(self._keep, label=KEEP, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._rename, label=RENAME, style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._not_one, label=NOT_ONE, style=discord.ButtonStyle.danger))
        if len(left) > 1:
            self.add_item(_Button(self._skip, label=SKIP, style=discord.ButtonStyle.secondary))
        if self.undo is not None:
            self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
        people = [(i, who) for i, (_, who) in enumerate(s.parts) if who in self.names]
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
        at = waiting.index(s) + 1
        return card_text(s, self.names, at, len(waiting), name=self.name, on=self.on)

    async def _who_picked(self, interaction: discord.Interaction) -> None:
        self.on = frozenset(int(v) for v in self.who.values)
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
                if how == "keep":
                    await memory.undo(g, c, int(ref))
                    await store.unkeep(g, c, int(ref))
                else:
                    await store.unturn(g, c, str(ref))
                self.undo, said = None, undone_text(was)
            elif what == "keep" and s is not None:
                name = self.name or s.name
                kept = await memory.keep_thread(
                    g, c, name, s.kind, s.state, aim=s.aim,
                    parts=[p for i, p in enumerate(s.parts) if i in self.on],
                    source=DM, suggestion_id=s.id,
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
