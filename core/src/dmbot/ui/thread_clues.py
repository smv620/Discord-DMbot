"""🔎 Clues on a story thread's 📋 screen (#4624, quests slice 6a; design `docs/design/quests.md`
§7 and §10; Supervisor's ruling on #4624). DM only, asked again on every press, as every thread
button is.

The screen lists the clues tied to the thread, each with whether the table has found it (one of
the player characters knows it) and the DM's "false lead" mark, under the count ("2 of 3 clues
found. Not yet found: 1.", worked out when shown, never stored: `memory.thread_clues`). The DM
adds a clue by its name (the campaign's clue of that name, or a new one, hidden from the players
until a character finds it), marks one a false lead or says it points to it, or takes it off.
Every change is one ↩ Undo. Nothing here is shown to a player: which clues a thread has, and their
marks, are the DM's alone."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

import discord

from dmbot.memory import gate
from dmbot.memory import thread_clues as tc
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
)
from dmbot.ui.names import _md, changed
from dmbot.ui.thread_parts import BACK, UNDO, _dm, _Form, _open_form
from dmbot.ui.thread_parts import _said as _part_said

CLUES_BUTTON = "🔎 Clues"
ADD_CLUE = "➕ Add a clue"
PICK_CLUE = "Pick a clue to change…"
FALSE_LEAD_BUTTON = "Mark as false lead"
POINTS_BUTTON = "Not a false lead"  # (the stored word stays "points to it")
UNTIE = "Take off this thread"
NONE_YET = "No clues on this thread yet. Tap ➕ Add a clue."
FOUND, NOT_FOUND = "found", "not found yet"
HOW = "Found: a player character knows it. Only you see this list."
CLUES_SHOWN = 25  # a menu holds 25
# (the store's clue refusals are already in the DM's words; the rest as the 📋 screen says them)
_OWN_WORDS = frozenset({tc.ALREADY_TIED, tc.NOT_TIED, tc.ONLY_CLUES, tc.DM_ONLY_TIE})


def _said(exc: MemoryRuleError) -> str:
    return f"⚠️ {exc}" if str(exc) in _OWN_WORDS else _part_said(exc)


def clue_line(c: tc.ThreadClue) -> str:
    """One clue on the DM's screen: its name, found or not, and the DM's mark."""
    said = FOUND if c.found else NOT_FOUND
    lead = f" · {tc.FALSE_LEAD}" if c.false_lead else ""
    return f"• {_md(c.name)} ({said}{lead})"


def screen_text(thread_name: str, clues: Sequence[tc.ThreadClue], note: str = "") -> str:
    head = f"🔎 **Clues to {_md(thread_name)}**"
    body = (
        [tc.found_text(clues), *(clue_line(c) for c in clues[:CLUES_SHOWN])]
        if clues
        else [NONE_YET]
    )
    more = [f"…and {len(clues) - CLUES_SHOWN} more."] if len(clues) > CLUES_SHOWN else []
    return logic.shorten(
        "\n".join([*([note, ""] if note else []), head, *body, *more, "", HOW]), 2000
    )


async def open_clues(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    *,
    note: str = "",
    undo: int | None = None,
) -> None:
    """The 🔎 screen: the thread's clues and the count, 🔎 Add a clue, and a list to pick one."""
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    thread = await memory.entity(campaign.guild_id, campaign.id, thread_id)
    if thread is None:
        await _replace(interaction, "That story thread is gone.", None)
        return
    dm = gate.Viewer.the_dm()
    clues = await memory.thread_clues(campaign.guild_id, campaign.id, thread_id, dm)
    await _replace(
        interaction,
        screen_text(thread.name, clues, note),
        ClueScreen(campaign_id, thread_id, clues, undo=undo),
    )


class ClueScreen(_Menu):
    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        clues: Sequence[tc.ThreadClue],
        *,
        undo: int | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id = campaign_id, thread_id
        self.clues = {c.clue_id: c for c in clues}
        if clues:
            self.pick = _Select(
                self._pick,
                placeholder=PICK_CLUE,
                options=[
                    discord.SelectOption(
                        label=logic.shorten(c.name, logic.OPTION_LABEL_MAX),
                        value=c.clue_id,
                        description=(FOUND if c.found else NOT_FOUND)
                        + (f" · {tc.FALSE_LEAD}" if c.false_lead else ""),
                    )
                    for c in list(clues)[:CLUES_SHOWN]
                ],
                row=0,
            )
            self.add_item(self.pick)
        self.add_item(_Button(self._add, label=ADD_CLUE, row=1))
        if undo is not None:
            self.add_item(_Button(partial(self._undo, undo), label=UNDO, row=1))
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _add(self, interaction: discord.Interaction) -> None:
        await _open_form(interaction, self, ClueForm(self.campaign_id, self.thread_id))

    async def _pick(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        clue = self.clues.get(self.pick.values[0])
        if clue is None:
            await open_clues(interaction, self.campaign_id, self.thread_id)
            return
        said = FOUND if clue.found else NOT_FOUND
        lead = f" · {tc.FALSE_LEAD}" if clue.false_lead else ""
        await _replace(
            interaction,
            f"🔎 **{_md(clue.name)}**: {said}{lead}\nPick what to change.",
            ClueMenu(self.campaign_id, self.thread_id, clue),
        )

    async def _undo(self, batch: int, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        self.stop()
        try:
            await memory.undo(campaign.guild_id, campaign.id, batch, source=DM)
        except MemoryRuleError as exc:
            await open_clues(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        await open_clues(interaction, self.campaign_id, self.thread_id, note="↩ Undone.")

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.thread_parts import open_parts

        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


class ClueMenu(_Menu):
    """One tied clue: mark it a false lead (or say it points to it), take it off, back."""

    def __init__(self, campaign_id: str, thread_id: str, clue: tc.ThreadClue) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.clue = campaign_id, thread_id, clue
        mark = POINTS_BUTTON if clue.false_lead else FALSE_LEAD_BUTTON
        self.add_item(_Button(self._mark, label=mark))
        self.add_item(_Button(self._untie, label=UNTIE, style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._back, label=BACK))

    async def _mark(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        self.stop()
        lead = not self.clue.false_lead
        try:
            done = await memory.set_clue_word(
                campaign.guild_id, campaign.id, self.thread_id, self.clue.clue_id,
                false_lead=lead, source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await open_clues(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        name = _md(self.clue.name)
        said = (
            f"✅ **{name}** is now marked a false lead."
            if lead
            else (f"✅ **{name}** is no longer a false lead.")
        )
        await open_clues(interaction, self.campaign_id, self.thread_id, note=said, undo=done.batch)

    async def _untie(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        self.stop()
        try:
            done = await memory.untie_clue(
                campaign.guild_id, campaign.id, self.thread_id, self.clue.clue_id, source=DM
            )
        except MemoryRuleError as exc:
            await open_clues(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        await open_clues(
            interaction, self.campaign_id, self.thread_id,
            note=f"🗑️ Took **{_md(self.clue.name)}** off this thread. It's still in your clues.",
            undo=done.batch,
        )  # fmt: skip

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_clues(interaction, self.campaign_id, self.thread_id)


class ClueForm(_Form, title="Add a clue"):
    def __init__(self, campaign_id: str, thread_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id = campaign_id, thread_id
        self.name: discord.ui.TextInput[ClueForm] = discord.ui.TextInput(
            label="Clue name", placeholder="A bloody glove", max_length=100
        )
        self.add_item(self.name)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def _submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            done = await memory.tie_clue(
                campaign.guild_id, campaign.id, self.thread_id, new_name=self.name.value,
                source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await open_clues(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        name = _md(self.name.value.strip())
        _tie, made = done.value
        said = (
            f"✅ Added **{name}** as a new clue. Players won't see it until a character finds it."
            if made
            else f"✅ **{name}** is now a clue to this thread."
        )
        await open_clues(interaction, self.campaign_id, self.thread_id, note=said, undo=done.batch)


__all__ = [
    "CLUES_BUTTON",
    "ClueForm",
    "ClueMenu",
    "ClueScreen",
    "clue_line",
    "open_clues",
    "screen_text",
]
