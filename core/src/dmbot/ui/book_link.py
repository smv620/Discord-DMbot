"""The book and the table, on the DM's screens (#1715; docs/STORY_MEMORY.md, section 6): who in
the adventure is who at the table. Two ways in, both for the campaign's DMs only and asked again
on every press:

- **🔗 Match names** on the Shared story card: up to ten pairs DMbot found by name (no AI), each
  answered **the same** or **not the same**, and the answers are remembered.
- **📖 Same as in the book…** on a person's, group's or place's name card: pick the book's entry by
  hand, or take a link off.

Every change can be undone with the button that follows it. DMbot never decides: a name that
matches is a question, not a fact. Nothing here goes to players or into a transcript.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import discord

from dmbot.memory import book_links as links
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _Button,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

BUTTON = "📖 Same as in the book…"  # on a name card
MATCH_LABEL = "Match names"  # on the Shared story card
ONLY_DM = links.ONLY_DM
NOTHING_TO_MATCH = (
    "Nothing to match right now. DMbot looks for names in your adventure that are also names at "
    "your table. Use **Same as in the book…** on a name's card to pick one yourself."
)
HEAD = (
    "🔗 **Is the book's name the same as the table's?**\n"
    "DMbot found these by name. Only you can say, and only you see this."
)
SAME_HOLDER = "These are the same…"
NOT_HOLDER = "These are not the same…"
UNDONE = "Undone."
LABEL_MAX = 100  # a menu option's label


def pair_label(pair: links.Pair) -> str:
    """ "Book: Captain Varn ↔ Table: Captain Varn", cut to what a menu option holds."""
    text = f"Book: {pair.plan_name} ↔ Table: {pair.entity_name}"
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def match_text(pairs: list[links.Pair], note: str = "") -> str:
    lines = [note] if note else []
    lines.append(HEAD)
    for n, pair in enumerate(pairs, 1):
        lines.append(f"{n}. Book: **{_md(pair.plan_name)}** ↔ Table: **{_md(pair.entity_name)}**")
    return "\n".join(lines)


async def _dm(interaction: discord.Interaction, campaign_id: str) -> tuple[Any, Any] | None:
    """The campaign and its memory, if this person is one of its DMs; else they are told."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    return campaign, memory


async def _back_to_card(interaction: discord.Interaction, campaign_id: str, note: str = "") -> None:
    """The Shared story adventure card, in place (it lives with the adventure's buttons)."""
    from dmbot.dm_screen.adventure_card import _card

    found = await _dm(interaction, campaign_id)
    if found is None:
        return
    campaign = found[0]
    text, view = await _card(interaction, campaign)
    await interaction.edit_original_response(
        content=f"{note}\n\n{text}" if note else text, view=view
    )


# ---- 🔗 Match names (the Shared story card) -------------------------------------------------


async def show_matches(
    interaction: discord.Interaction, campaign_id: str, *, note: str = "", undo: int | None = None
) -> None:
    await _answer_first(interaction, in_place=True)
    found = await _dm(interaction, campaign_id)
    if found is None:
        return
    campaign, memory = found
    pairs = await links.suggestions(memory, campaign.guild_id, campaign.id)
    if not pairs:
        text = f"{note}\n\n{NOTHING_TO_MATCH}" if note else NOTHING_TO_MATCH
        await _replace(interaction, text, MatchMenu(campaign.id, [], undo))
        return
    await _replace(interaction, match_text(pairs, note), MatchMenu(campaign.id, pairs, undo))


class MatchMenu(_Menu):
    """Two menus over the same pairs, one for the ones that are the same and one for the ones
    that are not, then Done. Picking in a menu saves at once and shows the next ten."""

    def __init__(self, campaign_id: str, pairs: list[links.Pair], undo: int | None) -> None:
        super().__init__()
        self.campaign_id, self.pairs, self.undo = campaign_id, pairs, undo
        if pairs:
            options = [
                discord.SelectOption(label=pair_label(p), value=str(n)) for n, p in enumerate(pairs)
            ]
            self.same = _Select(
                partial(self._pick, links.SAME),
                placeholder=SAME_HOLDER,
                options=options,
                min_values=1,
                max_values=len(options),
            )
            self.not_same = _Select(
                partial(self._pick, links.NOT_SAME),
                placeholder=NOT_HOLDER,
                options=options,
                min_values=1,
                max_values=len(options),
            )
            self.add_item(self.same)
            self.add_item(self.not_same)
        if undo is not None:
            self.add_item(_Button(self._undo, label="↩ Undo that", row=2))
        self.add_item(_Button(self._done, label="Done", row=2))

    async def _pick(self, status: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        chosen = [int(v) for v in (self.same if status == links.SAME else self.not_same).values]
        decisions = [(self.pairs[n].plan_id, self.pairs[n].entity_id, status) for n in chosen]
        try:
            written = await links.decide(
                memory, campaign.guild_id, campaign.id, decisions, interaction.user.id
            )
        except MemoryRuleError as exc:
            await show_matches(interaction, self.campaign_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        log.info("Book names answered: %d", written.value)  # a count only, never a name
        word = "the same" if status == links.SAME else "not the same"
        self.stop()
        await show_matches(
            interaction,
            self.campaign_id,
            note=f"✅ Saved {written.value} as {word}.",
            undo=written.batch,
        )

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _dm(interaction, self.campaign_id)
        if found is None or self.undo is None:
            return
        campaign, memory = found
        note = await _undo(memory, campaign, self.undo)
        changed(interaction, campaign)
        self.stop()
        await show_matches(interaction, self.campaign_id, note=note)

    async def _done(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _back_to_card(interaction, self.campaign_id)


async def _undo(memory: Any, campaign: Any, batch: int) -> str:
    try:
        await memory.undo(campaign.guild_id, campaign.id, batch)
    except TooLateToUndo:
        return "Too late to undo that."
    except MemoryRuleError:
        return "Not undone: it was changed again since."
    return UNDONE


# ---- 📖 Same as in the book… (a name's card) ---------------------------------------------------


def pick_text(entity_name: str, current: list[links.Linked], total: int, note: str = "") -> str:
    lines = [note] if note else []
    lines.append(f"📖 **{_md(entity_name)}**: which entry in your adventure is this?")
    for link in current:
        where = f" ({_md(link.chapter)})" if link.chapter else ""
        lines.append(f"Same as in the book: **{_md(link.name)}**{where}")
    if not total:
        lines.append("Your adventure has no other person, group or place of this kind to pick.")
    return "\n".join(lines)


async def show_picker(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    *,
    offset: int = 0,
    note: str = "",
    undo: int | None = None,
) -> None:
    await _answer_first(interaction, in_place=True)
    found = await _dm(interaction, campaign_id)
    if found is None:
        return
    campaign, memory = found
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None:
        await _replace(interaction, GONE, None)
        return
    current = await links.linked(memory, campaign.guild_id, campaign.id, entity_id)
    options, total = await links.options_for(
        memory, campaign.guild_id, campaign.id, entity_id, offset
    )
    await _replace(
        interaction,
        pick_text(entity.name, current, total, note),
        PickMenu(campaign.id, entity_id, options, current, offset, total, undo),
    )


class PickMenu(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        options: list[links.Option],
        current: list[links.Linked],
        offset: int,
        total: int,
        undo: int | None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.offset, self.undo = (
            campaign_id,
            entity_id,
            offset,
            undo,
        )
        self.options = {o.plan_id: o for o in options}
        if options:
            self.add_item(
                _Select(
                    self._picked,
                    placeholder="Pick the book's entry…",
                    options=[
                        discord.SelectOption(
                            label=o.name[:100],
                            value=o.plan_id,
                            description=(o.chapter[:100] if o.chapter else None),
                        )
                        for o in options
                    ],
                )
            )
        if offset + len(options) < total:
            self.add_item(_Button(self._more, label="More ▶", row=1))
        for link in current[:3]:  # (one for each file it is linked in; rarely more than one)
            self.add_item(
                _Button(
                    partial(self._off, link.plan_id),
                    label=f"Take off: {link.name}"[:80],
                    row=2,
                )
            )
        if undo is not None:
            self.add_item(_Button(self._undo, label="↩ Undo that", row=3))
        self.add_item(_Button(self._back, label="↩ Back", row=3))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        select = next(i for i in self.children if isinstance(i, discord.ui.Select))
        plan_id = select.values[0]
        try:
            written = await links.decide(
                memory,
                campaign.guild_id,
                campaign.id,
                [(plan_id, self.entity_id, links.SAME)],
                interaction.user.id,
            )
        except MemoryRuleError as exc:
            await show_picker(interaction, self.campaign_id, self.entity_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        log.info("Book name picked by hand")  # no name
        self.stop()
        name = self.options[plan_id].name if plan_id in self.options else "that"
        await show_picker(
            interaction,
            self.campaign_id,
            self.entity_id,
            note=f"✅ Saved: the same as **{_md(name)}** in the book.",
            undo=written.batch,
        )

    async def _off(self, plan_id: str, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        written = await links.unlink(
            memory, campaign.guild_id, campaign.id, plan_id, self.entity_id
        )
        changed(interaction, campaign)
        self.stop()
        await show_picker(
            interaction,
            self.campaign_id,
            self.entity_id,
            note="✅ Taken off." if written.value else "That was already taken off.",
            undo=written.batch,
        )

    async def _more(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_picker(
            interaction, self.campaign_id, self.entity_id, offset=self.offset + links.PICK_MAX
        )

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _dm(interaction, self.campaign_id)
        if found is None or self.undo is None:
            return
        campaign, memory = found
        note = await _undo(memory, campaign, self.undo)
        changed(interaction, campaign)
        self.stop()
        await show_picker(interaction, self.campaign_id, self.entity_id, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)
