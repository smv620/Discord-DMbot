"""Story threads the DM keeps (#1662; docs/STORY_MEMORY.md, sections 2 and 3): New thread on the
names home; on a thread's name card its state, who is involved and in what part, the events that
moved it, with Change state, Add who's involved and Link an event; and after a session one line
saying how many threads haven't moved, with a list to answer each. DM only, asked again on every
press. DMbot never decides where a thread is: the DM does.
"""

from __future__ import annotations

import logging
import re
from functools import partial
from typing import Any

import discord

from dmbot.memory import threads as model
from dmbot.memory.models import (
    ABANDONED,
    CONFIRMED,
    DEED,
    DM,
    OPEN,
    RESOLVED,
    THREAD_TYPES,
    MemoryRuleError,
)
from dmbot.memory.search import MAX_RESULTS, find
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

NEW_PROMISE_BUTTON = "🤝 Add a promise"
STATE_BUTTON = "🧵 Change state"
INVOLVE_BUTTON = "👥 Add who's involved"
LINK_BUTTON = "📌 Link an event"
ONLY_DM = "Only the DM can change a story thread."
KINDS = (
    ("quest", "A quest"),
    ("mystery", "A mystery"),
    ("hook", "A hook (a lead)"),
    ("goal", "A goal"),
)
PAGE = 5  # stalled threads per page: three answer buttons each, five rows


def _stalled_after(interaction: discord.Interaction) -> int:
    settings = getattr(_bot(interaction), "settings", None)
    return int(getattr(settings, "thread_stalled_sessions", model.STALLED_AFTER))


def waiting_text(count: int, quiet: int, promises: int = 0) -> str:
    """For the DM screen after a session: how many, and for how long, never which."""
    return (
        f"🧵 **{model.stalled_line(count, quiet, promises)}** Tap to check them. "
        "Nothing changes until you say."
    )


def thread_lines(info: model.ThreadInfo, numbers: dict[int, int]) -> list[str]:
    """What a thread's card adds: its state (and that it has gone quiet), who is involved and in
    what part, the events that moved it, and the history of its state."""
    state = info.state.condition if info.state is not None else None
    kind = info.thread.type
    word = model.words_for(kind).get(state or "", "No state yet")
    quiet = ""
    if info.stalled:
        n = info.quiet_sessions
        quiet = f" — quiet for {n} {'session' if n == 1 else 'sessions'}"
    lines = []
    if kind == model.PROMISE:  # "Kael promised the village: bring the healer back"
        by = ", ".join(_md(e.name) for e in info.promised_by)
        to = ", ".join(_md(e.name) for e in info.promised_to)
        if by and to:
            lines.append(f"🤝 **{by}** promised **{to}**: {_md(info.thread.name)}")
        else:  # until the DM says who: a plain title, not a half sentence
            lines.append(f"🤝 **Promise:** {_md(info.thread.name)}")
            if by or to:
                lines.append(
                    f"**Promised by:** {by or 'not set yet'}. **To:** {to or 'not set yet'}."
                )
            else:
                lines.append("Who promised and to whom: not set yet.")
    lines.append(f"🧵 **{word}**{quiet}")
    if info.due:
        lines.append(f"**Due:** {_md(info.due)}")
    for line in (
        model.names_line("Came from", [_md(e.name) for e in info.came_from]),
        model.names_line("Led to", [_md(e.name) for e in info.led_to]),
    ):
        if line:
            lines.append(line)
    if info.involved:
        parts = [
            f"{_md(i.who.name)}" + (f" ({_md(i.role)})" if i.role else "") for i in info.involved
        ]
        lines.append("**Involved:** " + ", ".join(parts))
    if info.movers:
        parts = [f"{_md(m.event.name)}" + (" (ended it)" if m.ends_it else "") for m in info.movers]
        lines.append("**Moved by:** " + ", ".join(parts))
    if len(info.history) > 1:
        lines.append("**So far:** " + "; ".join(model.history_lines(info.history, numbers, kind)))
    return lines


# ---- New thread ----------------------------------------------------------------------------


class NewThreadForm(discord.ui.Modal, title="New story thread"):
    thread_name: discord.ui.TextInput[NewThreadForm] = discord.ui.TextInput(
        label="Name", placeholder="For example: The Missing Heir", max_length=100
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        name = " ".join(self.thread_name.value.split())
        if not name:
            await _tell(interaction, "Type a name for the thread.")
            return
        await _send(
            interaction,
            f"**{_md(name)}**: what is it, and where is it now?",
            NewThread(self.campaign_id, name),
        )


class NewThread(_Menu):
    """The kind and the state on one screen, then Create."""

    def __init__(self, campaign_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id, self.name = campaign_id, name
        self.kind, self.state = "quest", OPEN
        self.kind_pick = _Select(
            self._kind,
            placeholder="What is it?",
            options=[
                discord.SelectOption(label=label, value=key, default=key == self.kind)
                for key, label in KINDS
            ],
        )
        self.state_pick = _Select(
            self._state,
            placeholder="Where is it now?",
            options=[
                discord.SelectOption(label=model.STATE_WORDS[s], value=s, default=s == self.state)
                for s in model.ALL_STATES
            ],
            row=1,
        )
        self.add_item(self.kind_pick)
        self.add_item(self.state_pick)
        self.add_item(
            _Button(self._create, label="Create it", style=discord.ButtonStyle.primary, row=2)
        )

    used = False

    async def _kind(self, interaction: discord.Interaction) -> None:
        self.kind = self.kind_pick.values[0]
        await interaction.response.defer()

    async def _state(self, interaction: discord.Interaction) -> None:
        self.state = self.state_pick.values[0]
        await interaction.response.defer()

    async def _create(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        if self.used:
            await _tell(interaction, "Already made. Find it under Names.")
            return
        self.used = True  # before the await, so a double tap makes one thread
        try:
            made = await memory.new_thread(
                campaign.guild_id, campaign.id, self.name, self.kind, self.state, source=DM
            )
        except MemoryRuleError as exc:
            self.used = False
            await _tell(interaction, f"⚠️ {exc}")
            return
        self.stop()
        changed(interaction, campaign)
        await show_thread(interaction, campaign.id, made.value.id, note="✅ Started.")


async def show_thread(
    interaction: discord.Interaction, campaign_id: str, thread_id: str, *, note: str = ""
) -> None:
    """Draw the thread's name card with its thread lines (the card does it from the database)."""
    from dmbot.ui.name_card import show_card

    await show_card(interaction, campaign_id, thread_id, replace=True, note=note or None)


# ---- the buttons on a thread's card -----------------------------------------------------------


async def _dm_and_memory(interaction: discord.Interaction, campaign_id: str) -> Any:
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
        await _tell(interaction, ONLY_DM)
        return None
    return campaign, memory


class ChangeState(_Menu):
    """The five states, one tap each; "Checked, still going" when it is."""

    def __init__(
        self, campaign_id: str, thread_id: str, now: str | None, kind: str = "thread"
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.kind = campaign_id, thread_id, kind
        words = model.words_for(kind)
        for state in model.ALL_STATES:
            self.add_item(
                _Button(
                    partial(self._set, state),
                    label=words[state],
                    style=discord.ButtonStyle.primary
                    if state == now
                    else discord.ButtonStyle.secondary,
                )
            )
        if now in model.LIVE_STATES:
            self.add_item(_Button(self._still, label="✔ Checked, still going", row=1))
        self.add_item(_Button(self._back, label="↩ Back", row=1))

    async def _set(self, state: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            moved = await memory.set_condition(
                campaign.guild_id, campaign.id, self.thread_id, state, source=DM
            )
            if moved.value[0] is None and state in model.LIVE_STATES:
                # The state it is already in: the DM means "still", so it counts as checked.
                await memory.check_thread(campaign.guild_id, campaign.id, self.thread_id, source=DM)
        except MemoryRuleError as exc:
            await show_thread(interaction, self.campaign_id, self.thread_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        await show_thread(
            interaction,
            self.campaign_id,
            self.thread_id,
            note=f"✅ Marked: {model.words_for(self.kind)[state]}.",
        )

    async def _still(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            await memory.check_thread(campaign.guild_id, campaign.id, self.thread_id, source=DM)
        except MemoryRuleError as exc:
            await show_thread(interaction, self.campaign_id, self.thread_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        await show_thread(
            interaction, self.campaign_id, self.thread_id, note="✅ Checked: still going."
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id)


async def open_change_state(
    interaction: discord.Interaction, campaign_id: str, thread_id: str
) -> None:
    got = await _dm_and_memory(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    infos = [
        t for t in await memory.threads(campaign.guild_id, campaign.id) if t.thread.id == thread_id
    ]
    if not infos:
        await _tell(interaction, GONE)
        return
    now = infos[0].state.condition if infos[0].state is not None else None
    await _replace(
        interaction,
        f"**{_md(infos[0].thread.name)}**: where is it now?",
        ChangeState(campaign_id, thread_id, now, infos[0].thread.type),
    )


class InvolveForm(discord.ui.Modal, title="Who is involved?"):
    who: discord.ui.TextInput[InvolveForm] = discord.ui.TextInput(
        label="A name", placeholder="A character, a group, a place or an item", max_length=100
    )
    role: discord.ui.TextInput[InvolveForm] = discord.ui.TextInput(
        label="Their part (optional)",
        placeholder="quest giver, target, villain, victim, reward…",
        required=False,
        max_length=60,
    )

    def __init__(self, campaign_id: str, thread_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id = campaign_id, thread_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await link_by_name(
            interaction,
            self.campaign_id,
            self.thread_id,
            self.who.value,
            kinds=None,
            then=partial(_save_involved, role=" ".join(self.role.value.split())),
        )


async def link_by_name(
    interaction: discord.Interaction,
    campaign_id: str,
    thread_id: str,
    typed: str,
    *,
    kinds: tuple[str, ...] | None,
    then: Any,
    make: str | None = None,
    threads_too: bool = False,
) -> None:
    """Find the one entry the DM means by name (the same search as Find a name), and hand it to
    `then`. Several: a pick list. None: nothing, or the entry made on the spot when `make` names
    a kind (an event)."""
    got = await _dm_and_memory(interaction, campaign_id)
    cache = getattr(interaction.client, "lookup", None)
    if got is None or cache is None:
        return
    campaign, memory = got
    names = await cache.get(campaign.guild_id, campaign.id)
    found = [
        m.entity_id
        for m in find(names, typed, secrets=True)
        if names.entities[m.entity_id].status == CONFIRMED
        and names.entities[m.entity_id].type != "person"
        and m.entity_id != thread_id
        and names.entities[m.entity_id].type != DEED
        and (threads_too or names.entities[m.entity_id].type not in THREAD_TYPES)
        and (kinds is None or names.entities[m.entity_id].type in kinds)
    ][:MAX_RESULTS]
    if len(found) == 1:
        await then(interaction, campaign_id, thread_id, found[0])
        return
    if not found:
        if make is not None:
            name = " ".join(typed.split())
            # Typed by the DM, so it starts unrevealed like any name added by hand (#2404).
            made = await memory.add_entity(
                campaign.guild_id, campaign.id, type=make, name=name, source=DM, status=CONFIRMED,
                hidden_from_players=True,
            )  # fmt: skip
            changed(interaction, campaign)
            await then(interaction, campaign_id, thread_id, made.value.id)
            return
        await show_thread(
            interaction,
            campaign_id,
            thread_id,
            note=f"Nothing called **{_md(typed)}** yet. Add it under Names, then try again.",
        )
        return
    options = [
        discord.SelectOption(
            label=logic.shorten(names.entities[i].name, logic.OPTION_LABEL_MAX), value=i
        )
        for i in found
    ]
    await _replace(
        interaction,
        f"**Which one is {_md(typed)}?**",
        PickOne(campaign_id, thread_id, options, then),
    )


class PickOne(_Menu):
    def __init__(
        self, campaign_id: str, thread_id: str, options: list[discord.SelectOption], then: Any
    ) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.then = campaign_id, thread_id, then
        self.pick = _Select(self._picked, placeholder="Pick one…", options=options)
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await self.then(interaction, self.campaign_id, self.thread_id, self.pick.values[0])


async def _save_involved(
    interaction: discord.Interaction, campaign_id: str, thread_id: str, who_id: str, *, role: str
) -> None:
    got = await _dm_and_memory(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    try:
        await memory.involve(campaign.guild_id, campaign.id, thread_id, who_id, role, source=DM)
    except MemoryRuleError as exc:
        await show_thread(interaction, campaign_id, thread_id, note=f"⚠️ {exc}")
        return
    changed(interaction, campaign)
    await show_thread(interaction, campaign_id, thread_id, note="✅ Added.")


class PartyForm(discord.ui.Modal):
    """Who made a promise, or who it was made to: a name, found like the other links."""

    who: discord.ui.TextInput[PartyForm] = discord.ui.TextInput(
        label="A name", placeholder="A character or a group", max_length=100
    )

    def __init__(self, campaign_id: str, promise_id: str, *, to: bool) -> None:
        super().__init__(
            title="Who was it promised to?" if to else "Who promised?", timeout=VIEW_TIMEOUT_S
        )
        self.campaign_id, self.promise_id, self.to = campaign_id, promise_id, to
        if to:
            self.who.placeholder = "A character, a group or a place"

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await link_by_name(
            interaction,
            self.campaign_id,
            self.promise_id,
            self.who.value,
            kinds=("character", "faction", "place") if self.to else ("character", "faction"),
            then=partial(_save_party, to=self.to),
        )


async def _save_party(
    interaction: discord.Interaction, campaign_id: str, promise_id: str, who_id: str, *, to: bool
) -> None:
    got = await _dm_and_memory(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    try:
        await memory.promise_party(
            campaign.guild_id, campaign.id, promise_id, who_id, to=to, source=DM
        )
    except MemoryRuleError as exc:
        await show_thread(interaction, campaign_id, promise_id, note=f"⚠️ {exc}")
        return
    changed(interaction, campaign)
    await show_thread(interaction, campaign_id, promise_id, note="✅ Added.")


class DueForm(discord.ui.Modal, title="When is it due?"):
    due: discord.ui.TextInput[DueForm] = discord.ui.TextInput(
        label="Your note (empty clears it)",
        placeholder="By the full moon",
        required=False,
        max_length=120,
    )

    def __init__(self, campaign_id: str, promise_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.promise_id = campaign_id, promise_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            await memory.set_due(
                campaign.guild_id, campaign.id, self.promise_id, self.due.value, source=DM
            )
        except MemoryRuleError as exc:
            await show_thread(interaction, self.campaign_id, self.promise_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        await show_thread(interaction, self.campaign_id, self.promise_id, note="✅ Saved.")


class NewPromiseForm(discord.ui.Modal, title="Add a promise"):
    what: discord.ui.TextInput[NewPromiseForm] = discord.ui.TextInput(
        label="What was promised",
        placeholder="Bring the healer back",
        max_length=100,
    )
    due: discord.ui.TextInput[NewPromiseForm] = discord.ui.TextInput(
        label="When is it due? (optional)",
        placeholder="By the full moon",
        required=False,
        max_length=120,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        what = " ".join(self.what.value.split())
        if not what:
            await _tell(interaction, "Type what was promised.")
            return
        try:
            made = await memory.new_thread(
                campaign.guild_id, campaign.id, what, model.PROMISE, OPEN, source=DM
            )
            if self.due.value.strip():
                await memory.set_due(
                    campaign.guild_id, campaign.id, made.value.id, self.due.value, source=DM
                )
        except MemoryRuleError as exc:
            await _tell(interaction, f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        await show_thread(
            interaction,
            campaign.id,
            made.value.id,
            note="✅ Added. Tap Who promised and Promised to to fill in the rest.",
        )


class LedToForm(discord.ui.Modal, title="Led to…"):
    """From a clue, an event or a thread: what it led to, a thread or an event of this campaign."""

    target: discord.ui.TextInput[LedToForm] = discord.ui.TextInput(
        label="What it led to", placeholder="A thread or an event", max_length=100
    )

    def __init__(self, campaign_id: str, from_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.from_id = campaign_id, from_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        memory = _memory(interaction)
        kinds: tuple[str, ...] | None = None
        if memory is not None and interaction.guild is not None:
            ontology = await memory.ontology(interaction.guild.id, self.campaign_id)
            kinds = tuple(
                k for k in ontology.types if ontology.is_a(k, "event") or ontology.is_a(k, "thread")
            )
        await link_by_name(
            interaction,
            self.campaign_id,
            self.from_id,
            self.target.value,
            kinds=kinds,
            then=_save_led_to,
            threads_too=True,
        )


async def _save_led_to(
    interaction: discord.Interaction, campaign_id: str, from_id: str, to_id: str
) -> None:
    got = await _dm_and_memory(interaction, campaign_id)
    if got is None:
        return
    campaign, memory = got
    try:
        await memory.led_to(campaign.guild_id, campaign.id, from_id, to_id, source=DM)
    except MemoryRuleError as exc:
        await show_thread(interaction, campaign_id, from_id, note=f"⚠️ {exc}")
        return
    changed(interaction, campaign)
    await show_thread(interaction, campaign_id, from_id, note="✅ Linked.")


class LinkForm(discord.ui.Modal, title="Link an event"):
    event: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="The event", placeholder="For example: The ambush at the ford", max_length=100
    )

    def __init__(self, campaign_id: str, thread_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.thread_id = campaign_id, thread_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await link_by_name(
            interaction,
            self.campaign_id,
            self.thread_id,
            self.event.value,
            kinds=("event",),
            then=_ask_how,
            make="event",
        )


async def _ask_how(
    interaction: discord.Interaction, campaign_id: str, thread_id: str, event_id: str
) -> None:
    memory = _memory(interaction)
    event = (
        await memory.entity(interaction.guild.id, campaign_id, event_id)
        if memory and interaction.guild
        else None
    )
    name = _md(event.name) if event is not None else "It"
    await _replace(
        interaction,
        f"**{name}**: did it move the thread along, or end it?",
        HowLinked(campaign_id, thread_id, event_id),
    )


class HowLinked(_Menu):
    def __init__(self, campaign_id: str, thread_id: str, event_id: str) -> None:
        super().__init__()
        self.campaign_id, self.thread_id, self.event_id = campaign_id, thread_id, event_id
        self.add_item(
            _Button(
                partial(self._link, False),
                label="Moved the thread",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(_Button(partial(self._link, True), label="Ended the thread"))

    async def _link(self, ends_it: bool, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            await memory.link_event(
                campaign.guild_id,
                campaign.id,
                self.thread_id,
                self.event_id,
                ends_it=ends_it,
                source=DM,
            )
        except MemoryRuleError as exc:
            await show_thread(interaction, self.campaign_id, self.thread_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        if ends_it:  # one tap offered; the state does not change by itself
            await _replace(
                interaction,
                "✅ Linked. Is the thread resolved now?",
                MarkResolved(self.campaign_id, self.thread_id),
            )
            return
        await show_thread(interaction, self.campaign_id, self.thread_id, note="✅ Linked.")


class MarkResolved(_Menu):
    def __init__(self, campaign_id: str, thread_id: str) -> None:
        super().__init__()
        self.campaign_id, self.thread_id = campaign_id, thread_id
        self.add_item(
            _Button(self._yes, label="Yes, it's resolved", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="Not yet"))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            await memory.set_condition(
                campaign.guild_id, campaign.id, self.thread_id, RESOLVED, source=DM
            )
        except MemoryRuleError as exc:
            await show_thread(interaction, self.campaign_id, self.thread_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id, note="✅ Now resolved.")

    async def _no(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_thread(interaction, self.campaign_id, self.thread_id)


# ---- after a session: the quiet ones ---------------------------------------------------------


class ThreadsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:threads:review:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🧵 Check the threads",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:threads:review:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ThreadsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_review(interaction, self.campaign_id)


class ThreadListButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:threads:list:(?P<campaign>[0-9a-f]{32})",
):
    """🧵 Threads, on the pre-session note (#1675): the DM's thread list, in play first."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🧵 Threads",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:threads:list:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ThreadListButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_review(interaction, self.campaign_id, all_live=True)


def threads_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ThreadsButton(campaign_id))
    return view


NO_LIVE_THREADS = "No live threads right now. Add one under New thread."
NOTHING_LEFT = "✅ No threads need a check right now. DMbot looks again after your next session."


async def start_review(
    interaction: discord.Interaction, campaign_id: str, *, all_live: bool = False
) -> None:
    if await _dm_and_memory(interaction, campaign_id) is None:
        return
    await _answer_first(interaction)  # the read below can be slow: answer within Discord's 3 s
    review = ThreadsReview(campaign_id, all_live=all_live)
    text = await review.current_text(interaction)
    if text is None:
        await _tell(interaction, NO_LIVE_THREADS if all_live else NOTHING_LEFT)
        return
    await _send(interaction, text, review)


class ThreadsReview(_Menu):
    """The threads that need a word, each with Still going and the two ends. By default only the
    quiet ones (after a session); `all_live` lists every thread that is still going, the ones in
    play first, with the quiet ones marked (the 🧵 Threads button on the pre-session note)."""

    PAGE_ALL = 4  # four threads and a row of paging

    def __init__(self, campaign_id: str, *, all_live: bool = False) -> None:
        super().__init__()
        self.campaign_id, self.all_live = campaign_id, all_live
        self.page: list[model.ThreadInfo] = []
        self.total = 0
        self.offset = 0

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        memory = _memory(interaction)
        campaign = await _campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return None
        found = await memory.threads(
            campaign.guild_id, campaign.id, stalled_after=_stalled_after(interaction)
        )
        if self.all_live:
            chosen = model.in_play_first(
                t for t in found if t.state is not None and t.state.condition in model.LIVE_STATES
            )
            size = self.PAGE_ALL
            self.offset = min(self.offset, max(0, (len(chosen) - 1) // size * size))
        else:
            chosen = [t for t in found if t.stalled]
            size = PAGE
        self.page, self.total = chosen[self.offset : self.offset + size], len(chosen)
        self.clear_items()
        if not self.page:
            return None
        for row, info in enumerate(self.page):
            promise = info.thread.type == model.PROMISE
            for key, label in (
                ("still", "Still going"),
                ("resolved", "Kept" if promise else "Finished"),
                ("dropped", "Broken" if promise else "Dropped"),
            ):
                self.add_item(
                    _Button(
                        partial(self._answer, info.thread.id, key),
                        label=f"{row + 1} · {label}",
                        row=row,
                    )
                )
        if self.all_live and self.total > size:
            if self.offset > 0:
                self.add_item(_Button(self._back, label="◀ Back", row=4))
            if self.offset + size < self.total:
                self.add_item(_Button(self._next, label="Next ▶", row=4))
        head = (
            "**Your threads.** ⏳ means it has gone quiet. Tap what is true in your story."
            if self.all_live
            else "**Threads that haven't moved.** Tap what is true in your story."
        )
        lines = [head + " You can change it later on the thread's card."]
        for n, info in enumerate(self.page, 1):
            words = model.words_for(info.thread.type)
            state = info.state.condition if info.state is not None else ""
            if info.stalled:
                s = info.quiet_sessions
                where = f"quiet for {s} {'session' if s == 1 else 'sessions'}"
                mark = "⏳ " if self.all_live else ""
            else:
                where, mark = words.get(state, ""), ""
            lines.append(f"{n}. {mark}**{_md(info.thread.name)}**: {where}.")
        if not self.all_live and self.total > len(self.page):
            lines.append(
                f"\n_{self.total - len(self.page)} more waiting. They show when these are done._"
            )
        return "\n".join(lines)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, -self.PAGE_ALL)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, self.PAGE_ALL)

    async def _turn(self, interaction: discord.Interaction, by: int) -> None:
        await _answer_first(interaction, in_place=True)
        if await _dm_and_memory(interaction, self.campaign_id) is None:
            return
        self.offset = max(0, self.offset + by)
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, NOTHING_LEFT, None)
            return
        await _replace(interaction, text, self)

    async def _answer(self, thread_id: str, key: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        got = await _dm_and_memory(interaction, self.campaign_id)
        if got is None:
            return
        campaign, memory = got
        try:
            if key == "still":
                await memory.check_thread(campaign.guild_id, campaign.id, thread_id, source=DM)
            else:
                await memory.set_condition(
                    campaign.guild_id,
                    campaign.id,
                    thread_id,
                    RESOLVED if key == "resolved" else ABANDONED,
                    source=DM,
                )
        except MemoryRuleError as exc:
            await _tell(interaction, f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        log.info("Thread answered: %s", key)  # a count, never a name
        fresh = await self.current_text(interaction)
        if fresh is None:
            self.stop()
            await _replace(interaction, NOTHING_LEFT, None)
            return
        await _replace(interaction, fresh, self)


__all__ = [
    "ChangeState",
    "DueForm",
    "InvolveForm",
    "LedToForm",
    "LinkForm",
    "NewPromiseForm",
    "NewThreadForm",
    "PartyForm",
    "ThreadListButton",
    "ThreadsButton",
    "ThreadsReview",
    "threads_view",
    "waiting_text",
]
