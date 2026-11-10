"""👣 Met but not named (#4045; #3172 slice 5): the beings the party met at the table that nobody
named (two ambushers, the sled dogs), kept apart from the names until the DM acts. DM-only. The DM
gives one a name (a new entry), says it is one the campaign already has (Same as…), or removes it.
Nothing here guesses a name: the DM types it.

Rows citing someone who is no longer recorded are left out (and deleted) before every read
(`MetStore.listed`). Naming and joining are one write each in the store (`name_met`, `join_met`):
the entry, its rank 0 and the row's removal happen together or not at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord

from dmbot.memory.met_store import MetRow, MetStore
from dmbot.memory.models import CONFIRMED, MemoryRuleError
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
from dmbot.ui.names import NOT_AVAILABLE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

TITLE = "👣 Met but not named"
INTRO = (
    "Creatures and people your party met at the table that nobody named. Only you see this list. "
    "Give one a name to add it to your names, or remove it."
)
EMPTY = "👣 Nobody is waiting for a name. DMbot adds here who your party meets in play."
DM_ONLY = "Only a DM of this campaign can see this list."
NOTHING_CHANGED = "Something went wrong and nothing was changed."
SHOWN_MAX = 25  # one menu's choices
NAME_LABEL = "Their name"
NAME_HELP = "What you call them"
SAME_LABEL = "The name they already have"
SAME_HELP = "A name in your campaign"


def count_text(count: int) -> str:
    """The Names home's button label. Pure."""
    return f"👣 Met but not named ({count if count < 100 else '99+'})"


def notice(made: int) -> str:
    """The after-session line (counts only): how many joined the list. Pure."""
    return (
        f"👣 Your party met {made} {'creature or person' if made == 1 else 'creatures or people'}"
        " nobody named. They wait in **Names → 👣 Met but not named** for you to name or remove."
    )


def label(row: MetRow) -> str:
    """How one row reads: the DM's word for them and how many. Pure."""
    many = "" if row.count == 1 else (" (several)" if row.count == 0 else f" ({row.count})")
    return f"{row.what}{many}"


def _when(row: MetRow) -> str:
    if row.session_started_at is None:
        return "met in play"
    day = datetime.fromtimestamp(row.session_started_at, UTC).strftime("%-d %b %Y")
    return f"met in the session of {day}"


def _store(interaction: discord.Interaction) -> MetStore | None:
    memory = _memory(interaction)
    return MetStore(memory._db) if memory is not None else None


async def show(interaction: discord.Interaction, campaign_id: str, note: str = "") -> None:
    """The list, newest session first, after the consent re-check."""
    await _answer_first(interaction)
    campaign = await _campaign_for(interaction, campaign_id)
    store = _store(interaction)
    if campaign is None:
        return
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, DM_ONLY)
        return
    try:
        agreed = await _bot(interaction).consent.consenting(campaign.guild_id)
        rows = await store.listed(campaign.guild_id, campaign.id, opted_in=agreed)
    except Exception:
        log.exception("Couldn't read the met-but-not-named list")
        await _tell(interaction, "DMbot couldn't read the list just now. Try again in a moment.")
        return
    head = f"{note}\n\n" if note else ""
    if not rows:
        await _replace(interaction, head + EMPTY, None)
        return
    shown = rows[:SHOWN_MAX]
    lines = [f"• **{_md(logic.shorten(label(r), 90))}** · {_when(r)}" for r in shown]
    more = f"\n…and {len(rows) - len(shown)} more." if len(rows) > len(shown) else ""
    text = f"{head}**{TITLE}**\n{INTRO}\n\n" + "\n".join(lines) + more
    await _replace(interaction, logic.shorten(text, 1990), MetList(campaign.id, shown))


class MetList(_Menu):
    def __init__(self, campaign_id: str, rows: list[MetRow]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.rows = {r.id: r for r in rows}
        self.pick = _Select(
            self._picked,
            placeholder="Pick one to name or remove",
            options=[
                discord.SelectOption(
                    label=logic.shorten(label(r), logic.OPTION_LABEL_MAX),
                    value=r.id,
                    description=logic.shorten(_when(r), 100),
                )
                for r in rows
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        row = self.rows.get(str(self.pick.values[0])) if self.pick.values else None
        if row is None:
            await show(interaction, self.campaign_id)
            return
        self.stop()
        await _answer_first(interaction, in_place=True)
        await _replace(
            interaction,
            f"**{_md(label(row))}**, {_when(row)}. What would you like to do?",
            MetActions(self.campaign_id, row),
        )


class MetActions(_Menu):
    def __init__(self, campaign_id: str, row: MetRow) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.row = row
        self.add_item(
            _Button(self._name, label="✏️ Give them a name", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._same, label="🔗 Same as…", style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._remove, label="🗑️ Remove", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._back, label="↩️ Back", style=discord.ButtonStyle.secondary))

    async def _name(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameForm(self.campaign_id, self.row, same=False))

    async def _same(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameForm(self.campaign_id, self.row, same=True))

    async def _remove(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        campaign = await _campaign_for(interaction, self.campaign_id)
        if store is None or campaign is None or not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, DM_ONLY)
            return
        try:
            gone = await store.remove(campaign.guild_id, campaign.id, self.row.id)
        except Exception:
            log.exception("Removing a met-but-not-named row failed")
            await _tell(interaction, NOTHING_CHANGED)
            return
        note = f"🗑️ Removed **{_md(label(self.row))}**." if gone else "That one was already gone."
        await show(interaction, self.campaign_id, note)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show(interaction, self.campaign_id)


class NameForm(discord.ui.Modal):
    """A new name for them (`same` False), or the name of the one they already are (`same`)."""

    name: discord.ui.TextInput[NameForm] = discord.ui.TextInput(
        label=NAME_LABEL, placeholder=NAME_HELP, min_length=1, max_length=100
    )

    def __init__(self, campaign_id: str, row: MetRow, *, same: bool) -> None:
        title = "Who are they?" if same else "Give them a name"
        super().__init__(title=title, timeout=VIEW_TIMEOUT_S)
        if same:
            self.name.label = SAME_LABEL
            self.name.placeholder = SAME_HELP
        self.campaign_id = campaign_id
        self.row = row
        self.same = same

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await name_them(
            interaction, self.campaign_id, self.row, " ".join(self.name.value.split()), self.same
        )


async def name_them(
    interaction: discord.Interaction, campaign_id: str, row: MetRow, name: str, same: bool
) -> None:
    """A name the campaign has already joins them to it (asked first when typed as a new name); a
    new name makes the entry. `same`: the DM said it is one the campaign already has."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    cache = _bot(interaction).lookup
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, DM_ONLY)
        return
    if not name:
        await _tell(interaction, "Type a name for them.")
        return
    if cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    found = await cache.get(campaign.guild_id, campaign.id)
    known = {
        e.entity_id: other.name
        for e in found.exact(name)
        if not e.person
        and (other := found.entities.get(e.entity_id)) is not None
        and other.status == CONFIRMED
        and not other.unnamed
    }
    if len(known) > 1:
        await _tell(
            interaction,
            f"More than one name is **{_md(name)}**. Type the full name of the one they are.",
        )
        return
    if known:
        ((entity_id, known_name),) = known.items()
        if same:
            await _join(interaction, campaign_id, row, entity_id, known_name)
            return
        await _replace(
            interaction,
            f"**{_md(known_name)}** is already a name in your campaign. Is **{_md(label(row))}** "
            "the same one?",
            JoinAsk(campaign_id, row, entity_id, known_name),
        )
        return
    if same:
        await _tell(
            interaction,
            f"No name in your campaign is **{_md(name)}**. To add it as a new name, use "
            "✏️ Give them a name.",
        )
        return
    try:
        written = await memory.name_met(campaign.guild_id, campaign.id, row.id, name)
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't name them. {exc}")
        return
    except Exception:
        log.exception("Naming a met-but-not-named row failed")
        await _tell(interaction, NOTHING_CHANGED)
        return
    changed(interaction, campaign)
    await show(
        interaction,
        campaign_id,
        f"✏️ **{_md(label(row))}** is now **{_md(written.value.name)}**, in your names.",
    )


async def _join(
    interaction: discord.Interaction, campaign_id: str, row: MetRow, entity_id: str, name: str
) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    try:
        await memory.join_met(campaign.guild_id, campaign.id, row.id, entity_id)
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't join them. {exc}")
        return
    except Exception:
        log.exception("Joining a met-but-not-named row failed")
        await _tell(interaction, NOTHING_CHANGED)
        return
    changed(interaction, campaign)
    await show(interaction, campaign_id, f"🔗 **{_md(label(row))}** is **{_md(name)}**.")


class JoinAsk(_Menu):
    """The DM typed a name the campaign already has: the same one, or a different name?"""

    def __init__(self, campaign_id: str, row: MetRow, entity_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.row = row
        self.entity_id = entity_id
        self.name = name
        self.add_item(
            _Button(self._yes, label="Yes, the same one", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="No, different", style=discord.ButtonStyle.secondary))

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        await _join(interaction, self.campaign_id, self.row, self.entity_id, self.name)

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        await _replace(
            interaction,
            f"Then give **{_md(label(self.row))}** a different name, so the two aren't mixed up.",
            MetActions(self.campaign_id, self.row),
        )
