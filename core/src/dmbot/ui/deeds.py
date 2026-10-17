"""⚔️ Record a deed, on a player character's or the party's name card (#1732; docs/STORY_MEMORY.md,
section 5): the DM types what happened, picks who it mattered to (up to five NPCs, groups or
places) and how it landed, confirms once, and can undo it. The deed then moves how those it
mattered to feel about the doer (🤝 Standing lists it as a reason). DM only, asked again on every
step; never shown to players or put in a transcript. Nothing here reads speech: DMbot records only
what the DM says happened.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import discord

from dmbot.memory import standing
from dmbot.memory.models import (
    CONFIRMED,
    DEED_TEXT_MAX,
    DEED_VIEWERS_MAX,
    DM,
    Entity,
    MemoryRuleError,
    TooLateToUndo,
)
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
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

BUTTON = "⚔️ Record a deed"  # on the name card
ONLY_DM = "Only the DM can record a deed."
NOBODY = "No one to pick yet. Add an NPC, group or place under Names, then record the deed again."
UNDONE = "↩ Undone."
PAGE = logic.SELECT_OPTIONS_MAX


async def _checked(interaction: discord.Interaction, campaign_id: str) -> tuple[Any, Any] | None:
    """The campaign and its memory, if this person is one of its DMs (asked on every step)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    return campaign, memory


async def start(interaction: discord.Interaction, campaign_id: str, doer_id: str) -> None:
    """The button: a form for what happened."""
    found = await _checked(interaction, campaign_id)
    if found is None:
        return
    campaign, memory = found
    doer = await memory.entity(campaign.guild_id, campaign.id, doer_id)
    if doer is None or doer.status != CONFIRMED:
        await _tell(interaction, GONE)
        return
    # (The form opens at once: Discord gives three seconds. Whether there is anyone to pick is
    # asked when it is sent.)
    await interaction.response.send_modal(DeedForm(campaign_id, doer))


class DeedForm(discord.ui.Modal, title="Record a deed"):
    text: discord.ui.TextInput[DeedForm] = discord.ui.TextInput(
        label="What happened?",
        placeholder="Saved the mill from the fire",
        style=discord.TextStyle.paragraph,
        max_length=DEED_TEXT_MAX,
    )

    def __init__(self, campaign_id: str, doer: Entity) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.doer = campaign_id, doer

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        words = " ".join(self.text.value.split())
        if not words:
            await _tell(interaction, "You didn't type anything. Tap Record a deed to try again.")
            return
        mattering = await _can_matter(memory, campaign, self.doer.id)
        if not mattering:
            await _replace(interaction, NOBODY, None)
            return
        await _replace(
            interaction,
            who_text(self.doer, words, []),
            WhoMenu(self.campaign_id, self.doer, words, mattering),
        )


async def _can_matter(memory: Any, campaign: Any, doer_id: str) -> list[Entity]:
    """Whom a deed can matter to: confirmed NPCs, groups and places, by name (not the party)."""
    everyone = await memory.entities(campaign.guild_id, campaign.id)
    pool = [
        e
        for e in everyone
        if e.status == CONFIRMED
        and e.id != doer_id
        and not e.party
        and (
            e.type in ("faction", "place") or (e.type == "character" and not e.is_player_character)
        )
    ]
    return sorted(pool, key=lambda e: (e.name.casefold(), e.id))


def who_text(doer: Entity, words: str, chosen: list[Entity]) -> str:
    lines = [
        f"⚔️ **{_md(doer.name)}**: “{_md(words)}”",
        f"Who did it matter to? (up to {DEED_VIEWERS_MAX})",
    ]
    if chosen:
        lines.append("Picked: " + ", ".join(f"**{_md(e.name)}**" for e in chosen))
    return "\n".join(lines)


class WhoMenu(_Menu):
    """Up to five from the list (pages of 25), then on to how it landed."""

    def __init__(
        self, campaign_id: str, doer: Entity, words: str, pool: list[Entity], page: int = 0,
        chosen: dict[str, Entity] | None = None,
    ) -> None:  # fmt: skip
        super().__init__()
        self.campaign_id, self.doer, self.words, self.pool = campaign_id, doer, words, pool
        self.page = max(0, min(page, (len(pool) - 1) // PAGE))
        self.chosen: dict[str, Entity] = chosen if chosen is not None else {}
        here = pool[self.page * PAGE : (self.page + 1) * PAGE]
        self.here = {e.id for e in here}
        self.select = _Select(
            self._picked,
            placeholder=f"Pick up to {DEED_VIEWERS_MAX} names…",
            min_values=0,
            # (what is picked on other pages already counts toward the five)
            max_values=max(
                1,
                min(
                    len(here),
                    DEED_VIEWERS_MAX - len([k for k in self.chosen if k not in self.here]),
                ),
            ),
            options=[
                discord.SelectOption(
                    label=logic.shorten(e.name, logic.OPTION_LABEL_MAX),
                    value=e.id,
                    default=e.id in self.chosen,
                )
                for e in here
            ],
        )
        self.add_item(self.select)
        if self.page > 0:
            self.add_item(_Button(partial(self._move, -1), label="◀ Earlier names", row=1))
        if (self.page + 1) * PAGE < len(pool):
            self.add_item(_Button(partial(self._move, 1), label="More names ▶", row=1))
        if self.chosen:
            self.add_item(
                _Button(
                    self._on, label="Next: how it went ▶", style=discord.ButtonStyle.primary, row=2
                )
            )

    def _again(self, page: int, note: str = "") -> tuple[str, WhoMenu]:
        menu = WhoMenu(self.campaign_id, self.doer, self.words, self.pool, page, self.chosen)
        text = who_text(self.doer, self.words, list(self.chosen.values()))
        return (f"{note}\n{text}" if note else text), menu

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if await _checked(interaction, self.campaign_id) is None:
            return
        by_id = {e.id: e for e in self.pool}
        for key in self.here:  # this page's choices replace what was picked from it before
            self.chosen.pop(key, None)
        for key in self.select.values:
            self.chosen[key] = by_id[key]
        note = ""
        if len(self.chosen) > DEED_VIEWERS_MAX:
            for key in list(self.chosen)[DEED_VIEWERS_MAX:]:
                del self.chosen[key]
            note = f"⚠️ Kept the first {DEED_VIEWERS_MAX}."
        self.stop()
        text, menu = self._again(self.page, note)
        await _replace(interaction, text, menu)

    async def _move(self, step: int, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if await _checked(interaction, self.campaign_id) is None:
            return
        self.stop()
        text, menu = self._again(self.page + step)
        await _replace(interaction, text, menu)

    async def _on(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if await _checked(interaction, self.campaign_id) is None:
            return
        self.stop()
        people = list(self.chosen.values())
        await _replace(
            interaction,
            f"⚔️ **{_md(self.doer.name)}**: “{_md(self.words)}”\n"
            f"How did **{_md(self.doer.name)}** treat "
            + ", ".join(f"**{_md(e.name)}**" for e in people)
            + "?",
            HowMenu(self.campaign_id, self.doer, self.words, people),
        )


class HowMenu(_Menu):
    """Helped, pleased, annoyed or hurt: one tap."""

    def __init__(self, campaign_id: str, doer: Entity, words: str, people: list[Entity]) -> None:
        super().__init__()
        self.campaign_id, self.doer, self.words, self.people = campaign_id, doer, words, people
        for label, _ in standing.DEED_EFFECTS:
            self.add_item(_Button(partial(self._landed, label), label=label))

    async def _landed(self, effect: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if await _checked(interaction, self.campaign_id) is None:
            return
        self.stop()
        names = ", ".join(f"**{_md(e.name)}**" for e in self.people)
        await _replace(
            interaction,
            f"⚔️ **{_md(self.doer.name)}**: “{_md(self.words)}”\n"
            f"**{_md(self.doer.name)}** {standing.DEED_VERB[effect]} {names}.\nRecord this deed?",
            ConfirmMenu(self.campaign_id, self.doer, self.words, self.people, effect),
        )


class ConfirmMenu(_Menu):
    """The one confirmation."""

    def __init__(
        self, campaign_id: str, doer: Entity, words: str, people: list[Entity], effect: str
    ) -> None:
        super().__init__()
        self.campaign_id, self.doer, self.words = campaign_id, doer, words
        self.people, self.effect = people, effect
        self.add_item(_Button(self._yes, label="✅ Record it", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._no, label="No, cancel"))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        table = getattr(_bot(interaction), "tables", {}).get(campaign.guild_id)
        if getattr(table, "campaign_id", None) != campaign.id:
            table = None  # the server's running table may be another campaign's
        try:
            written = await memory.add_deed(
                campaign.guild_id,
                campaign.id,
                self.doer.id,
                self.words,
                [e.id for e in self.people],
                self.effect,
                source=DM,
                from_session_at=getattr(table, "started_at", None),
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"⚠️ {exc}", None)
            return
        changed(interaction, campaign)
        log.info("Deed recorded: %d affected", len(self.people))  # a count, never the words
        self.stop()
        await _replace(
            interaction,
            f"✅ Recorded: **{_md(self.doer.name)}** {standing.DEED_VERB[self.effect]} "
            + ", ".join(f"**{_md(e.name)}**" for e in self.people)
            + f". {standing.REMINDER}",
            DoneMenu(self.campaign_id, self.doer.id, written.batch),
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _replace(interaction, "Not recorded.", None)


class DoneMenu(_Menu):
    """After it is recorded: Undo."""

    def __init__(self, campaign_id: str, doer_id: str, batch: int | None) -> None:
        super().__init__()
        self.campaign_id, self.batch = campaign_id, batch
        if batch is not None:
            self.add_item(_Button(self._undo, label="↩ Undo that"))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None or self.batch is None:
            return
        campaign, memory = found
        text = UNDONE
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
            changed(interaction, campaign)
        except TooLateToUndo:
            text = "Too late to undo. Open Standing to see it."
        except MemoryRuleError:
            text = "Not undone, because something changed. Check Standing, then try again."
        self.stop()
        await _replace(interaction, text, None)
