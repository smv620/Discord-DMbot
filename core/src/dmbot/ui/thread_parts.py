"""A story thread's steps, rewards and more on its 🧵 card (#4471, quests slice 2b; design
`docs/design/quests.md` §5 to §6a and §10.1; the ux-critic's words on #4471). DM only, asked
again on every press, as every thread button is.

On the card: the thread's steps as a checklist, what is in the way, rewards promised and given,
help given or lent, time limits (⏰, Supervisor's ruling: ⏳ stays "gone quiet"), its branches and
the bigger thread it is a branch of. "📋 Steps, rewards and more" opens one screen to add, tick,
change, show or take off each one; "🌿 Branch of…" picks the bigger thread from a list.

The DM never sees "part" or a stored state word (§13 rule 10): each state has its own button
words here. Everything the DM types starts hidden from players (§10.2); 👁️ marks what they may
see. A change in the story keeps the old one as history, a fix doesn't (Supervisor's ruling); the
DM says which when changing words. Every change is one Undo."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

import discord

from dmbot.memory import thread_parts as tp
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
    _tell,
)
from dmbot.ui.names import PLAYERS_DONT_KNOW, PLAYERS_KNOW, _md, changed

log = logging.getLogger(__name__)

PARTS_BUTTON = "📋 Steps, rewards and more"
BRANCH_BUTTON = "🌿 Branch of…"
ADD_PLACEHOLDER = "➕ Add…"
PICK_PLACEHOLDER = "Pick one to change…"
CHANGE_WORDS = "✏️ Change words"
TAKE_OFF = "🗑️ Take off (it was wrong)"
BACK = "↩ Back"
UNDO = "↩ Undo"
SUB_STEP = "➕ Add a step under it"
TOWARD = "🔗 Toward a promise…"
IN_STORY = "📖 It changed in the story"
JUST_FIX = "✏️ Just fixing a mistake"
MOVE_IT, LEAVE_IT = "🌿 Move it", "Leave it"
NOT_A_BRANCH = "Not a branch of anything"
NOT_PROMISED = "No, it wasn't promised"

# The Add menu: (kind, label, description)
ADD_CHOICES = (
    (tp.OBJECTIVE, "A step", "Something the party has to do. You can add smaller steps under it."),
    (tp.COMPLICATION, "Something in the way", "An obstacle or a setback, like a road cut off."),
    (tp.REWARD, "A reward promised", "What someone will give for the job, such as 100 gold."),
    (tp.REWARD_GIVEN, "A reward given", "Something handed over as payment, promised or not."),
    (tp.HELP, "Help given or lent",
     "Something handed over to help with the job, kept or to give back."),
    (tp.TIME_LIMIT, "A time limit", "When it has to be done by, in the story's own words."),
)  # fmt: skip
KIND_WORDS = {
    tp.OBJECTIVE: "Step", tp.COMPLICATION: "In the way", tp.REWARD: "Reward",
    tp.REWARD_GIVEN: "Given", tp.HELP: "Help", tp.TIME_LIMIT: "Time limit",
}  # fmt: skip
# Each stored state's button words (the ux-critic's list; ⏰ for a time limit, Supervisor's ruling)
STATE_BUTTONS: Mapping[str, Mapping[str, str]] = {
    tp.OBJECTIVE: {
        "to_do": "☐ To do", "done": "☑ Done", "failed": "✖ Failed",
        "not_needed": "➖ Not needed now",
    },
    tp.COMPLICATION: {
        "in_the_way": "🚧 Still in the way", "overcome": "✔ Overcome",
        "no_longer_matters": "➖ No longer matters",
    },
    tp.REWARD: {
        "owed": "Still owed", "settled": "✔ All given", "wont_be_given": "Won't be given",
        "turned_down": "Party turned it down",
    },
    tp.REWARD_GIVEN: {"given": "Given"},
    tp.HELP: {
        "to_keep": "Theirs to keep", "lent": "Lent", "returned": "↩ Returned",
        "not_returned": "Not returned",
    },
    tp.TIME_LIMIT: {"running": "⏰ Still running", "met": "✔ Met in time", "lifted": "Lifted"},
}  # fmt: skip
# How a state reads in a card line's brackets ("(all given)") or a confirmation ("✅ Overcome")
STATE_SAID: Mapping[str, str] = {
    "to_do": "to do", "done": "done", "failed": "failed", "not_needed": "no longer needed",
    "in_the_way": "still in the way", "overcome": "overcome",
    "no_longer_matters": "no longer matters", "owed": "still owed", "settled": "all given",
    "wont_be_given": "won't be given", "turned_down": "the party turned it down",
    "given": "given", "to_keep": "theirs to keep", "lent": "lent", "returned": "returned",
    "not_returned": "not returned", "running": "still running", "met": "met",
    "lifted": "lifted",
}  # fmt: skip
DUE_CHOICES = (("Up front", tp.UP_FRONT), ("When the job's done", tp.AFTERWARDS), ("Not said", ""))
DUE_SAID = {tp.UP_FRONT: "up front", tp.AFTERWARDS: "when the job's done"}
HELP_CHOICES = (("Theirs to keep", "to_keep"), ("Lent: give it back", "lent"))
LIMIT_CHOICES = (("Hard: something's lost", tp.HARD), ("Soft: things get worse", tp.SOFT))
# what the card folds into "Also: …" instead of a line each, so it stays one phone screen
FOLDED = {
    tp.COMPLICATION: ("overcome", "no_longer_matters"),
    tp.HELP: ("returned", "not_returned"),
    tp.TIME_LIMIT: ("met", "lifted"),
}
SEEN = " 👁️"
SEEN_NOTE = "👁️ Players can see it (once you show them the thread)."
ONLY_DM = "Only the DM can change a story thread."
TOO_LATE = "⚠️ Can't undo: it was changed since. Change it in 🧵 Threads."
NO_WORDS = "⚠️ Type a few words, then try again."
# the store's refusals, in the ux-critic's words
SAID_FOR = {
    tp.SAME_PART: "⚠️ This thread already has that. Pick it from the list to change it.",
    tp.WRONG_UNDER: "⚠️ A smaller step can't have steps of its own. Add it under the main step.",
    tp.NO_WORDS: NO_WORDS,
    tp.GONE: "⚠️ That was changed since. Here's how it is now.",
}


def _said(exc: MemoryRuleError) -> str:
    return SAID_FOR.get(str(exc), f"⚠️ {exc}")


def _name(names: Mapping[str, str], entry: str | None) -> str:
    return _md(names[entry]) if entry is not None and entry in names else ""


def _reward_line(p: tp.Part, names: Mapping[str, str], given: Sequence[tp.Part]) -> str:
    who = _name(names, p.who_id)
    head = f"🎁 **Reward from {who}:**" if who else "🎁 **Reward:**"
    text = _md(p.words)
    if p.due:
        text += f", {DUE_SAID[p.due]}"
    against = [g for g in given if g.under_id == p.id]
    left = tp.owed(p, against)
    if p.state == "owed" and against:
        if left is not None and p.amount is not None:
            text += f" ({_amount(p.amount - left)} given, {_amount(left)} owed)"
        else:
            text += " (some given)"
    elif p.state != "owed":
        text += f" ({STATE_SAID[p.state]})"
    return f"{head} {text}"


def _amount(n: float) -> str:
    return str(int(n)) if n == int(n) else f"{n:g}"


def card_lines(
    parts: Sequence[tp.Part],
    names: Mapping[str, str],
    bigger: str | None,
    branches: Sequence[str],
) -> list[str]:
    """What a thread's card shows of its steps, rewards and more, and its branches (§10.1; the
    ux-critic's layout). Standing parts only; finished obstacles, help and time limits fold into one
    "Also:" line."""
    lines: list[str] = []
    by_kind = {k: [p for p in parts if p.kind == k] for k in tp.KINDS}
    seen = False

    def mark(p: tp.Part) -> str:
        nonlocal seen
        seen = seen or p.table_knows
        return SEEN if p.table_knows else ""

    steps = by_kind[tp.OBJECTIVE]
    if steps:
        done, total = tp.done_count(steps)
        lines.append(f"**Steps ({done} of {total} done):**")
        for top in (p for p in steps if p.under_id is None):
            lines.append(_step(top) + mark(top))
            for sub in (p for p in steps if p.under_id == top.id):
                lines.append("  ↳ " + _step(sub) + mark(sub))
    folded: dict[str, int] = {}
    for p in by_kind[tp.COMPLICATION]:
        if p.state in FOLDED[tp.COMPLICATION]:
            folded[p.state] = folded.get(p.state, 0) + 1
        else:
            lines.append(f"**In the way:** {_md(p.words)}" + mark(p))
    given = by_kind[tp.REWARD_GIVEN]
    for p in by_kind[tp.REWARD]:
        lines.append(_reward_line(p, names, given) + mark(p))
    for p in given:
        if p.under_id is None:  # (one counted against a promise is in its line already)
            who = _name(names, p.who_id)
            head = f"🎁 **Given by {who}:**" if who else "🎁 **Given:**"
            lines.append(f"{head} {_md(p.words)} (not promised)" + mark(p))
    for p in by_kind[tp.HELP]:
        if p.state in FOLDED[tp.HELP]:
            folded[p.state] = folded.get(p.state, 0) + 1
            continue
        who = _name(names, p.who_id)
        lent = p.state == "lent"
        head = ("🤲 **Lent by " if lent else "🤲 **Help from ") + (who or "someone") + ":**"
        tail = f"to return: {_md(p.terms)}" if lent and p.terms else STATE_SAID[p.state]
        lines.append(f"{head} {_md(p.words)} ({tail})" + mark(p))
    for p in by_kind[tp.TIME_LIMIT]:
        if p.state in FOLDED[tp.TIME_LIMIT]:
            folded[p.state] = folded.get(p.state, 0) + 1
            continue
        kind = f" ({'hard' if p.limit_kind == tp.HARD else 'soft'})" if p.limit_kind else ""
        lines.append(f"⏰ **Time limit:** {_md(p.words)}{kind}" + mark(p))
    if folded:
        said = ", ".join(f"{n} {STATE_SAID[s]}" for s, n in folded.items())
        lines.append(f"Also: {said}. See 📋.")
    if branches:
        lines.append("🌿 **Branches:** " + ", ".join(_name(names, b) for b in branches))
    if bigger is not None:
        lines.append(f"🌿 **Branch of:** {_name(names, bigger)}")
    if seen:
        lines.append(SEEN_NOTE)
    return lines


def _step(p: tp.Part) -> str:
    box = "☑" if p.state == "done" else "✖" if p.state == "failed" else "☐"
    text = f"~~{_md(p.words)}~~" if p.state == "not_needed" else _md(p.words)
    return f"{box} {text}"


def option_for(p: tp.Part, names: Mapping[str, str]) -> tuple[str, str]:
    """A part in the pick list: its words, then its kind and state ("Step · to do · hidden")."""
    who = names.get(p.who_id or "", "")
    kind = KIND_WORDS[p.kind] + (f" from {who}" if who and p.kind == tp.REWARD else "")
    shown = "players see it" if p.table_knows else "hidden"
    desc = f"{kind} · {STATE_SAID[p.state]} · {shown}"
    if p.under_id is not None and p.kind == tp.OBJECTIVE:
        desc = "Smaller step · " + desc.split(" · ", 1)[1]
    return (
        logic.shorten(p.words, logic.OPTION_LABEL_MAX),
        logic.shorten(desc, logic.DESCRIPTION_MAX),
    )


# ---- the screens ------------------------------------------------------------------------------


async def _dm(interaction: discord.Interaction, campaign_id: str) -> Any:
    from dmbot.ui.threads import _dm_and_memory  # (the one DM check every thread button uses)

    return await _dm_and_memory(interaction, campaign_id)


async def open_parts(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    *,
    note: str = "",
    undo: int | None = None,
) -> None:
    """The 📋 screen: the thread's steps, rewards and more, Add, and a list to pick one."""
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    parts, names, bigger, branches = await memory.thread_card(
        campaign.guild_id, campaign.id, thread_id
    )
    thread = await memory.entity(campaign.guild_id, campaign.id, thread_id)
    if thread is None:
        await _tell(interaction, SAID_FOR[tp.GONE])
        return
    lines = card_lines(parts, names, bigger, branches) or ["Nothing yet. Add a step to start."]
    head = f"📋 **{_md(thread.name)}**"
    text = "\n".join(([note, ""] if note else []) + [head, *lines])
    await _replace(
        interaction,
        logic.shorten(text, 2000),
        PartsScreen(campaign_id, thread_id, parts, names, undo=undo),
    )


class PartsScreen(_Menu):
    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        parts: Sequence[tp.Part],
        names: Mapping[str, str],
        *,
        undo: int | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id = campaign_id, thread_id
        self.parts = {p.id: p for p in parts}
        self.owed = [p for p in parts if p.kind == tp.REWARD and p.state == "owed"]
        self.names = dict(names)
        self.add = _Select(
            self._add,
            placeholder=ADD_PLACEHOLDER,
            options=[
                discord.SelectOption(label=label, value=kind, description=desc)
                for kind, label, desc in ADD_CHOICES
            ],
            row=0,
        )
        self.add_item(self.add)
        if parts:
            options = []
            for p in list(parts)[:25]:
                label, desc = option_for(p, names)
                options.append(discord.SelectOption(label=label, value=p.id, description=desc))
            self.pick = _Select(self._pick, placeholder=PICK_PLACEHOLDER, options=options, row=1)
            self.add_item(self.pick)
        if undo is not None:
            self.add_item(_Button(partial(self._undo, undo), label=UNDO, row=2))
        self.add_item(_Button(self._back, label=BACK, row=2))

    async def _add(self, interaction: discord.Interaction) -> None:
        kind = self.add.values[0]
        if kind == tp.REWARD:
            await _choose(interaction, "**When is it due?**", self, kind, "due", DUE_CHOICES)
        elif kind == tp.HELP:
            await _choose(
                interaction, "**Is it theirs to keep?**", self, kind, "state", HELP_CHOICES
            )
        elif kind == tp.TIME_LIMIT:
            await _choose(
                interaction, "**What happens if it runs out?**", self, kind, "limit_kind",
                LIMIT_CHOICES,
            )  # fmt: skip
        elif kind == tp.REWARD_GIVEN and self.owed:
            await _answer_first(interaction, in_place=True)
            self.stop()
            await _replace(
                interaction,
                "**Is it toward a promised reward?**",
                TowardMenu(self.campaign_id, self.thread_id, self.owed, self.names, part_id=None),
            )
        else:
            await interaction.response.send_modal(AddForm(self.campaign_id, self.thread_id, kind))

    async def _pick(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        part = self.parts.get(self.pick.values[0])
        self.stop()
        if part is None:
            await open_parts(interaction, self.campaign_id, self.thread_id)
            return
        await _replace(
            interaction,
            _part_text(part, self.names),
            PartMenu(self.campaign_id, self.thread_id, part, self.owed, self.names),
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
        except MemoryRuleError:
            await open_parts(interaction, self.campaign_id, self.thread_id, note=TOO_LATE)
            return
        changed(interaction, campaign)
        await open_parts(interaction, self.campaign_id, self.thread_id, note="↩ Undone.")

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import show_thread

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id)


def _part_text(p: tp.Part, names: Mapping[str, str]) -> str:
    _label, desc = option_for(p, names)
    terms = f"\nTerms: {_md(p.terms)}" if p.terms else ""
    return f"**{_md(p.words)}**\n{desc}{terms}"


async def _choose(
    interaction: discord.Interaction,
    question: str,
    screen: PartsScreen,
    kind: str,
    field: str,
    choices: Sequence[tuple[str, str]],
) -> None:
    """One question asked before the form (a modal must be the answer to a press)."""
    await _answer_first(interaction, in_place=True)
    screen.stop()
    await _replace(
        interaction, question, Choose(screen.campaign_id, screen.thread_id, kind, field, choices)
    )


class Choose(_Menu):
    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        kind: str,
        field: str,
        choices: Sequence[tuple[str, str]],
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.kind, self.field = (
            campaign_id,
            thread_id,
            kind,
            field,
        )
        for label, value in choices:
            self.add_item(_Button(partial(self._picked, value), label=label))
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _picked(self, value: str, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.send_modal(
            AddForm(self.campaign_id, self.thread_id, self.kind, **{self.field: value})
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


# The form for each kind: (title, field label, placeholder)
FORMS = {
    tp.OBJECTIVE: ("Add a step", "The step", "Find where Sephek sleeps"),
    tp.COMPLICATION: ("Something in the way", "What's in the way", "The road north is cut"),
    tp.REWARD: ("A reward promised", "The reward", "100 gold"),
    tp.REWARD_GIVEN: ("A reward given", "What was given", "50 gold"),
    tp.HELP: ("Help given or lent", "What was handed over", "Two sleds and eight dogs"),
    tp.TIME_LIMIT: ("A time limit", "The time limit, in their words", "Before the new moon"),
}
SUB_FORM = ("Add a step under it", "The smaller step", "Talk to the barkeep")
WHO_LABEL = {
    tp.REWARD: "Who promises it? (optional)",
    tp.REWARD_GIVEN: "Who gave it? (optional)",
    tp.HELP: "Who gave or lent it? (optional)",
}


class AddForm(discord.ui.Modal):
    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        kind: str,
        *,
        under_id: str | None = None,
        due: str = "",
        state: str | None = None,
        limit_kind: str = "",
    ) -> None:
        title, label, placeholder = SUB_FORM if under_id and kind == tp.OBJECTIVE else FORMS[kind]
        super().__init__(title=title, timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id, self.kind = campaign_id, thread_id, kind
        self.extra: dict[str, Any] = {
            "under_id": under_id, "due": due, "state": state, "limit_kind": limit_kind,
        }  # fmt: skip
        self.words: discord.ui.TextInput[AddForm] = discord.ui.TextInput(
            label=label, placeholder=placeholder, max_length=tp.WORDS_MAX
        )
        self.add_item(self.words)
        self.who: discord.ui.TextInput[AddForm] | None = None
        if kind in WHO_LABEL:
            self.who = discord.ui.TextInput(label=WHO_LABEL[kind], required=False, max_length=100)
            self.add_item(self.who)
        self.terms: discord.ui.TextInput[AddForm] | None = None
        if kind == tp.HELP:
            self.terms = discord.ui.TextInput(
                label="Terms, in their words (optional)",
                placeholder="Return them unharmed",
                required=False,
                max_length=tp.TERMS_MAX,
            )
            self.add_item(self.terms)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import link_by_name

        await _answer_first(interaction, in_place=True)
        save = partial(
            _save_added,
            kind=self.kind,
            words=self.words.value,
            terms=self.terms.value if self.terms is not None else "",
            extra=self.extra,
        )
        typed = " ".join((self.who.value if self.who is not None else "").split())
        if not typed:
            await save(interaction, self.campaign_id, self.thread_id, None)
            return
        await link_by_name(
            interaction, self.campaign_id, self.thread_id, typed, kinds=None, then=save
        )


async def _save_added(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    who_id: str | None,
    *,
    kind: str,
    words: str,
    terms: str,
    extra: Mapping[str, Any],
) -> None:
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    try:
        done = await memory.add_thread_part(
            campaign.guild_id, campaign.id, thread_id, kind, words, source=DM, who_id=who_id,
            terms=terms, **extra,
        )  # fmt: skip
    except MemoryRuleError as exc:
        await open_parts(interaction, campaign_id, thread_id, note=_said(exc))
        return
    changed(interaction, campaign)
    await open_parts(
        interaction,
        campaign_id,
        thread_id,
        note=f"✅ Added: **{_md(done.value.words)}**. 🙈 Hidden from players for now.",
        undo=done.batch,
    )


class PartMenu(_Menu):
    """One part: its state buttons, then change words, show or hide, take off, back."""

    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        part: tp.Part,
        owed: Sequence[tp.Part],
        names: Mapping[str, str],
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.part = campaign_id, thread_id, part
        self.owed, self.names = list(owed), dict(names)
        words = STATE_BUTTONS[part.kind]
        if len(words) > 1:
            for state, label in words.items():
                self.add_item(
                    _Button(
                        partial(self._state, state),
                        label=label,
                        row=0,
                        style=discord.ButtonStyle.primary
                        if state == part.state
                        else discord.ButtonStyle.secondary,
                    )
                )
        self.add_item(_Button(self._words, label=CHANGE_WORDS, row=1))
        shown = PLAYERS_KNOW if part.table_knows else PLAYERS_DONT_KNOW
        self.add_item(_Button(self._show, label=shown, row=1))
        self.add_item(_Button(self._take_off, label=TAKE_OFF, row=1))
        if part.kind == tp.OBJECTIVE and part.under_id is None:
            self.add_item(_Button(self._sub_step, label=SUB_STEP, row=2))
        if part.kind == tp.REWARD_GIVEN and owed:
            self.add_item(_Button(self._toward, label=TOWARD, row=2))
        self.add_item(_Button(self._back, label=BACK, row=2))

    async def _run(self, interaction: discord.Interaction, write: Any, note: Any) -> None:
        """Do one change, then back to the 📋 screen with its words and ↩ Undo."""
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        self.stop()
        try:
            done = await write(memory, campaign)
        except MemoryRuleError as exc:
            await open_parts(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        await open_parts(
            interaction, self.campaign_id, self.thread_id, note=note(done), undo=done.batch
        )

    async def _state(self, state: str, interaction: discord.Interaction) -> None:
        words = _md(self.part.words)
        await self._run(
            interaction,
            lambda memory, c: memory.set_thread_part_state(
                c.guild_id, c.id, self.part.id, state, source=DM
            ),
            lambda _d: f"✅ {STATE_SAID[state].capitalize()}: **{words}**.",
        )

    async def _show(self, interaction: discord.Interaction) -> None:
        shown = not self.part.table_knows
        words = _md(self.part.words)
        await self._run(
            interaction,
            lambda memory, c: memory.show_thread_part(
                c.guild_id, c.id, self.part.id, shown, source=DM
            ),
            lambda _d: (
                f"✅ Players can now see: **{words}**."
                if shown
                else f"✅ Hidden from players: **{words}**."
            ),
        )

    async def _take_off(self, interaction: discord.Interaction) -> None:
        words = _md(self.part.words)
        await self._run(
            interaction,
            lambda memory, c: memory.remove_thread_part(c.guild_id, c.id, self.part.id, source=DM),
            lambda _d: f"✅ Taken off: **{words}**.",
        )

    async def _words(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.send_modal(
            WordsForm(self.campaign_id, self.thread_id, self.part)
        )

    async def _sub_step(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.send_modal(
            AddForm(self.campaign_id, self.thread_id, tp.OBJECTIVE, under_id=self.part.id)
        )

    async def _toward(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _replace(
            interaction,
            "**Is it toward a promised reward?**",
            TowardMenu(self.campaign_id, self.thread_id, self.owed, self.names, self.part.id),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


class TowardMenu(_Menu):
    """Which owed promise a reward given counts against: before the form for a new one
    (`part_id` None), or for one already written."""

    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        owed: Sequence[tp.Part],
        names: Mapping[str, str],
        part_id: str | None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.part_id = campaign_id, thread_id, part_id
        options = [discord.SelectOption(label=NOT_PROMISED, value="")]
        for p in list(owed)[:24]:
            who = names.get(p.who_id or "", "")
            label = p.words + (f" from {who}" if who else "")
            options.append(
                discord.SelectOption(label=logic.shorten(label, logic.OPTION_LABEL_MAX), value=p.id)
            )
        self.pick = _Select(
            self._picked, placeholder="Is it toward a promised reward?", options=options
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        under = self.pick.values[0] or None
        self.stop()
        if self.part_id is None:
            await interaction.response.send_modal(
                AddForm(self.campaign_id, self.thread_id, tp.REWARD_GIVEN, under_id=under)
            )
            return
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        part_id = self.part_id
        try:
            done = await memory.correct_thread_part(
                campaign.guild_id, campaign.id, part_id, under_id=under, source=DM
            )
        except MemoryRuleError as exc:
            await open_parts(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        note = "✅ Counted toward a promise." if under else "✅ Not counted toward a promise."
        await open_parts(interaction, self.campaign_id, self.thread_id, note=note, undo=done.batch)

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


class WordsForm(discord.ui.Modal, title="Change the words"):
    def __init__(self, campaign_id: str, thread_id: str, part: tp.Part) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id, self.part = campaign_id, thread_id, part
        self.words: discord.ui.TextInput[WordsForm] = discord.ui.TextInput(
            label="Words", default=part.words, max_length=tp.WORDS_MAX
        )
        self.add_item(self.words)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        new = " ".join(self.words.value.split())
        if not new:
            await open_parts(interaction, self.campaign_id, self.thread_id, note=NO_WORDS)
            return
        await _replace(
            interaction,
            f"**{_md(self.part.words)}** → **{_md(new)}**\nDid it change in the story, or are you "
            "fixing a mistake?",
            HowChanged(self.campaign_id, self.thread_id, self.part, new),
        )


class HowChanged(_Menu):
    """A change in the story keeps the old words as history; a fix doesn't (Supervisor's ruling,
    the ux-critic's question)."""

    def __init__(self, campaign_id: str, thread_id: str, part: tp.Part, new: str) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.part, self.new = campaign_id, thread_id, part, new
        self.add_item(_Button(partial(self._save, True), label=IN_STORY))
        self.add_item(_Button(partial(self._save, False), label=JUST_FIX))
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _save(self, in_story: bool, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        self.stop()
        try:
            done = await memory.correct_thread_part(
                campaign.guild_id, campaign.id, self.part.id, words=self.new, in_story=in_story,
                source=DM,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await open_parts(interaction, self.campaign_id, self.thread_id, note=_said(exc))
            return
        changed(interaction, campaign)
        new, old = _md(done.value.words), _md(self.part.words)
        history = done.value.id != self.part.id
        note = (
            f"✅ Changed in the story: **{new}** (was: {old})."
            if history
            else f"✅ Fixed: **{new}**."
        )
        await open_parts(interaction, self.campaign_id, self.thread_id, note=note, undo=done.batch)

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


# ---- 🌿 Branch of… ------------------------------------------------------------------------------


async def open_branch(interaction: discord.Interaction, campaign_id: str, thread_id: str) -> None:
    """Pick the bigger thread this one is a branch of, from the campaign's threads."""
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    threads = [
        t.thread
        for t in await memory.threads(campaign.guild_id, campaign.id)
        if t.thread.id != thread_id
    ]
    bigger = await memory.bigger_thread(campaign.guild_id, campaign.id, thread_id)
    if not threads and bigger is None:
        await _tell(interaction, "There are no other threads yet. Add the bigger one first.")
        return
    await _replace(
        interaction,
        "**Which bigger thread is it a branch of?**",
        BranchMenu(campaign_id, thread_id, [(t.id, t.name, t.type) for t in threads], bigger),
    )


class BranchMenu(_Menu):
    def __init__(
        self,
        campaign_id: str,
        thread_id: str,
        threads: Sequence[tuple[str, str, str]],
        bigger: str | None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.bigger = campaign_id, thread_id, bigger
        self.names = {i: n for i, n, _ in threads}
        options = []
        if bigger is not None:
            options.append(discord.SelectOption(label=NOT_A_BRANCH, value=""))
        for i, name, kind in sorted(threads, key=lambda t: t[1].lower())[: 25 - len(options)]:
            options.append(
                discord.SelectOption(
                    label=logic.shorten(name, logic.OPTION_LABEL_MAX), value=i,
                    description=kind.capitalize(), default=i == bigger,
                )
            )  # fmt: skip
        self.pick = _Select(
            self._picked, placeholder="Which bigger thread is it a branch of?", options=options
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        chosen = self.pick.values[0]
        self.stop()
        if chosen and self.bigger is not None and chosen != self.bigger:
            await _replace(
                interaction,
                f"It is already a branch of **{_md(self.names.get(self.bigger, ''))}**. Move it to "
                f"**{_md(self.names.get(chosen, ''))}**?",
                MoveBranch(self.campaign_id, self.thread_id, chosen, self.names.get(chosen, "")),
            )
            return
        await _save_branch(
            interaction,
            self.campaign_id,
            self.thread_id,
            chosen,
            self.names.get(chosen, ""),
            move=False,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import show_thread

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id)


class MoveBranch(_Menu):
    def __init__(self, campaign_id: str, thread_id: str, bigger: str, name: str) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.bigger, self.name = (
            campaign_id, thread_id, bigger, name,
        )  # fmt: skip
        self.add_item(_Button(self._move, label=MOVE_IT, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._leave, label=LEAVE_IT))

    async def _move(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _save_branch(
            interaction, self.campaign_id, self.thread_id, self.bigger, self.name, move=True
        )

    async def _leave(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import show_thread

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id)


async def _save_branch(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    bigger: str,
    name: str,
    *,
    move: bool,
) -> None:
    from dmbot.ui.threads import show_thread

    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    try:
        if bigger:
            await memory.branch_of(
                campaign.guild_id, campaign.id, thread_id, bigger, source=DM, move=move
            )
            note = f"✅ It is now a branch of **{_md(name)}**."
        else:
            await memory.not_a_branch(campaign.guild_id, campaign.id, thread_id, source=DM)
            note = "✅ It is no longer a branch."
    except MemoryRuleError as exc:
        text = str(exc)
        if "loop" in text:
            text = (
                f"⚠️ **{_md(name)}** is already a branch of this thread, so this one can't be "
                "a branch of it."
            )
        else:
            text = f"⚠️ {text}"
        await show_thread(interaction, campaign_id, thread_id, note=text)
        return
    changed(interaction, campaign)
    await show_thread(interaction, campaign_id, thread_id, note=note)
