"""World notes in Discord (#4285 slice 1b; Supervisor's ruling, 2026-11-14, and the ux-critic's
words): the DM's private screen of DMbot's guesses about the world (`memory/world_notes.py`). Pick
one note from the menu, see it on its own with where DMbot read it, then ✅ Keep, ✏️ Change (the
place's own 🗺️ screen, or the character's languages), 🚫 Not right, or ↩ Back; ✅ Keep all keeps
every line of one group the screen showed. Each press is one change, so one Undo.

DM only, checked on every press (a menu can be pressed minutes later); only ever a private reply,
never on the DM screen or where players are. Names and the lists' own words only."""

from __future__ import annotations

from collections.abc import Sequence

import discord

from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _Select, _tell
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets
from dmbot.ui.place_world import RULED, climate_words, land_words

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
NEVER_AGAIN = "DMbot won't guess this again."
MAY_COME_BACK = "If your lore says it again later, it comes back here to check."
MENU_MAX = 25  # (Discord's limit for a menu's choices)
LIST_MAX = 1700  # the list in the message, so the whole reply stays inside Discord's 2,000
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
    return f"**{_md(note.name)}** · {LAYER[note.layer].lower()}: {_md(guess(note))}"


def option(note: wn.Note) -> discord.SelectOption:
    """A menu choice short enough for a phone: the guess in the label, the rest below it."""
    what = "Language" if note.kind == wn.LANGUAGE else LAYER[note.layer]
    return discord.SelectOption(
        label=logic.shorten(f"{note.name}: {guess(note)}", 100),
        description=logic.shorten(f"{what} · {source(note)}", 100),
        value=note.id,
    )


def list_text(notes: Sequence[wn.Note]) -> str:
    """The intro, then each group's lines cut to fit, and how many are shown when not all."""
    if not notes:
        return EMPTY
    lines = [INTRO]
    used = len(INTRO)
    shown = 0
    for kind in wn.KINDS:
        group = [n for n in notes if n.kind == kind]
        if not group:
            continue
        head = f"\n**{GROUP[kind]}**"
        if used + len(head) > LIST_MAX:
            break
        lines.append(head)
        used += len(head)
        for n in group:
            line = f"• {full_line(n)}"
            if used + len(line) + 1 > LIST_MAX:
                break
            lines.append(line)
            used += len(line) + 1
            shown += 1
    if shown < len(notes) or len(notes) > MENU_MAX:
        lines.append(MORE.format(shown=min(shown, MENU_MAX), total=len(notes)))
    return "\n".join(lines)


def note_text(note: wn.Note) -> str:
    where = "from a session" if note.session_at is not None else "from your lore"
    return f"{full_line(note)}\nDMbot guessed this {where}."


def done_text(note: wn.Note, kept: bool) -> str:
    if kept:
        return f"✅ Kept: {full_line(note)}."
    after = NEVER_AGAIN if note.kind == wn.PLACE else MAY_COME_BACK
    return f"🚫 Taken out: {full_line(note)}. {after}"


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
    await _replace(interaction, text, NotesMenu(campaign_id, notes) if notes else None)


class NotesMenu(_Menu):
    """The list's controls: pick one note, or keep a whole group the DM was shown."""

    def __init__(self, campaign_id: str, notes: Sequence[wn.Note]) -> None:
        super().__init__()
        self.campaign_id, self.notes = campaign_id, list(notes)
        self.pick = _Select(
            self._picked, placeholder=PICK, options=[option(n) for n in self.notes[:MENU_MAX]]
        )
        self.add_item(self.pick)
        for kind in wn.KINDS:
            ids = wn.of_kind(self.notes, kind)
            if ids:
                label = keep_all_label(len(ids), kind)
                self.add_item(_Button(self._keep_all(kind), label=label, row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        chosen = next((n for n in self.notes if n.id == self.pick.values[0]), None)
        if chosen is None:
            await show_world_notes(interaction, self.campaign_id, STALE)
            return
        self.stop()
        await _replace(interaction, note_text(chosen), OneNote(self.campaign_id, chosen))

    def _keep_all(self, kind: str):  # type: ignore[no-untyped-def]  # (a button's callback)
        async def press(interaction: discord.Interaction) -> None:
            ids = wn.of_kind(self.notes, kind)  # (exactly the lines this screen showed)
            await _act(
                interaction, self.campaign_id, ids, None, f"✅ Kept {counted(len(ids), kind)}."
            )

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
            interaction, self.campaign_id, [self.note.id], self.note, done_text(self.note, True)
        )

    async def _not_right(self, interaction: discord.Interaction) -> None:
        await _act(
            interaction, self.campaign_id, [self.note.id], self.note,
            done_text(self.note, False), turn_down=True,
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
    note: wn.Note | None,
    done: str,
    *,
    turn_down: bool = False,
) -> None:
    """Keep these lines, or turn one down, as one change; then say so, with ↩ Undo."""
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
            assert note is not None
            written = await memory.turn_down_world_note(
                campaign.guild_id, campaign.id, note.id, source=DM
            )
            batch = written.batch
        else:
            kept = await memory.keep_world_notes(campaign.guild_id, campaign.id, ids, source=DM)
            batch = kept.batch
    except MemoryRuleError:
        await show_world_notes(interaction, campaign_id, STALE)
        return
    except Exception:  # (the type only: the DM is told nothing changed, and may press again)
        await _tell(interaction, FAILED)
        raise
    changed(interaction, campaign)
    await _replace(interaction, done, Done(campaign_id, batch))


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
