"""🔗 Look-alikes and 🔗 Merge several… (#2426): the easy way to make many spellings one name.

The names panel's **🔗 Look-alikes (N)** shows the groups DMbot found (`memory/lookalikes.py`,
no AI), one group per message: every name ticked, **Keep which name?** (the most heard first),
then **🔗 Merge** with one confirmation that names them all, or **Not the same** (remembered, so
the group is not offered again), or **Next group**. A name's card has **🔗 Merge several…**: tick
up to 25 other names for it. Both end in `MemoryStore.merge_many`: one change, one **Undo** for
all of it, nothing deleted.

The DM's alone (the same rule as the card's Same as…): the finder reads every confirmed name,
secret ones included, and the merge moves a secret name as a secret name.
"""

from __future__ import annotations

import logging

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.lookalikes import Name, by_use
from dmbot.memory.models import DM, MemoryRuleError, days
from dmbot.memory.store import MemoryStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _Button,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import (
    KIND_SHORT,
    NOT_AVAILABLE,
    _campaign_for,
    _md,
    _memory,
    changed,
    sees_secrets,
)

log = logging.getLogger(__name__)

PAGE = 25  # what one Discord menu holds
BUTTON = "🔗 Look-alikes"
MERGE_SEVERAL = "🔗 Merge several…"
NOT_THE_DM = "Only the DM can join names."
NONE_FOUND = "No look-alikes right now: no names that look or sound alike are waiting."
NOTHING_CHANGED = "Something broke on DMbot's side. Nothing was changed. Try once more."


def label(count: int) -> str:
    """The panel button: with how many groups wait, when it is known."""
    return f"{BUTTON} ({count if count < 100 else '99+'})" if count else BUTTON


def _kind_of(kinds: dict[str, str], name: Name) -> str:
    return kinds.get(name.id, "other")


def group_text(
    names: list[Name], kinds: dict[str, str], *, number: int, of: int, extra: int = 0
) -> str:
    head = "**These may be one name:**" + (f" ({number} of {of})" if of > 1 else "")
    lines = [
        f"• **{_md(n.name)}** ({_kind_of(kinds, n)}"
        + (f", heard {n.mentions} times" if n.mentions > 1 else "")
        + ")"
        for n in names
    ]
    more = [f"…and {extra} more not shown."] if extra else []
    return "\n".join(
        [
            head,
            *lines,
            *more,
            "Untick any that are a different name, pick the one to keep, then press 🔗 Merge. "
            "DMbot only suggests; you decide.",
        ]
    )


def _options(
    names: list[Name], kinds: dict[str, str], *, ticked: bool
) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=logic.shorten(n.name, logic.OPTION_LABEL_MAX),
            value=n.id,
            description=_kind_of(kinds, n) + (f" · heard {n.mentions}" if n.mentions else ""),
            default=ticked,
        )
        for n in names
    ]


def confirm_text(keep: Name, others: list[Name], kinds: dict[str, str]) -> str:
    """The one confirmation, naming every name it joins."""
    joined = ", ".join(f"**{_md(n.name)}**" for n in others)
    differ = sorted({_kind_of(kinds, n) for n in [keep, *others]})
    kind_line = (
        f"\nThey are not all the same kind ({', '.join(differ)}); it keeps the kind of "
        f"**{_md(keep.name)}** ({_kind_of(kinds, keep)})."
        if len(differ) > 1
        else ""
    )
    return (
        f"Make {joined} and **{_md(keep.name)}** one name? The others become "
        f"**{_md(keep.name)}**'s other names, and their connections, notes and mentions go "
        f"together. Nothing is deleted, and you can undo it.{kind_line}"
    )


async def _kinds(interaction: discord.Interaction, campaign_id: str) -> dict[str, str] | None:
    from dmbot.ui.name_card import _names

    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return None
    names = await _names(interaction, campaign)
    if names is None:
        return None
    return {e.id: KIND_SHORT.get(e.kind_key, "other") for e in names.entities.values()}


async def count_groups(interaction: discord.Interaction, campaign_id: str) -> int:
    """How many groups wait, for the panel's button label (0 on any trouble: the button then
    shows without a number)."""
    memory = _memory(interaction)
    campaign = await _campaign_for(interaction, campaign_id)
    if memory is None or campaign is None:
        return 0
    try:
        return len(await memory.look_alikes(campaign.guild_id, campaign.id))
    except Exception:
        log.exception("Couldn't look for look-alike names")
        return 0


async def show_look_alikes(interaction: discord.Interaction, campaign_id: str) -> None:
    """The panel's 🔗 Look-alikes: the first group, or a line saying there is none."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, NOT_THE_DM)
        return
    kinds = await _kinds(interaction, campaign_id)
    if kinds is None:
        return
    try:
        groups = await memory.look_alikes(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't look for look-alike names")
        await _tell(interaction, NOTHING_CHANGED)
        return
    await _show_group(interaction, campaign_id, groups, 0, kinds)


async def _show_group(
    interaction: discord.Interaction,
    campaign_id: str,
    groups: list[list[Name]],
    index: int,
    kinds: dict[str, str],
    *,
    note: str = "",
) -> None:
    if index >= len(groups):
        await _replace(interaction, (note + "\n" if note else "") + NONE_FOUND, None)
        return
    view = LookAlikeGroup(campaign_id, groups, index, kinds)
    text = group_text(
        view.names,
        kinds,
        number=index + 1,
        of=len(groups),
        extra=len(groups[index]) - len(view.names),
    )
    await _replace(interaction, (note + "\n\n" if note else "") + text, view)


class LookAlikeGroup(_Menu):
    """One group: what is ticked, which name to keep, and the buttons."""

    def __init__(
        self, campaign_id: str, groups: list[list[Name]], index: int, kinds: dict[str, str]
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.groups = groups
        self.index = index
        self.kinds = kinds
        self.names = by_use(groups[index])[:PAGE]
        self.ticked = {n.id for n in self.names}
        self.keep: str | None = None
        self.members = _Select(
            self._ticked,
            placeholder="These are the same name…",
            options=_options(self.names, kinds, ticked=True),
            min_values=1,
            max_values=len(self.names),
            row=0,
        )
        self.add_item(self.members)
        self.chooser = _Select(
            self._kept,
            placeholder="Keep which name? ▾",
            options=_options(self.names, kinds, ticked=False),
            row=1,
        )
        self.add_item(self.chooser)
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._merge, label="🔗 Merge", style=discord.ButtonStyle.primary, row=2)
        )
        self.add_item(_Button(self._apart, label="Not the same", style=grey, row=2))
        if len(groups) > 1:
            self.add_item(_Button(self._next, label="Next group ▸", style=grey, row=2))
        self.add_item(_Button(self._done, label="Done", style=grey, row=2))

    async def _ticked(self, interaction: discord.Interaction) -> None:
        self.ticked = set(self.members.values)
        await interaction.response.defer()

    async def _kept(self, interaction: discord.Interaction) -> None:
        self.keep = self.chooser.values[0]
        await interaction.response.defer()

    def _chosen(self) -> list[Name]:
        return [n for n in self.names if n.id in self.ticked]

    async def _merge(self, interaction: discord.Interaction) -> None:
        chosen = self._chosen()
        if len(chosen) < 2:
            await _tell(interaction, "Tick at least two names to join.")
            return
        keep = next((n for n in chosen if n.id == self.keep), None)
        if keep is None:
            await _tell(interaction, "Pick the name to keep first, from the ticked ones.")
            return
        others = [n for n in chosen if n.id != keep.id]
        self.stop()
        await _replace(
            interaction,
            confirm_text(keep, others, self.kinds),
            ConfirmMerge(self.campaign_id, self.groups, self.index, self.kinds, keep, others),
        )

    async def _apart(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        chosen = self._chosen()
        if len(chosen) < 2:
            await _tell(interaction, "Tick at least two names that are not the same.")
            return
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, NOT_THE_DM)
            return
        try:
            await memory.not_the_same(
                campaign.guild_id,
                campaign.id,
                [n.id for n in chosen],
                user_id=interaction.user.id,
            )
        except MemoryRuleError:
            await _tell(interaction, "One of those names has changed. Open 🔗 Look-alikes again.")
            return
        self.stop()
        rest = await _reload(memory, campaign)
        await _show_group(
            interaction,
            self.campaign_id,
            rest,
            min(self.index, max(len(rest) - 1, 0)),
            self.kinds,
            note="👍 Noted: those won't be suggested together again.",
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _show_group(
            interaction,
            self.campaign_id,
            self.groups,
            (self.index + 1) % len(self.groups),
            self.kinds,
        )

    async def _done(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _replace(interaction, "Closed. Open the names panel again any time.", None)


async def _reload(memory: MemoryStore, campaign: Campaign) -> list[list[Name]]:
    """The groups as they stand now: after a merge or a "Not the same" some are gone and others
    may have changed, so ask the finder again."""
    try:
        return await memory.look_alikes(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't look for look-alike names")
        return []


class ConfirmMerge(_Menu):
    """The one confirmation of a merge from a look-alike group."""

    def __init__(
        self,
        campaign_id: str,
        groups: list[list[Name]],
        index: int,
        kinds: dict[str, str],
        keep: Name,
        others: list[Name],
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.groups = groups
        self.index = index
        self.kinds = kinds
        self.keep = keep
        self.others = others
        self.add_item(
            _Button(self._yes, label="🔗 Yes, join them", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="No, go back", style=discord.ButtonStyle.secondary))

    async def _no(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        self.stop()
        await _show_group(interaction, self.campaign_id, self.groups, self.index, self.kinds)

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, NOT_THE_DM)
            return
        self.stop()
        # Answer Discord at once (#351): a big merge can take longer than its 3 seconds, and
        # saying so in place also takes the buttons away so nothing is pressed twice.
        await _replace(interaction, f"🔗 Joining into **{_md(self.keep.name)}**…", None)
        try:
            written = await memory.merge_many(
                campaign.guild_id, campaign.id, self.keep.id, [n.id for n in self.others], source=DM
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't join them. {exc} Nothing was changed.", None)
            return
        except Exception:
            log.exception("Joining %s names into %s failed", len(self.others), self.keep.id)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        note = (
            f"🔗 Done: {len(self.others)} "
            f"{'name is' if len(self.others) == 1 else 'names are'} now other "
            f"{'name' if len(self.others) == 1 else 'names'} for **{_md(self.keep.name)}**."
        )
        rest = await _reload(memory, campaign)
        await _show_group(
            interaction,
            self.campaign_id,
            rest,
            min(self.index, max(len(rest) - 1, 0)),
            self.kinds,
            note=note,
        )
        await send_undo(
            interaction, campaign.id, self.others[0].id, written.batch, memory, self.keep.name
        )


async def send_undo(
    interaction: discord.Interaction,
    campaign_id: str,
    gone_id: str,
    batch: int | None,
    memory: MemoryStore,
    keep_name: str,
) -> None:
    """Its own message, so Undo outlives the menu (and a restart): one press takes the whole
    merge back, because it was one change."""
    from dmbot.ui.name_card import UndoButton

    if batch is None:
        return
    view = discord.ui.View(timeout=None)
    view.add_item(UndoButton(campaign_id, gone_id, batch))
    await interaction.followup.send(
        f"Wrong? Press **Undo** within {days(memory.keep_days)} to split them all from "
        f"**{_md(keep_name)}** again.",
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# ---- 🔗 Merge several… (a name's card) ----------------------------------------------------


async def show_merge_several(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, *, typed: str = ""
) -> None:
    """Pick up to 25 other names to join into this one. Past 25 names, `typed` narrows the list
    (the same search as Same as…)."""
    from dmbot.ui.name_card import _current, _names
    from dmbot.ui.name_dropdown import candidates

    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return
    campaign, name = found
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, NOT_THE_DM)
        return
    names = await _names(interaction, campaign)
    if names is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    pool = sorted(candidates(names, entity_id), key=lambda c: c[1].casefold())
    wanted = typed.strip().casefold()
    if wanted:
        pool = [c for c in pool if wanted in c[1].casefold()]
    shown = pool[:PAGE]
    if not shown:
        await _replace(
            interaction,
            f"No other name like **{_md(typed)}**. Search again." if typed else NOT_AVAILABLE,
            MergeSeveral(campaign_id, entity_id, name, []),
        )
        return
    more = (
        f" Showing the first {PAGE} of {len(pool)}: press 🔍 Search to narrow them."
        if len(pool) > PAGE
        else ""
    )
    await _replace(
        interaction,
        f"**Which names are the same as {_md(name)}?** Tick them all, then press 🔗 Join. "
        f"They become other names for **{_md(name)}**.{more}",
        MergeSeveral(campaign_id, entity_id, name, shown),
    )


class SearchForm(discord.ui.Modal, title="Find the other names"):
    typed: discord.ui.TextInput[SearchForm] = discord.ui.TextInput(
        label="Part of a name", placeholder="Type part of a name", max_length=100
    )

    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_merge_several(
            interaction, self.campaign_id, self.entity_id, typed=self.typed.value
        )


class MergeSeveral(_Menu):
    def __init__(
        self, campaign_id: str, entity_id: str, name: str, options: list[tuple[str, str]]
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.options = dict(options)
        self.ticked: set[str] = set()
        if options:
            self.pick = _Select(
                self._ticked,
                placeholder="Pick the names that are the same…",
                options=[
                    discord.SelectOption(label=logic.shorten(n, logic.OPTION_LABEL_MAX), value=i)
                    for i, n in options
                ],
                min_values=1,
                max_values=len(options),
            )
            self.add_item(self.pick)
            self.add_item(_Button(self._join, label="🔗 Join", style=discord.ButtonStyle.primary))
        grey = discord.ButtonStyle.secondary
        self.add_item(_Button(self._search, label="🔍 Search", style=grey))
        self.add_item(_Button(self._back, label="Back", style=grey))

    async def _ticked(self, interaction: discord.Interaction) -> None:
        self.ticked = set(self.pick.values)
        await interaction.response.defer()

    async def _search(self, interaction: discord.Interaction) -> None:
        self.origin = None  # the form's answer replaces this message; cancel keeps it
        await interaction.response.send_modal(SearchForm(self.campaign_id, self.entity_id))

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)

    async def _join(self, interaction: discord.Interaction) -> None:
        if not self.ticked:
            await _tell(interaction, "Tick at least one name first.")
            return
        others = [Name(i, self.options[i]) for i in self.options if i in self.ticked]
        keep = Name(self.entity_id, self.name)
        kinds = await _kinds(interaction, self.campaign_id) or {}
        self.stop()
        await _replace(
            interaction,
            confirm_text(keep, others, kinds),
            ConfirmCard(self.campaign_id, keep, others),
        )


class ConfirmCard(_Menu):
    """The confirmation of Merge several… on a card: the card's name is the one kept."""

    def __init__(self, campaign_id: str, keep: Name, others: list[Name]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.keep = keep
        self.others = others
        self.add_item(
            _Button(self._yes, label="🔗 Yes, join them", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="No, go back", style=discord.ButtonStyle.secondary))

    async def _no(self, interaction: discord.Interaction) -> None:
        await show_merge_several(interaction, self.campaign_id, self.keep.id)

    async def _yes(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, NOT_THE_DM)
            return
        self.stop()
        await _replace(interaction, f"🔗 Joining into **{_md(self.keep.name)}**…", None)
        try:
            written = await memory.merge_many(
                campaign.guild_id, campaign.id, self.keep.id, [n.id for n in self.others], source=DM
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't join them. {exc} Nothing was changed.", None)
            return
        except Exception:
            log.exception("Joining %s names into %s failed", len(self.others), self.keep.id)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        count = len(self.others)
        note = (
            f"🔗 Done: {count} {'name is' if count == 1 else 'names are'} now other "
            f"{'name' if count == 1 else 'names'} for **{_md(self.keep.name)}**."
        )
        await show_card(interaction, campaign.id, self.keep.id, replace=True, note=note)
        await send_undo(
            interaction, campaign.id, self.others[0].id, written.batch, memory, self.keep.name
        )
