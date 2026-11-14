"""World notes in Discord (#4285 slice 1b; Supervisor's ruling, 2026-11-14, and the ux-critic's
words): the DM's private screen of DMbot's guesses about the world (`memory/world_notes.py`). Pick
one note from the menu, see it on its own with where DMbot read it, then ✅ Keep, ✏️ Change (the
place's own 🗺️ screen, or the character's languages), 🚫 Not right, or ↩ Back; ✅ Keep all keeps
every line of one group the screen showed. Each press is one change, so one Undo.

DM only, checked on every press (a menu can be pressed minutes later); only ever a private reply,
never on the DM screen or where players are. Names and the lists' own words only."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, SELECT_OPTIONS_MAX
from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    Handler,
    _answer_first,
    _Button,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets
from dmbot.ui.place_world import RULED, climate_words, land_words

log = logging.getLogger(__name__)

INTRO = (
    "🌍 **World notes**: DMbot's guesses about your world. Only you see these. Keep the ones that "
    "are right; press 🚫 Not right on the rest. DMbot treats these as guesses until you check them."
)
EMPTY = "🌍 **World notes**: nothing to check. 🎉"
PICK = "Pick a note to check…"
MORE = "Showing {shown} of {total}. Check some and the rest come up."
ONLY_DM = "Only a DM of this campaign can check world notes."
FAILED = "Something went wrong. Nothing was changed. Press the button again."
STALE = "Someone already checked or changed that note. Here's the list as it is now."
KEEP, CHANGE, NOT_RIGHT, BACK, UNDO = "✅ Keep", "✏️ Change", "🚫 Not right", "↩ Back", "↩ Undo"
UNDONE = "↩ Undone."
TOO_LATE = "Too late to undo. Open the place or the character to change it."
NOT_UNDONE = "Not undone, because something changed since. Open the place or the character."
NEVER_AGAIN = "DMbot won't guess this again."  # (a place and a language alike since #4362)
MENU_MAX = SELECT_OPTIONS_MAX  # (Discord's limit for a menu's choices)
LIST_MAX = MESSAGE_MAX - 300  # the list, so the reply with a line above it stays inside one message
LAYER = {mf.TERRAIN: "Land", mf.CLIMATE: "Climate", mf.CONTROL: "Ruler"}
GROUP = {wn.PLACE: "Places", wn.LANGUAGE: "Languages"}
ONE = {wn.PLACE: "place", wn.LANGUAGE: "language"}


def counted(n: int, kind: str) -> str:
    """ "1 place", "12 places"."""
    return f"{n} {ONE[kind]}" if n == 1 else f"{n} {GROUP[kind].lower()}"


def keep_all_label(n: int, kind: str) -> str:
    return f"✅ Keep the {counted(n, kind)}" if n == 1 else f"✅ Keep all {counted(n, kind)}"


def guess(note: wn.Note) -> str:
    """What DMbot guessed, in the place screen's own words ("arctic", "Ruled by: Ten-Towns")."""
    if note.kind == wn.LANGUAGE:
        return note.value
    f = note.fact
    if note.layer == mf.CONTROL:
        return f"{RULED.get(note.how or 'rules', 'Ruled by').lower()} {note.ruler}"
    if f is None:
        return note.value
    return land_words(f) if note.layer == mf.TERRAIN else climate_words(f)


def source(note: wn.Note) -> str:
    return "from a session" if note.session_at is not None else "from your lore"


def full_line(note: wn.Note) -> str:
    """The note in the message text, where it wraps: "**Icewind Dale** · land: arctic"."""
    if note.kind == wn.LANGUAGE:
        return f"**{_md(note.name)}** speaks {_md(note.value)}"
    if note.layer == mf.CONTROL:  # ("ruled by Ten-Towns" says what it is: no "ruler:" before it)
        return f"**{_md(note.name)}** · {_md(guess(note))}"
    return f"**{_md(note.name)}** · {LAYER[note.layer].lower()}: {_md(guess(note))}"


def option(note: wn.Note) -> discord.SelectOption:
    """A menu choice short enough for a phone: the guess in the label, the rest below it."""
    what = "Language" if note.kind == wn.LANGUAGE else LAYER[note.layer]
    return discord.SelectOption(
        label=logic.shorten(f"{note.name}: {guess(note)}", 100),
        description=logic.shorten(f"{what} · {source(note)}", 100),
        value=note.id,
    )


def shown_notes(notes: Sequence[wn.Note]) -> list[wn.Note]:
    """The lines the screen shows: in the groups' order, as many as fit the message and the menu
    (25). Text, menu and ✅ Keep all all use these, so Keep all never keeps a line the DM didn't
    see (#4285)."""
    out: list[wn.Note] = []
    used = len(INTRO) + len(MORE) + 20  # (room for the "Showing … of …" line)
    for kind in wn.KINDS:
        group = [n for n in notes if n.kind == kind]
        if not group:
            continue
        used += len(GROUP[kind]) + 6
        for n in group:
            line = len(full_line(n)) + 3
            if used + line > LIST_MAX or len(out) >= MENU_MAX:
                return out  # (the whole list stops here, so no later group skips ahead)
            out.append(n)
            used += line
    return out


def list_text(notes: Sequence[wn.Note]) -> str:
    """The intro, then the lines shown by group, and how many are shown when not all."""
    if not notes:
        return EMPTY
    shown = shown_notes(notes)
    lines = [INTRO]
    for kind in wn.KINDS:
        group = [n for n in shown if n.kind == kind]
        if group:
            lines.append(f"\n**{GROUP[kind]}**")
            lines.extend(f"• {full_line(n)}" for n in group)
    if len(shown) < len(notes):
        lines.append(MORE.format(shown=len(shown), total=len(notes)))
    return "\n".join(lines)


def note_text(note: wn.Note) -> str:
    return f"{full_line(note)}\nDMbot guessed this {source(note)}."


def done_text(note: wn.Note, kept: bool) -> str:
    if kept:
        return f"✅ Kept: {full_line(note)}."
    return f"🚫 Taken out: {full_line(note)}. {NEVER_AGAIN}"


async def show_world_notes(
    interaction: discord.Interaction, campaign_id: str, note: str = ""
) -> None:
    """The list, in place of what the DM pressed (a private reply). `note`: a line above it."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):  # (checked before the list is read)
        await _tell(interaction, ONLY_DM)
        return
    notes = await memory.world_notes(campaign.guild_id, campaign.id)
    text = list_text(notes)
    if note:
        text = f"{note}\n\n{text}"
    shown = shown_notes(notes)
    await _replace(interaction, text, NotesMenu(campaign_id, shown) if shown else None)


class NotesMenu(_Menu):
    """The list's controls: pick one note, or keep a whole group, of the lines shown only."""

    def __init__(self, campaign_id: str, shown: Sequence[wn.Note]) -> None:
        super().__init__()
        self.campaign_id, self.notes = campaign_id, list(shown)[:MENU_MAX]
        self.pick = _Select(self._picked, placeholder=PICK, options=[option(n) for n in self.notes])
        self.add_item(self.pick)
        for kind in wn.KINDS:
            ids = wn.of_kind(self.notes, kind)
            if ids:
                label = keep_all_label(len(ids), kind)
                self.add_item(_Button(self._keep_all(kind), label=label, row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # (every press, as the others)
            await _tell(interaction, ONLY_DM)
            return
        chosen = next((n for n in self.notes if n.id == self.pick.values[0]), None)
        if chosen is None:
            await show_world_notes(interaction, self.campaign_id, STALE)
            return
        self.stop()
        await _replace(interaction, note_text(chosen), OneNote(self.campaign_id, chosen))

    def _keep_all(self, kind: str) -> Handler:
        async def press(interaction: discord.Interaction) -> None:
            ids = wn.of_kind(self.notes, kind)  # (exactly the lines this screen showed)
            await _act(interaction, self.campaign_id, ids, lambda n: f"✅ Kept {counted(n, kind)}.")

        return press


class OneNote(_Menu):
    """One note on its own: ✅ Keep, ✏️ Change, 🚫 Not right, ↩ Back."""

    def __init__(self, campaign_id: str, note: wn.Note) -> None:
        super().__init__()
        self.campaign_id, self.note = campaign_id, note
        self.add_item(_Button(self._keep, label=KEEP, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._change, label=CHANGE))
        self.add_item(_Button(self._not_right, label=NOT_RIGHT, style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._back, label=BACK))

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _act(
            interaction, self.campaign_id, [self.note.id], lambda _: done_text(self.note, True)
        )

    async def _not_right(self, interaction: discord.Interaction) -> None:
        await _act(
            interaction, self.campaign_id, [self.note.id], lambda _: done_text(self.note, False),
            turn_down=True,
        )  # fmt: skip

    async def _change(self, interaction: discord.Interaction) -> None:
        self.stop()
        if self.note.kind == wn.PLACE:
            from dmbot.ui.place_world import show_world

            await show_world(interaction, self.campaign_id, self.note.entity_id)
            return
        from dmbot.ui.languages_card import show_languages

        await show_languages(interaction, self.campaign_id, self.note.entity_id)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_world_notes(interaction, self.campaign_id)


async def _act(
    interaction: discord.Interaction,
    campaign_id: str,
    ids: Sequence[str],
    done: Callable[[int], str],
    *,
    turn_down: bool = False,
) -> None:
    """Keep these lines, or turn one down, as one change; then say so (`done` gets how many were
    kept), with ↩ Undo."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):  # (asked again as it is saved)
        await _tell(interaction, ONLY_DM)
        return
    try:
        if turn_down:
            written = await memory.turn_down_world_note(
                campaign.guild_id, campaign.id, ids[0], source=DM
            )
            batch, n = written.batch, 1
        else:
            kept = await memory.keep_world_notes(campaign.guild_id, campaign.id, ids, source=DM)
            batch, n = kept.batch, kept.value
    except MemoryRuleError as exc:
        if str(exc) != wn.GONE:
            await _tell(interaction, str(exc))  # (a rule's own words, never a false "stale")
            return
        await show_world_notes(interaction, campaign_id, STALE)
        return
    except Exception as exc:  # (one plain reply, the type only in the log)
        log.warning("World notes: a check couldn't be saved (%s)", type(exc).__name__)
        await _tell(interaction, FAILED)
        return
    changed(interaction, campaign)
    await _replace(interaction, done(n), Done(campaign_id, batch))


class Done(_Menu):
    """After a press: ↩ Undo, and back to the list."""

    def __init__(self, campaign_id: str, batch: int | None) -> None:
        super().__init__()
        self.campaign_id, self.batch = campaign_id, batch
        if batch is not None:
            self.add_item(_Button(self._undo, label=UNDO))
        self.add_item(_Button(self._back, label="🌍 Back to world notes"))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None or self.batch is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DM)
            return
        text = UNDONE
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
            changed(interaction, campaign)
        except TooLateToUndo:
            text = TOO_LATE
        except MemoryRuleError:
            text = NOT_UNDONE
        self.stop()
        await show_world_notes(interaction, self.campaign_id, text)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_world_notes(interaction, self.campaign_id)


class WorldNotesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:questions:world:(?P<campaign>[0-9a-f]{32})",
):
    """🌍 World notes, from the ❓ Questions for you message (Supervisor's ruling on #4285). It
    works after a restart (the campaign is in the button's ID); the DM check is the screen's own."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🌍 World notes",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:questions:world:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WorldNotesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        # (a private "thinking" answer first: the screen replaces the message it opens from, and
        # that must be this private one, never the Questions message, as Look-alikes does)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await show_world_notes(interaction, self.campaign_id)
