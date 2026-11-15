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
from dmbot.memory import time_limits
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
from dmbot.ui.names import PLAYERS_DONT_KNOW, PLAYERS_KNOW, _md, changed

log = logging.getLogger(__name__)

PARTS_BUTTON = "📋 Steps, rewards and more"
BRANCH_BUTTON = "🌿 Branch of…"
ADD_PLACEHOLDER = "➕ Add…"
PICK_PLACEHOLDER = "Pick one to change…"
CHANGE_WORDS = "✏️ Change words"
TAKE_OFF = "🗑️ Take off (it was wrong)"
BACK = "◀ Back"  # (not ↩: it sits beside ↩ Undo, an easy mis-tap mid-game)
UNDO = "↩ Undo"
SUB_STEP = "➕ Add a step under it"
TOWARD = "🔗 Toward a promise…"
IN_STORY = "📖 It changed in the story"
JUST_FIX = "✏️ Just fixing a mistake"
MOVE_IT, LEAVE_IT = "🌿 Move it", "Leave it"
NOT_A_BRANCH = "Not a branch of anything"
NOT_PROMISED = "No, it wasn't promised"
NONE = "none"  # a menu choice's value for "none of them" (Discord refuses an empty value)
# A time limit's game date (quests slice 4, §8.2: the DM's one tap)
# (📅, not ⏰: the part's own "⏰ Still running" sits on the same screen; the ux-critic)
SET_DATE_BUTTON = "📅 Set a date"  # (…_BUTTON: the website's check reads the bot's labels by it)
CHANGE_DATE_BUTTON = "📅 Change the date"
SET_DATE, CHANGE_DATE = SET_DATE_BUTTON, CHANGE_DATE_BUTTON
CLEAR_DATE = "🗑️ Clear the date"
PICK_DAY = "Other number of days…"
QUICK_SAID = {1: "Tomorrow", 3: "In 3 days", 7: "In a week"}
# (slice 4c: DMbot's read of a date in the words, offered, never counted until the DM taps)
COUNT_DOWN = "📅 Start the countdown"
GONE_BY = "⚠️ Nothing was changed: the game clock has moved on. Tap 📅 Set a date instead."
READS_AS = f"📅 DMbot reads this as **{{date}}**. Nothing counts down until you tap {COUNT_DOWN}."
NO_CLOCK_YET = (
    "⚠️ No game clock yet, so nothing was dated. Set one up: ⚙️ Manage DMbot, then Game clock."
)


def date_question(words: str, today: str | None) -> str:
    """The date screen's question, naming the limit and today's date, so the quick choices need
    no sum (the ux-critic on #4448)."""
    now = f" (it's {today})" if today else ""
    return f"**When is it due: {_md(words)}?** In game days from now{now}."


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
    tp.REWARD_GIVEN: "Reward given", tp.HELP: "Help", tp.TIME_LIMIT: "Time limit",
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
    "to_do": "to do", "done": "done", "failed": "failed", "not_needed": "not needed now",
    "in_the_way": "still in the way", "overcome": "overcome",
    "no_longer_matters": "no longer matters", "owed": "still owed", "settled": "all given",
    "wont_be_given": "won't be given", "turned_down": "the party turned it down",
    "given": "given", "to_keep": "theirs to keep", "lent": "lent", "returned": "returned",
    "not_returned": "not returned", "running": "still running", "met": "met in time",
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
# what the "Also:" line calls each folded state, so it says what was overcome or returned
FOLDED_SAID = {
    "overcome": "overcome", "no_longer_matters": "no longer in the way",
    "returned": "lent item(s) returned", "not_returned": "never given back",
    "met": "time limit(s) met", "lifted": "time limit(s) lifted",
}  # fmt: skip
SEEN = " 👁️"
# A dated limit with no game clock to count on (§8.2: "No game clock here, so I can't count this
# down.")
NO_CLOCK = "no game clock here, so I can't count this down"
SEEN_NOTE = "👁️ Players can see it (once you show them the thread)."
ONLY_DM = "Only the DM can change a story thread."
TOO_LATE = "⚠️ Can't undo: it was changed since. Change it here instead."
NO_WORDS = "⚠️ Type a few words, then try again."
# the store's refusals, in the ux-critic's words
SAID_FOR = {
    GONE_BY: GONE_BY,  # (4c: a read date the clock passed while the menu was open)
    tp.SAME_PART: "⚠️ This thread already has that. Pick it from the list to change it.",
    tp.WRONG_UNDER: "⚠️ A smaller step can only go under a main step of this thread.",
    tp.NO_WORDS: NO_WORDS,
    tp.GONE: "⚠️ That was changed since. Here's how it is now.",
    tp.NOT_A_THREAD: "⚠️ Keep this as a story thread first, then add steps to it.",
    tp.WRONG_PROMISE: "⚠️ That can only count toward a reward promised in this thread.",
    tp.NOT_WHO: "⚠️ Pick a character or a group, then try again.",
    tp.NOT_A_STATE: "⚠️ That choice isn't there any more. Here's how it is now.",
}
DIDNT_SAVE = "⚠️ That didn't save. Try again."


def _said(exc: MemoryRuleError) -> str:
    """The store's refusal in the card's words; never its own wording (it can say "part")."""
    return SAID_FOR.get(str(exc), DIDNT_SAVE)


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
    *,
    most: int | None = None,
    clock: tuple[int, Any] | None = None,
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
        if lent:
            tail = f"to return: {_md(p.terms)}" if p.terms else "to give back"
        else:
            tail = STATE_SAID[p.state]
        lines.append(f"{head} {_md(p.words)} ({tail})" + mark(p))
    for p in by_kind[tp.TIME_LIMIT]:
        if p.state in FOLDED[tp.TIME_LIMIT]:
            folded[p.state] = folded.get(p.state, 0) + 1
            continue
        kind = f" ({'hard' if p.limit_kind == tp.HARD else 'soft'})" if p.limit_kind else ""
        # (slice 4: a dated limit counts down on the clock, `clock` = (now, the calendar); with no
        # game clock it can't be counted, and the card says so once)
        left = ""
        offered = (
            time_limits.offer_for(p.words, clock)
            if p.due_minute is None and clock is not None and p.state == "running"
            else None
        )
        if offered is not None and clock is not None:  # (4c: the read, offered on 📋)
            said = time_limits.date_said(offered, clock[1])
            left = f" · DMbot reads this as {said}, not counting yet"
        if p.due_minute is not None:
            left = (
                f" · {time_limits.left_said(p.due_minute, clock[0], clock[1])}"
                if clock is not None
                else f" · {NO_CLOCK}"
            )
        lines.append(f"⏰ **Time limit:** {_md(p.words)}{kind}{left}" + mark(p))
    if folded:
        said = ", ".join(f"{n} {FOLDED_SAID[s]}" for s, n in folded.items())
        lines.append(f"Also: {said}. Tap 📋 to see them.")
    if branches:
        lines.append("🌿 **Branches:** " + ", ".join(_name(names, b) for b in branches))
    if bigger is not None:
        lines.append(f"🌿 **Branch of:** {_name(names, bigger)}")
    if seen:
        lines.append(SEEN_NOTE)
    if most is not None and len(lines) > most:  # (the name card has room for so much)
        lines = [*lines[: most - 1], f"…and {len(lines) - most + 1} more. Tap 📋 to see them."]
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
    state = f" · {STATE_SAID[p.state]}" if len(tp.STATES[p.kind]) > 1 else ""
    desc = f"{kind}{state} · {shown}"
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


async def clock_now(client: Any, guild_id: int, campaign_id: str) -> tuple[int, Any] | None:
    """The clock a time limit is counted on, from the bot's clock store (`time_limits`)."""
    return await time_limits.clock_now(getattr(client, "clocks", None), guild_id, campaign_id)


def dated(parts: Sequence[tp.Part]) -> bool:
    """Whether a card needs the clock: a time limit is counted on it, or its words read on the
    calendar (slice 4c)."""
    return any(p.kind == tp.TIME_LIMIT for p in parts)


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
        await _replace(interaction, SAID_FOR[tp.GONE], None)
        return
    clock = (
        await clock_now(interaction.client, campaign.guild_id, campaign.id)
        if dated(parts)
        else None
    )
    lines = card_lines(parts, names, bigger, branches, clock=clock) or [
        "Nothing yet. Pick ➕ Add… to start."
    ]
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
        self.add_item(_Button(self._branch, label=BRANCH_BUTTON, row=2))
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
        got = await _dm(interaction, self.campaign_id)  # (the DM check, and the server it is on)
        if got is None:
            return
        offer = await read_offer(interaction.client, got[0].guild_id, self.campaign_id, part)
        text = _part_text(part, self.names)
        if offer is not None:
            text += f"\n{READS_AS.format(date=offer[1])}"
        await _replace(
            interaction,
            text,
            PartMenu(self.campaign_id, self.thread_id, part, self.owed, self.names, offer),
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

    async def _branch(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_branch(interaction, self.campaign_id, self.thread_id)

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
        # (not stopped: a form closed unsent leaves this screen working; sending redraws it)
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
        offer: tuple[int, str, int, str] | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.part = campaign_id, thread_id, part
        self.offer = (
            offer  # (slice 4c: a date read from the words, its words, the clock's now, time left)
        )
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
        if part.kind == tp.TIME_LIMIT and part.state == "running":
            if offer is not None:
                self.add_item(
                    _Button(
                        self._count_down,
                        label=COUNT_DOWN,
                        style=discord.ButtonStyle.primary,
                        row=2,
                    )
                )
            dated = part.due_minute is not None
            self.add_item(_Button(self._date, label=CHANGE_DATE if dated else SET_DATE, row=2))
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
        if state == self.part.state:  # (the one it is already: nothing to write or undo)
            await _answer_first(interaction, in_place=True)
            self.stop()
            await open_parts(interaction, self.campaign_id, self.thread_id)
            return
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
        await interaction.response.send_modal(
            WordsForm(self.campaign_id, self.thread_id, self.part)
        )

    async def _sub_step(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            AddForm(self.campaign_id, self.thread_id, tp.OBJECTIVE, under_id=self.part.id)
        )

    async def _count_down(self, interaction: discord.Interaction) -> None:
        """The DM keeps DMbot's read of the date (§8.2, Supervisor's choice B): `due_from` read.
        Read again on the clock as it is at the tap, so a menu left open while the game moved on
        never writes a date already past, nor counts "soon" from a stale moment."""
        assert self.offer is not None
        offered = self.offer[0]
        words = _md(self.part.words)
        kept: list[str] = []

        async def write(memory: Any, c: Any) -> Any:
            fresh = await read_offer(interaction.client, c.guild_id, c.id, self.part)
            if fresh is None or fresh[0] != offered:
                raise MemoryRuleError(GONE_BY)
            due, _said, now, left = fresh
            kept.append(left)
            return await memory.set_time_limit_date(
                c.guild_id, c.id, self.part.id, due, now=now, source=DM, given_by=tp.READ
            )

        await self._run(interaction, write, lambda _d: f"✅ Date set: **{words}**, {kept[0]}.")

    async def _date(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_date(interaction, self.campaign_id, self.thread_id, self.part)

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


async def open_date(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    part: tp.Part,
    note: str = "",
) -> None:
    """The date screen, with today's date when the clock can be read."""
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, _memory = got
    clock = await clock_now(interaction.client, campaign.guild_id, campaign.id)
    today = time_limits.date_said(clock[0], clock[1]) if clock is not None else None
    question = date_question(part.words, today)
    await _replace(
        interaction,
        f"{note}\n\n{question}" if note else question,
        DateMenu(campaign_id, thread_id, part),
    )


class DateMenu(_Menu):
    """A time limit's date (slice 4, §8.2): Tomorrow, In 3 days, In a week, or a number of days
    from now; Clear the date makes it words only again. Counted from the clock's now (the group
    furthest ahead while the party is split). One change, one Undo."""

    def __init__(self, campaign_id: str, thread_id: str, part: tp.Part) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.part = campaign_id, thread_id, part
        for days in time_limits.QUICK_DAYS:
            self.add_item(_Button(partial(self._days, days), label=QUICK_SAID[days], row=0))
        self.add_item(_Button(self._pick, label=PICK_DAY, row=0))
        if part.due_minute is not None:
            self.add_item(_Button(self._clear, label=CLEAR_DATE, row=1))
        self.add_item(_Button(self._back, label=BACK, row=1))

    async def _days(self, days: int, interaction: discord.Interaction) -> None:
        self.stop()  # (before the write, as `PartMenu._run`: a quick second tap writes nothing)
        await set_date(interaction, self.campaign_id, self.thread_id, self.part, days)

    async def _pick(self, interaction: discord.Interaction) -> None:
        # (not stopped: a form closed unsent leaves this screen working)
        await interaction.response.send_modal(DaysForm(self.campaign_id, self.thread_id, self.part))

    async def _clear(self, interaction: discord.Interaction) -> None:
        self.stop()
        await set_date(interaction, self.campaign_id, self.thread_id, self.part, None)

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_parts(interaction, self.campaign_id, self.thread_id)


class DaysForm(discord.ui.Modal, title="When is it due?"):
    def __init__(self, campaign_id: str, thread_id: str, part: tp.Part) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id, self.part = campaign_id, thread_id, part
        self.days: discord.ui.TextInput[DaysForm] = discord.ui.TextInput(
            label="How many game days from now?", placeholder="3", max_length=4
        )
        self.add_item(self.days)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.days.value.strip()
        days = int(text) if text.isascii() and text.isdigit() else 0
        if not 1 <= days <= time_limits.MOST_DAYS:
            await _answer_first(interaction, in_place=True)
            await open_date(interaction, self.campaign_id, self.thread_id, self.part, DAYS_NUMBER)
            return
        await set_date(interaction, self.campaign_id, self.thread_id, self.part, days)


async def read_offer(
    client: Any, guild_id: int, campaign_id: str, part: tp.Part
) -> tuple[int, str, int, str] | None:
    """A running time limit with no date whose words name one on the campaign's calendar (slice
    4c, §8.2): (its due minute, the date in words, the clock's now, the time left as the cards say
    it), offered to the DM for one tap,
    never counted until the DM taps (Supervisor's choice B). None with no clock or no date."""
    if part.kind != tp.TIME_LIMIT or part.state != "running" or part.due_minute is not None:
        return None
    clock = await clock_now(client, guild_id, campaign_id)
    if clock is None:
        return None
    due = time_limits.offer_for(part.words, clock)
    if due is None:
        return None
    now, calendar = clock
    try:
        return (
            due,
            time_limits.date_said(due, calendar),
            now,
            time_limits.left_said(due, now, calendar),
        )
    except Exception:  # (an offer never breaks the menu: the DM can still tap 📅 Set a date)
        log.warning("Couldn't word a date read from a time limit", exc_info=True)
        return None


DAYS_NUMBER = f"⚠️ Nothing was changed. Type a number of game days, 1 to {time_limits.MOST_DAYS}."


async def set_date(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    part: tp.Part,
    days: int | None,
) -> None:
    """Write a time limit's date `days` game days from the clock's now, or clear it (None), then
    back to the 📋 screen with its words and ↩ Undo. No game clock: nothing is written."""
    await _answer_first(interaction, in_place=True)
    got = await _dm(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    clock = await clock_now(interaction.client, campaign.guild_id, campaign.id)
    if days is not None and clock is None:
        await open_parts(interaction, campaign_id, thread_id, note=NO_CLOCK_YET)
        return
    now = clock[0] if clock is not None else None
    due = time_limits.days_from(now, days) if days is not None and now is not None else None
    try:
        done = await memory.set_time_limit_date(
            campaign.guild_id, campaign.id, part.id, due, now=now, source=DM
        )
    except MemoryRuleError as exc:
        await open_parts(interaction, campaign_id, thread_id, note=_said(exc))
        return
    changed(interaction, campaign)
    words = _md(part.words)
    if due is None or clock is None:
        note = f"✅ Date cleared: **{words}**. No countdown now."
    else:
        note = f"✅ Date set: **{words}**, {time_limits.left_said(due, clock[0], clock[1])}."
    await open_parts(interaction, campaign_id, thread_id, note=note, undo=done.batch)


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
        options = [discord.SelectOption(label=NOT_PROMISED, value=NONE)]
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
        under = None if self.pick.values[0] == NONE else self.pick.values[0]
        if self.part_id is None:  # (not stopped first: a form closed unsent leaves this working)
            await interaction.response.send_modal(
                AddForm(self.campaign_id, self.thread_id, tp.REWARD_GIVEN, under_id=under)
            )
            return
        await _answer_first(interaction, in_place=True)
        self.stop()
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
        if new == self.part.words:
            await open_parts(interaction, self.campaign_id, self.thread_id)
            return
        if self.part.kind in (tp.REWARD, tp.REWARD_GIVEN) and tp.read_amount(new) != (
            self.part.amount,
            self.part.unit,
        ):
            # a new amount is always a change in the story (Supervisor's ruling), so no question
            await HowChanged(self.campaign_id, self.thread_id, self.part, new)._save(
                True, interaction
            )
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
        await open_parts(
            interaction, campaign_id, thread_id,
            note="There are no other threads yet. Add the bigger one first.",
        )  # fmt: skip
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
            options.append(discord.SelectOption(label=NOT_A_BRANCH, value=NONE))
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
        chosen = "" if self.pick.values[0] == NONE else self.pick.values[0]
        self.stop()
        if chosen == self.bigger or (not chosen and self.bigger is None):
            from dmbot.ui.threads import show_thread

            await show_thread(interaction, self.campaign_id, self.thread_id)  # (nothing new)
            return
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
        from dmbot.memory.store import BRANCH_LOOP

        text = str(exc)
        if text == BRANCH_LOOP:
            text = (
                f"⚠️ **{_md(name)}** is already a branch of this thread, so this one can't be "
                "a branch of it."
            )
        else:
            text = SAID_FOR.get(text, DIDNT_SAVE)
        await show_thread(interaction, campaign_id, thread_id, note=text)
        return
    changed(interaction, campaign)
    await show_thread(interaction, campaign_id, thread_id, note=note)
