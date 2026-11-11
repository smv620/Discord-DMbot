"""👣 Met but not named (#4045; #3172 slice 5): the beings the party met in play that nobody named
(two ambushers, the sled dogs), kept apart from the names until the DM acts. DM-only. The DM gives
one a name (a new entry), says it is one the campaign already has (Same as…), or removes it.
Nothing here guesses a name: the DM types it.

Every press is checked again: the person must still be a DM of the campaign, and who is recorded
is read again (a row citing someone who stopped is deleted, never named). Naming and joining are
one write each in the store (`name_met`, `join_met`): the entry, its rank 0 and the row's removal
happen together or not at all. What counts as a name already taken is the store's one rule
(`met_name_owner`), so this list never sends the DM between two answers that both say no.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import discord

from dmbot.campaigns.models import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory.met_store import MetRow, MetStore
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.store import MET_GONE, MET_NAME_TAKEN, MET_NAME_WAITING
from dmbot.ui import logic, split_words
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
    "Creatures and people your party met in play that nobody named. Only you see this list. "
    "Pick one to name it, match it to a name you have, or remove it. DMbot never names them: "
    "you do."
)
EMPTY = (
    "👣 Nobody is waiting for a name. After each session, DMbot adds here anyone your party met "
    "that nobody named."
)
DM_ONLY = "Only a DM of this campaign can see this list."
NOTHING_CHANGED = "Something went wrong, so nothing was changed. Try again in a moment."
SHOWN_MAX = 25  # one menu's choices
MESSAGE_LIMIT = MESSAGE_MAX  # (the one message limit, #1835)
NAME_LABEL = "Their name"
NAME_HELP = "What you call them"
GROUP_HELP = "Their name, or the group's name"
SAME_LABEL = "The name they already have"
SAME_HELP = "A person or group in your names"
SHOWN_ONCE_NAMED = "Once named, they show in your names like any name you add."


def count_text(count: int) -> str:
    """The Names home's button label. Pure."""
    return f"👣 Met but not named ({count if count < 100 else '99+'})"


def notice(made: int) -> str:
    """The after-session line in the DM Bot channel (counts only: anyone in the campaign may read
    that channel, and the list's button checks the DM on every press). Pure."""
    if made == 1:
        return (
            "👣 1 creature or person your party met has no name yet. Name it, match it to a name "
            "you have, or remove it."
        )
    return (
        f"👣 {made} creatures or people your party met have no name yet. Name them, match them to "
        "a name you have, or remove them."
    )


def label(row: MetRow) -> str:
    """How one row reads: the DM's word for them and how many. Pure."""
    many = "" if row.count == 1 else (" (several)" if row.count == 0 else f" ({row.count})")
    return f"{row.what}{many}"


def when(row: MetRow) -> str:
    """When they were met, as a date. Pure."""
    if row.session_started_at is None:
        return "met in play"
    day = datetime.fromtimestamp(row.session_started_at, UTC)
    return f"met on {day.day} {day:%b %Y}"


def _store(interaction: discord.Interaction) -> MetStore | None:
    memory = _memory(interaction)
    return MetStore(memory._db) if memory is not None else None


async def _for_dm(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, when the one pressing is its DM now (checked on every press). Says why not,
    once, otherwise."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return None  # (`_campaign_for` already said why)
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, DM_ONLY)
        return None
    return campaign


async def show(
    interaction: discord.Interaction, campaign_id: str, note: str = "", *, in_place: bool = False
) -> None:
    """The list, newest session first, after the consent re-check."""
    await _answer_first(interaction, in_place=in_place)
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _for_dm(interaction, campaign_id)
    if campaign is None:
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
        await _replace(interaction, head + EMPTY, BackOnly(campaign.id))
        return
    shown = rows[:SHOWN_MAX]
    await _replace(interaction, list_text(head, shown, len(rows)), MetList(campaign.id, shown))


def list_text(head: str, shown: list[MetRow], total: int) -> str:
    """The list's message, one row a line, inside Discord's limit (#4106: `logic.shorten` joined
    every line into one paragraph). When every line does not fit, the newest that do are written
    and the rest are counted; the menu below still offers them all. Pure."""
    lines = [f"• **{_md(logic.shorten(label(r), 90))}** · {when(r)}" for r in shown]
    top = f"{head}**{TITLE}**\n{INTRO}\n\n"
    for written in range(len(lines), -1, -1):
        rest = total - written
        more = ""
        if rest and written < len(shown):
            more = f"\n…and {rest} more: pick them in the menu below."
        elif rest:
            more = f"\n…and {rest} more. Name or remove some to see the rest."
        text = top + "\n".join(lines[:written]) + more
        if len(text) <= MESSAGE_LIMIT:
            return text
    return fit_message(top, limit=MESSAGE_LIMIT)


async def _back_to_names(interaction: discord.Interaction, campaign_id: str) -> None:
    from dmbot.ui.names import show_home

    await show_home(interaction, campaign_id)


class BackOnly(_Menu):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(
            _Button(self._back, label="↩️ Back to Names", style=discord.ButtonStyle.secondary)
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _back_to_names(interaction, self.campaign_id)


class MetList(BackOnly):
    def __init__(self, campaign_id: str, rows: list[MetRow]) -> None:
        super().__init__(campaign_id)
        self.rows = {r.id: r for r in rows}
        self.pick = _Select(
            self._picked,
            placeholder="Pick one to name, match or remove",
            options=[
                discord.SelectOption(
                    label=logic.shorten(label(r), logic.OPTION_LABEL_MAX),
                    value=r.id,
                    description=logic.shorten(when(r), 100),
                )
                for r in rows[:SHOWN_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        row = self.rows.get(str(self.pick.values[0])) if self.pick.values else None
        if row is None:
            await show(interaction, self.campaign_id, in_place=True)
            return
        await _answer_first(interaction, in_place=True)
        await _replace(interaction, choice_text(row), MetActions(self.campaign_id, row))


def choice_text(row: MetRow) -> str:
    """What the DM reads after picking one: each button said in words. Pure."""
    return (
        f"**{_md(label(row))}**, {when(row)}.\n"
        "✏️ **Give them a name**: add them as a new name.\n"
        "🔗 **Same as…**: they're someone already in your names.\n"
        "🗑️ **Remove**: take them off this list.\n"
        f"{SHOWN_ONCE_NAMED}"
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
        if store is None:
            await _tell(interaction, NOT_AVAILABLE)
            return
        campaign = await _for_dm(interaction, self.campaign_id)
        if campaign is None:
            return
        try:
            gone = await store.remove(campaign.guild_id, campaign.id, self.row.id)
        except Exception:
            log.exception("Removing a met-but-not-named row failed")
            await _tell(interaction, NOTHING_CHANGED)
            return
        note = f"🗑️ Removed **{_md(label(self.row))}**." if gone else "That one was already gone."
        await show(interaction, self.campaign_id, note, in_place=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show(interaction, self.campaign_id, in_place=True)


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
        elif row.count != 1:
            self.name.placeholder = GROUP_HELP
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
    """A new name makes the entry; a name a person or group already has asks first whether they
    are the same one; one still waiting in the name check says to check it there first. `same`:
    the DM said it is one the campaign already has."""
    await _answer_first(interaction, in_place=True)
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _for_dm(interaction, campaign_id)
    if campaign is None:
        return
    if not name:
        await _tell(interaction, "Type a name for them.")
        return
    try:
        owners = await memory.met_name_owners(campaign.guild_id, campaign.id, name)
    except Exception:
        log.exception("Couldn't look the name up")
        await _tell(interaction, NOTHING_CHANGED)
        return
    if any(not o.confirmed for o in owners):
        await _tell(interaction, MET_NAME_WAITING.replace("That name", f"**{_md(name)}**"))
        return
    joinable = [o for o in owners if o.joinable]
    if owners and not joinable:
        await _tell(
            interaction,
            f"**{_md(name)}** is already the name of a place or thing in your campaign. Give them "
            "a different name.",
        )
        return
    if len(joinable) > 1:
        await _tell(
            interaction,
            f"**{_md(name)}** matches more than one name in your campaign. Press 🔗 Same as… again "
            "and type the full name of the one you mean.",
        )
        return
    if joinable:
        (owner,) = joinable
        if same:
            await _join(interaction, campaign, row, owner.id, owner.name)
            return
        await _replace(
            interaction,
            f"**{_md(owner.name)}** is already a name in your campaign. Is **{_md(label(row))}** "
            "the same one?",
            JoinAsk(campaign_id, row, owner.id, owner.name),
        )
        return
    if same:
        await _tell(
            interaction,
            f"DMbot found no person or group called **{_md(name)}** in your campaign. Check the "
            "spelling and press 🔗 Same as… again, or press ✏️ Give them a name to add it as new.",
        )
        return
    try:
        agreed = await _bot(interaction).consent.consenting(campaign.guild_id)
        written = await memory.name_met(
            campaign.guild_id, campaign.id, row.id, name, opted_in=agreed
        )
    except MemoryRuleError as exc:
        await _refused(interaction, campaign_id, str(exc), "Couldn't name them.")
        return
    except Exception:
        log.exception("Naming a met-but-not-named row failed")
        await _tell(interaction, NOTHING_CHANGED)
        return
    changed(interaction, campaign)
    many = row.count != 1
    done = (
        f"✏️ **{_md(label(row))}** are now **{_md(written.value.name)}**, a group in your names."
        if many
        else f"✏️ **{_md(label(row))}** is now **{_md(written.value.name)}**, in your names."
    )
    if hidden := split_words.for_one(written.split):  # (met while the party was split, #4086)
        done = f"{done}\n{hidden}"
    await show(interaction, campaign_id, done, in_place=True)


async def _refused(interaction: discord.Interaction, campaign_id: str, why: str, lead: str) -> None:
    """A refusal from the store: a gone row shows the list again (the buttons there are stale);
    the name rules' words are said as they are."""
    if why == MET_GONE:
        await show(interaction, campaign_id, why, in_place=True)
        return
    if why in (MET_NAME_TAKEN, MET_NAME_WAITING):
        await _tell(interaction, why)
        return
    await _tell(interaction, f"{lead} {why}")


async def _join(
    interaction: discord.Interaction, campaign: Campaign, row: MetRow, entity_id: str, name: str
) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    try:
        agreed = await _bot(interaction).consent.consenting(campaign.guild_id)
        await memory.join_met(campaign.guild_id, campaign.id, row.id, entity_id, opted_in=agreed)
    except MemoryRuleError as exc:
        await _refused(interaction, campaign.id, str(exc), "Couldn't match them to that name.")
        return
    except Exception:
        log.exception("Matching a met-but-not-named row failed")
        await _tell(interaction, NOTHING_CHANGED)
        return
    changed(interaction, campaign)
    await show(
        interaction, campaign.id, f"🔗 **{_md(label(row))}** is **{_md(name)}**.", in_place=True
    )


class JoinAsk(_Menu):
    """The DM typed a name a person or group already has: the same one, or someone else?"""

    def __init__(self, campaign_id: str, row: MetRow, entity_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.row = row
        self.entity_id = entity_id
        self.name = name
        self.add_item(
            _Button(self._yes, label="Yes, the same one", style=discord.ButtonStyle.primary)
        )
        self.add_item(
            _Button(self._no, label="No, someone else", style=discord.ButtonStyle.secondary)
        )

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        campaign = await _for_dm(interaction, self.campaign_id)  # (checked again on this press)
        if campaign is None:
            return
        await _join(interaction, campaign, self.row, self.entity_id, self.name)

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _answer_first(interaction, in_place=True)
        await _replace(
            interaction,
            f"Then give **{_md(label(self.row))}** a different name, so the two aren't mixed up.",
            MetActions(self.campaign_id, self.row),
        )


class MetListButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:met:list:(?P<campaign>[0-9a-f]{32})",
):
    """On the after-session notice in the DM Bot channel: opens the list privately for a DM of
    the campaign. The channel is the campaign's people's, so it is checked on every press (as
    `names.ReviewButton`). Works after a restart (the campaign is in the button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="👣 Open the list",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:met:list:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MetListButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await show(interaction, self.campaign_id)  # (the DM check is the list's own, every press)


def notice_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(MetListButton(campaign_id))
    return view
