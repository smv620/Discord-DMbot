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

from dmbot.campaigns.models import STORY_OFF
from dmbot.discord_limits import MESSAGE_MAX, fit_message
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

ONLY_DM = links.ONLY_DM
NOT_ON = (
    "Not now. In Settings, turn on Shared story and say you have the right to use the material."
)
NOTHING_TO_MATCH = (
    "Nothing to match. No name in your adventure is also one DMbot remembers. Open a name's card "
    "and press **📖 In my adventure…** to link one yourself."
)
HEAD = (
    "🔗 **Match names**\n"
    "Is a name in your adventure the same character or place as one DMbot remembers from your "
    "game? You say; DMbot never guesses. Nothing changes in your game. Only you see this."
)
HOW = (
    "Top menu: these are the same. Bottom menu: these are not, and DMbot won't ask about them "
    "again (you can still link one by hand from its name card). Pairs you skip come back next time."
)
SAME_HOLDER = "Same character or place…"
NOT_HOLDER = "Not the same…"
UNDONE = "Undone."
LABEL_MAX = 100  # a menu option's label


SHOWN_LINKS = 5  # links listed (and with a Take off) on the picker
TEXT_MAX = MESSAGE_MAX - 10  # a little under a whole message, for the tail the sender adds
LABEL_SHOWN = 25  # a phone shows about this much of a menu option's label


def pair_label(n: int, pair: links.Pair) -> str:
    """ "1. Captain Varn": the number ties it to the message, whose line has both names."""
    return f"{n}. {pair.plan_name}"[:LABEL_SHOWN]


def pair_description(pair: links.Pair) -> str:
    """Both names, for the line under the option."""
    text = f"In your adventure: {pair.plan_name}. DMbot remembers: {pair.entity_name}"
    return text[:LABEL_MAX]


NAME_SHOWN = 40  # characters of a name on a line of the message: ten lines must fit one message


def _short(name: str) -> str:
    return _md(name if len(name) <= NAME_SHOWN else name[: NAME_SHOWN - 1] + "…")


def match_text(pairs: list[links.Pair], note: str = "") -> str:
    lines = [note] if note else []
    lines.append(HEAD)
    for n, pair in enumerate(pairs, 1):
        lines.append(
            f"{n}. In your adventure: **{_short(pair.plan_name)}** ↔ "
            f"DMbot remembers: **{_short(pair.entity_name)}**"
        )
    lines.append(HOW)
    return fit_message("\n".join(lines), limit=TEXT_MAX)


async def _dm(interaction: discord.Interaction, campaign_id: str) -> tuple[Any, Any] | None:
    """The campaign and its memory, if this person is one of its DMs; else they are told."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    # The switch may have been turned off since this screen was opened (CLAUDE.md: re-check).
    campaigns: Any = interaction.client.campaigns  # type: ignore[attr-defined]
    said = campaign.story_mode != STORY_OFF and await campaigns.story_confirmed(
        campaign.guild_id, campaign.id
    )
    if not said:
        await _tell(interaction, NOT_ON)
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
                discord.SelectOption(
                    label=pair_label(n, p), value=str(n - 1), description=pair_description(p)
                )
                for n, p in enumerate(pairs, 1)
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
        note = (
            f"✅ Linked {written.value} to your adventure."
            if status == links.SAME
            else f"✅ Saved {written.value} as not the same. DMbot won't ask about them again."
        )
        self.stop()
        await show_matches(interaction, self.campaign_id, note=note, undo=written.batch)

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
        return "Too late to undo that. Change it by hand on the name's card."
    except MemoryRuleError:
        return "Not undone: you changed it again after."
    return UNDONE


# ---- 📖 Same as in the book… (a name's card) ---------------------------------------------------


def pick_text(entity_name: str, current: list[links.Linked], total: int, note: str = "") -> str:
    lines = [note] if note else []
    lines.append(f"📖 **{_md(entity_name)}**: which entry in your adventure is this?")
    for n, link in enumerate(current[:SHOWN_LINKS], 1):
        where = f" ({_short(link.chapter)})" if link.chapter else ""
        lines.append(f"{n}. In your adventure: **{_short(link.name)}**{where}")
    if len(current) > SHOWN_LINKS:
        lines.append(f"…and {len(current) - SHOWN_LINKS} more.")
    if not total:
        lines.append(
            "Nothing in your adventure to link this to. Give DMbot your adventure on the Shared "
            "story card."
        )
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
                    placeholder="Pick from your adventure…",
                    options=[
                        discord.SelectOption(
                            label=o.name[:LABEL_SHOWN],
                            value=o.plan_id,
                            description=(f"{o.chapter} · {o.name}" if o.chapter else o.name)[
                                :LABEL_MAX
                            ],
                        )
                        for o in options
                    ],
                )
            )
        if offset + len(options) < total:
            self.add_item(_Button(self._more, label="More ▶", row=1))
        for n, link in enumerate(current[:SHOWN_LINKS], 1):  # (one for each file it is linked in)
            self.add_item(
                _Button(
                    partial(self._off, link.plan_id),
                    label="Take off" if len(current) == 1 else f"Take off {n}",
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
            note=f"✅ Linked to **{_md(name)}** in your adventure.",
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
