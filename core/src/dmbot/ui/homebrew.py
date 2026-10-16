"""📚 Homebrew: a campaign's own spells, monsters, items and rules, from ⚙️ Settings or
`/dmbot homebrew` (#1520). A private menu for the campaign's DMs only; the store checks again that
the person is a DM on every change.

DMbot never writes or decides homebrew: it only keeps what the DM typed. An entry is the DM's own
creation, so the form says so in one line and points a published book to "Share a rulebook". Each
entry is **only for the DM** until the DM presses 👀 (a homebrew monster is a spoiler).

Remove can be undone while the bot keeps running: the last few removed entries are held in memory
(`bot.homebrew_removed`, nothing stored), shown as ↩️ buttons on the list.
"""

from __future__ import annotations

import logging
from functools import partial

import discord

from dmbot.campaigns import Campaign
from dmbot.logs import set_log_context
from dmbot.rules.homebrew import (
    KINDS,
    NAME_MAX,
    SUMMARY_MAX,
    TEXT_MAX,
    Homebrew,
    HomebrewError,
    HomebrewStore,
)
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
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
    dmbot_group,
)

log = logging.getLogger(__name__)

PAGE_ENTRIES = 25  # a menu holds 25 choices
UNDO_KEPT = 5  # removed entries that can be put back, per campaign
UNDO_SHOWN = 2  # ...and shown as buttons on the list

NOT_READY = "Homebrew isn't available right now. Please try again in a moment."
ONLY_DMS = "Only this campaign's DM can open its homebrew. Players can ask with `/dmbot rule`."
CAMPAIGN_GONE = "That campaign isn't here any more."
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
NOT_YOUR_CAMPAIGN = "You aren't a DM of a campaign in this server, so there's no homebrew to open."
ENTRY_GONE = "That entry was already removed. Here is the list now."
INTRO = (
    "Your own creations only. For a published book, use **Share a rulebook**. Homebrew beats "
    "the book. Only you see an entry until you press 👀; DMbot never makes one up."
)
NONE_YET = "No homebrew yet. Press **➕ Add** to write the first one."
KIND_ASK = "What kind of thing is it?"
FORM_NOTE = "Your own creations only. For a published book, use Share a rulebook."
CHANGED_MIND = "Changed your mind? Press ↩️ **Undo** on the list."

KIND_LABELS = {
    "spell": "✨ Spell",
    "monster": "👹 Monster",
    "item": "🗡 Item",
    "feat": "💪 Feat",
    "condition": "🌀 Condition",
    "rule": "📜 Rule",
    "other": "❔ Other",
}


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _fit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if (len(cut) - len(cut.rstrip("\\"))) % 2:  # a lone backslash would escape the "…"
        cut = cut[:-1]
    return cut + "…"


def _store(interaction: discord.Interaction) -> HomebrewStore | None:
    return _bot(interaction).homebrew


def mark(entry: Homebrew) -> str:
    """👀 players may know it, 🙈 only the DM."""
    return "👀" if entry.players_may_know else "🙈"


def list_text(campaign: Campaign, entries: list[Homebrew], page: int, note: str = "") -> str:
    lines = [_fit(note, 200)] if note else []
    lines.append(f"📚 **Homebrew: {_fit(_md(campaign.name), 100)}**")
    lines.append(INTRO)
    if not entries:
        lines.append(NONE_YET)
    shown = entries[page * PAGE_ENTRIES : (page + 1) * PAGE_ENTRIES]
    for entry in shown:
        lines.append(
            _fit(f"{mark(entry)} {KIND_LABELS[entry.kind]} **{_md(entry.name)}**: ", 400)
            + _fit(_md(entry.shown()), 120)
        )
    pages = max(1, -(-len(entries) // PAGE_ENTRIES))
    if pages > 1:
        lines.append(f"_Page {page + 1} of {pages}. 🙈 only you, 👀 players may know._")
    elif entries:
        lines.append("_🙈 only you, 👀 players may know._")
    return _fit("\n".join(lines), 1900)


def entry_text(entry: Homebrew, note: str = "") -> str:
    who = "players may know it 👀" if entry.players_may_know else "only you can see it 🙈"
    head = f"📚 **{_md(entry.name)}** ({entry.kind.capitalize()}, {who})"
    body = _fit(_md(entry.text), 1500)
    summary = f"\n_Shown in alerts as:_ {_md(entry.shown())}" if entry.summary else ""
    return _fit((f"{note}\n" if note else "") + f"{head}\n{body}{summary}", 1900)


async def _is_dm(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Checked on every press, from the campaign as it is now."""
    guild = interaction.guild
    if guild is None:
        return False
    current = await _bot(interaction).campaigns.get(guild.id, campaign.id)
    return current is not None and interaction.user.id in current.dm_user_ids


def _removed(interaction: discord.Interaction, campaign: Campaign) -> list[Homebrew]:
    return _bot(interaction).homebrew_removed.get((campaign.guild_id, campaign.id), [])


async def show_list(
    interaction: discord.Interaction,
    campaign: Campaign,
    *,
    page: int = 0,
    note: str = "",
    first: bool = False,
) -> None:
    """Draw the list: as a new private message (`first`), or in place of this one."""
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    entries = await store.list(campaign.guild_id, campaign.id)
    page = min(max(page, 0), max(0, (len(entries) - 1) // PAGE_ENTRIES))
    menu = ListMenu(campaign, entries, page, _removed(interaction, campaign))
    text = list_text(campaign, entries, page, note)
    if first:
        await _send(interaction, text, menu)
    else:
        await _show(interaction, text, menu)


async def _show(interaction: discord.Interaction, text: str, view: _Menu | None) -> None:
    try:
        await _replace(interaction, text, view)
    except discord.HTTPException:
        log.warning("Couldn't change a homebrew message; sent a new one", exc_info=True)
        await _send(interaction, text, view)


async def _redraw(
    interaction: discord.Interaction, campaign: Campaign, *, note: str = "", page: int = 0
) -> None:
    await _answer_first(interaction, in_place=True)
    current = await _bot(interaction).campaigns.get(campaign.guild_id, campaign.id)
    if current is None:
        await _tell(interaction, CAMPAIGN_GONE)
        return
    await show_list(interaction, current, page=page, note=note)


async def open_menu(interaction: discord.Interaction, campaign: Campaign) -> None:
    """From ⚙️ Settings or the command, for a DM (the caller checked; each press checks again)."""
    await _answer_first(interaction)
    await show_list(interaction, campaign, first=True)


class ListMenu(_Menu):
    """The list's buttons: Add, page turning, a menu to pick an entry, and ↩️ for the last
    entries removed."""

    def __init__(
        self, campaign: Campaign, entries: list[Homebrew], page: int, removed: list[Homebrew]
    ) -> None:
        super().__init__()
        self.campaign, self.entries, self.page = campaign, entries, page
        self.add_item(_Button(self._add, label="➕ Add", style=discord.ButtonStyle.primary))
        if page > 0:
            self.add_item(_Button(self._newer, label="◀ Back"))
        if (page + 1) * PAGE_ENTRIES < len(entries):
            self.add_item(_Button(self._older, label="More ▶"))
        for gone in removed[-UNDO_SHOWN:]:
            self.add_item(
                _Button(
                    partial(self._undo, gone),
                    label=logic.shorten(f"↩️ Undo: {gone.name}", logic.PHONE_LABEL_MAX + 15),
                )
            )
        shown = entries[page * PAGE_ENTRIES : (page + 1) * PAGE_ENTRIES]
        if shown:
            self.pick = _Select(
                self._picked,
                placeholder="Pick one to open…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(e.name, logic.PHONE_LABEL_MAX + 15),
                        value=str(e.id),
                        description=logic.shorten(
                            f"{mark(e)} {e.kind}: {e.shown()}", logic.DESCRIPTION_MAX
                        ),
                    )
                    for e in shown
                ],
            )
            self.add_item(self.pick)

    async def _add(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _show(interaction, KIND_ASK, KindPick(self.campaign, self.page))

    async def _newer(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page - 1)

    async def _older(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page + 1)

    async def _picked(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        wanted = int(self.pick.values[0])
        entry = next((e for e in self.entries if e.id == wanted), None)
        if entry is None:
            await _redraw(interaction, self.campaign, note=ENTRY_GONE, page=self.page)
            return
        await _show(interaction, entry_text(entry), EntryMenu(self.campaign, entry, self.page))

    async def _undo(self, gone: Homebrew, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        kept = _removed(interaction, self.campaign)
        if gone not in kept:  # put back already (another press, or another DM)
            await _redraw(interaction, self.campaign, page=self.page)
            return
        c = self.campaign
        try:
            await store.put_back(c.guild_id, c.id, interaction.user.id, gone)
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        kept.remove(gone)
        await _redraw(interaction, c, note=f"↩️ Put back **{_md(gone.name)}**.", page=self.page)


class KindPick(_Menu):
    """Add, step one: what kind of thing."""

    def __init__(self, campaign: Campaign, page: int) -> None:
        super().__init__()
        self.campaign, self.page = campaign, page
        self.pick = _Select(
            self._picked,
            placeholder="Pick a kind…",
            options=[discord.SelectOption(label=KIND_LABELS[k], value=k) for k in KINDS],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back to the list"))

    async def _picked(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await interaction.response.send_modal(AddForm(self.campaign, self.pick.values[0]))

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


class EntryMenu(_Menu):
    """One entry: Edit, who may know it, its kind, Remove, back."""

    def __init__(self, campaign: Campaign, entry: Homebrew, page: int) -> None:
        super().__init__()
        self.campaign, self.entry, self.page = campaign, entry, page
        self.add_item(_Button(self._edit, label="✏️ Edit"))
        self.add_item(
            _Button(
                self._toggle,
                label="🙈 Keep it to me" if entry.players_may_know else "👀 Let players know",
            )
        )
        self.add_item(_Button(self._remove, label="🗑 Remove", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._back, label="Back to the list"))
        self.kind = _Select(
            self._kind,
            placeholder=f"Kind: {entry.kind}. Change…",
            options=[
                discord.SelectOption(label=KIND_LABELS[k], value=k, default=k == entry.kind)
                for k in KINDS
            ],
            row=1,
        )
        self.add_item(self.kind)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if await _is_dm(interaction, self.campaign):
            return True
        await _tell(interaction, ONLY_DMS)
        return False

    async def _edit(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await interaction.response.send_modal(EditForm(self.campaign, self.entry, self.page))

    async def _change(self, interaction: discord.Interaction, **changes: object) -> None:
        """Save one change (everything else as it is), then show the entry again."""
        await _answer_first(interaction, in_place=True)
        store, c, e = _store(interaction), self.campaign, self.entry
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        values = {
            "kind": e.kind,
            "name": e.name,
            "text": e.text,
            "summary": e.summary,
            "players_may_know": e.players_may_know,
            **changes,
        }
        try:
            saved = await store.edit(
                c.guild_id,
                c.id,
                interaction.user.id,
                e.id,
                unchanged_since=e.version,
                **values,  # type: ignore[arg-type]
            )
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        await _show(
            interaction, entry_text(saved, "✅ Saved."), EntryMenu(self.campaign, saved, self.page)
        )

    async def _toggle(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await self._change(interaction, players_may_know=not self.entry.players_may_know)

    async def _kind(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await self._change(interaction, kind=self.kind.values[0])

    async def _remove(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await _show(
                interaction,
                f"Remove **{_md(self.entry.name)}**? You can press ↩️ **Undo** on the list "
                "afterwards.",
                ConfirmRemove(self.campaign, self.entry, self.page),
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


class ConfirmRemove(_Menu):
    def __init__(self, campaign: Campaign, entry: Homebrew, page: int) -> None:
        super().__init__()
        self.campaign, self.entry, self.page = campaign, entry, page
        self.add_item(_Button(self._yes, label="Yes, remove it", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label="Keep it"))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store, c = _store(interaction), self.campaign
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            gone = await store.remove(
                c.guild_id,
                c.id,
                interaction.user.id,
                self.entry.id,
                unchanged_since=self.entry.version,
            )
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        kept = _bot(interaction).homebrew_removed.setdefault((c.guild_id, c.id), [])
        kept.append(gone)
        del kept[:-UNDO_KEPT]
        await _redraw(
            interaction, c, note=f"🗑 Removed **{_md(gone.name)}**. {CHANGED_MIND}", page=self.page
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


class _EntryForm(discord.ui.Modal):
    """The boxes shared by Add and Edit."""

    name: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="Name", max_length=NAME_MAX, placeholder="For example: The Hollow King"
    )
    text: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="What it does",
        style=discord.TextStyle.paragraph,
        max_length=TEXT_MAX,
        placeholder=FORM_NOTE,
    )
    summary: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="One line for alerts (optional)",
        required=False,
        max_length=SUMMARY_MAX,
        placeholder="Shown when DMbot mentions it",
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign
        self.page = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)
        return True

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            note = await self.save(store, interaction.user.id)
        except HomebrewError as exc:
            # The form is closed by the time a refusal comes: give the words back to copy.
            typed = (
                f"\nYour words, to copy:\n```\n{self.text.value.replace('```', '` ``')[:1200]}\n```"
            )
            await _tell(interaction, f"{exc}{typed}")
            await _redraw(interaction, self.campaign, page=self.page)
            return
        await _redraw(interaction, self.campaign, note=note, page=self.page)

    async def save(self, store: HomebrewStore, user_id: int) -> str:
        raise NotImplementedError


class AddForm(_EntryForm, title="Add homebrew"):
    def __init__(self, campaign: Campaign, kind: str) -> None:
        super().__init__(campaign)
        self.kind = kind

    async def save(self, store: HomebrewStore, user_id: int) -> str:
        c = self.campaign
        saved = await store.add(
            c.guild_id,
            c.id,
            user_id,
            kind=self.kind,
            name=self.name.value,
            text=self.text.value,
            summary=self.summary.value,
        )
        return (
            f"➕ Added **{_md(saved.name)}**. Only you see it: open it and press 👀 to let "
            "players know."
        )


class EditForm(_EntryForm, title="Edit homebrew"):
    def __init__(self, campaign: Campaign, entry: Homebrew, page: int) -> None:
        super().__init__(campaign)
        self.entry, self.page = entry, page
        self.name.default = entry.name
        self.text.default = entry.text
        self.summary.default = entry.summary

    async def save(self, store: HomebrewStore, user_id: int) -> str:
        c, e = self.campaign, self.entry
        saved = await store.edit(
            c.guild_id,
            c.id,
            user_id,
            e.id,
            kind=e.kind,
            name=self.name.value,
            text=self.text.value,
            summary=self.summary.value,
            players_may_know=e.players_may_know,
            unchanged_since=e.version,
        )
        return f"✏️ Saved **{_md(saved.name)}**."


@dmbot_group.command(
    name="homebrew",
    description="Your campaign's own spells, monsters, items and rules (DMs only)",
)
async def dmbot_homebrew(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    await _answer_first(interaction)
    bot = _bot(interaction)
    everyone = await bot.campaigns.list_campaigns(guild.id)
    if not everyone:
        await _tell(interaction, NO_CAMPAIGNS)
        return
    mine = [c for c in everyone if interaction.user.id in c.dm_user_ids]
    if not mine:
        await _tell(interaction, NOT_YOUR_CAMPAIGN)
        return
    playing = bot.active_campaign_id(guild.id)
    current = next((c for c in mine if c.id == playing), None) or (
        mine[0] if len(mine) == 1 else None
    )
    if current is not None:
        await show_list(interaction, current, first=True)
        return
    await _send(interaction, "**Which campaign's homebrew?**", CampaignChoice(mine))


class CampaignChoice(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        self.campaigns = {c.id: c for c in campaigns}
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(label=logic.name_label(c.name), value=c.id)
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = self.campaigns.get(self.pick.values[0])
        if campaign is None:
            await _tell(interaction, CAMPAIGN_GONE)
            return
        self.stop()
        await _redraw(interaction, campaign)
