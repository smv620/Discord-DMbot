"""📚 Homebrew: a campaign's own spells, monsters, items and other game content, from
⚙️ Manage DMbot or `/dmbot homebrew` (#1520). A private menu for the campaign's DMs only; the
store checks again that the person is a DM on every change.

DMbot never writes or decides homebrew: it only keeps what the DM typed. An entry is the DM's own
creation, so the form says so in one line and points a published book to "Share a rulebook". Each
entry is on or off for DMbot's use; nothing is hidden from players.

Remove can be undone while the bot keeps running: the last few removed entries are held in memory
(`bot.homebrew_removed`, nothing stored), shown as ↩️ buttons on the list.
"""

from __future__ import annotations

import io
import logging
from functools import partial
from typing import cast

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.campaigns.models import RULEBOOK, fingerprint
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.dm_screen import pack_suggestion
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS
from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import (
    KINDS,
    NAME_MAX,
    NO_CAMPAIGN,
    SUMMARY_MAX,
    TEXT_MAX,
    FileEntry,
    Homebrew,
    HomebrewError,
    HomebrewStore,
    credit_label,
    name_key,
)
from dmbot.rules.homebrew_import import ImportRefused, Preview, Refresh
from dmbot.rules.site_urls import normalise_domain, normalise_url
from dmbot.ui import homebrew_sources, logic
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

PACKS_LABEL = "📦 Homebrew packs"
PAGE_ENTRIES = 25  # a menu holds 25 choices
HOMEBREW_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds
UNDO_KEPT = 5  # removed entries that can be put back, per campaign
UNDO_SHOWN = 1  # ...and the newest is the one button on the list

WHICH_RULES = (
    "**Which rules is this book written for?**\n"
    "This only changes which answer DMbot shows first when the book and the free rules have "
    "the same name.\n"
    "2024 book: its answer comes first.\n"
    "2014 book: the newer 2024 rule comes first.\n"
    "Not sure, or an add-on book: it goes in the middle."
)
NOT_READY = "Homebrew isn't available right now. Please try again in a moment."
SUGGEST_LABEL = "💡 Suggest to the pack"
SEND_LABEL = "Yes, send it"
KEEP_PRIVATE_LABEL = "No, keep it here"
FROM_A_BOOK = "That entry came from a book you shared, so it stays in this campaign."
ONLY_DMS = "Only this campaign's DM can open its homebrew. Ask your DM."
CAMPAIGN_GONE = "That campaign isn't here any more."
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
NOT_YOUR_CAMPAIGN = (
    "Only a campaign's DM can open its homebrew. You aren't a DM of one in this server."
)
MINE_NOTE = "✏️ Saved. This is now your own, and it replaces the pack's version in this campaign."
ENTRY_GONE = "That entry is gone, maybe removed by another DM. Here is the list now."
INTRO = (
    "Your own spells, monsters, items and more. DMbot uses each entry that is on and skips the "
    "ones you turn off. For a published book, use **Share a rulebook**."
)
SHORT_INTRO = "Your own creations only. For a published book, use **Share a rulebook**."
NONE_YET = "No homebrew yet. Press **➕ Add** to write the first one, or **📥 Load file**."
KIND_ASK = "What are you adding? Pick one and a form opens."
CHANGED_MIND = "Press ↩️ **Undo** to bring it back."

FILE_MAX_BYTES = 4 * homebrew_file.MAX_FILE_CHARS  # UTF-8 is up to 4 bytes a character
DOWNLOAD_LABEL = "📤 Download file"
LOAD_LABEL = "📥 Load file"
LINK_LABEL = "🔗 Add from a link"
SITES_LABEL = "🌐 Approved sites"
PREVIEW_SHOWN = 700  # characters of the page's words shown before saving
DOWNLOAD_NOTE = (
    "**Your homebrew file.** Only you can see this message. Open it in any text editor, change "
    "it, then press **Load file** to bring it back. Nothing changes until you check it and say "
    "yes. It is plain text and holds every entry, the ones that are off too, so anyone you send "
    "it to can read them."
)
COULDNT_FILE = "Couldn't make the file just now. Try again in a moment."
FROM_BOOK_ASK = "**Is any of this from a published book?** (Your answer decides how it is kept.)"
FROM_BOOK_WARNING = (
    "**Before loading book material**\n"
    "DMbot doesn't check what you may use. By pressing **I have the right**, you say you may "
    "use this text.\n"
    "DMbot will copy it into this campaign's storage and use it when it answers questions. It "
    "is recorded that you confirmed, and when.\n"
    "It stays in this campaign only. It is never shared with other campaigns or servers, and "
    "never goes into a shared ruleset.\n"
    "You can remove it any time. It is deleted with the campaign."
)

WHO_MADE_ASK = "**Who made these?** Pick one. The name stays with the entries."
SITE_WARNING = (
    "**Do you have the right to use homebrew from {site}?**\n"
    "DMbot doesn't check. Press **I have the right** if you may use it in this campaign. "
    "DMbot records that you said so, and when.\n"
    "It stays in this campaign only and is never shared. You can remove it any time. DMbot "
    "won't ask again for {site} here."
)
BAD_SITE = "DMbot can't read that as a website. Try something like dndbeyond.com."

KIND_LABELS = {
    "spell": "✨ Spell",
    "monster": "👹 Monster",
    "item": "🗡 Item",
    "feat": "💪 Feat",
    "condition": "🌀 Condition",
    "species": "🧝 Species",
    "subclass": "🔱 Subclass",
    "background": "📖 Background",
    "other": "❔ Other",
}


# Plural words for the kind buttons ("Spells 10/12"); one for each kind in KINDS.
KIND_PLURALS = {
    "spell": "Spells",
    "monster": "Monsters",
    "item": "Items",
    "feat": "Feats",
    "condition": "Conditions",
    "species": "Species",
    "subclass": "Subclasses",
    "background": "Backgrounds",
    "other": "Other",
}
ALL_KINDS_LABEL = "↩ All kinds"
PICK_A_KIND = (
    "Pick a kind. **Spells 10/12** means 10 of your 12 spells are on. DMbot uses the ones that "
    "are on."
)


UNDO_PREFIX = "↩️ Put back "
# A button on a phone shows about PHONE_LABEL_MAX (25) characters; this one may be about 25%
# wider, NAME_LABEL_MAX (32), so "Amulet of Tenacity" is whole where the old 12-character cut left
# "Amulet of T…" (#1965). Discord's own limit (80) is far above it. A longer name is cut with an
# ellipsis: the shortest form that still says which entry.
UNDO_NAME_MAX = logic.NAME_LABEL_MAX - len(UNDO_PREFIX)


def _undo_label(gone: Homebrew) -> str:
    """Says which entry Undo puts back, since the note naming it goes with the next redraw."""
    return UNDO_PREFIX + logic.shorten(gone.name, UNDO_NAME_MAX)


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _fit(text: str, limit: int) -> str:
    """`fit_message`, plus one thing it doesn't know: the text is already escaped for Discord, so a
    cut must not leave a lone backslash in front of the "…" (it would escape it)."""
    if len(text) <= limit:
        return text
    cut = fit_message(text, limit=limit)[:-1]
    if (len(cut) - len(cut.rstrip("\\"))) % 2:
        cut = cut[:-1]
    return cut + "…"


def _store(interaction: discord.Interaction) -> HomebrewStore | None:
    return _bot(interaction).homebrew


WAITING_FOR_SITE = " (waiting for the site)"


def list_text(
    campaign: Campaign,
    entries: list[Homebrew],
    page: int,
    note: str = "",
    approved: set[str] | None = None,
    kind: str | None = None,
) -> str:
    """The kinds menu (`kind` None: the intro and how many of each, on the buttons) or one
    kind's list."""
    lines = [_fit(note, 200)] if note else []
    lines.append(f"📚 **Homebrew: {_fit(_md(campaign.name), 100)}**")
    lines.append(INTRO if not entries else SHORT_INTRO)
    if not entries:
        lines.append(NONE_YET)
    if kind is None:
        if entries:
            lines.append(PICK_A_KIND)
        return _fit("\n".join(lines), HOMEBREW_MAX)
    entries = [e for e in entries if e.kind == kind]
    lines.append(f"**{KIND_PLURALS.get(kind, kind)}**")
    shown = entries[page * PAGE_ENTRIES : (page + 1) * PAGE_ENTRIES]
    for entry in shown:
        off = "" if entry.enabled else " (off)"
        if approved is not None and entry.credit_domain and entry.credit_domain not in approved:
            off += WAITING_FOR_SITE  # its site was taken back (or never approved here)
        lines.append(
            _fit(f"{KIND_LABELS[entry.kind]} **{_md(entry.name)}**{off}: ", 400)
            + _fit(_md(entry.shown()), 120)
        )
    pages = max(1, -(-len(entries) // PAGE_ENTRIES))
    if pages > 1:
        lines.append(f"_Page {page + 1} of {pages}._")
    if any(not e.enabled for e in entries):
        lines.append("_(off) means DMbot ignores it until you turn it on._")
    if approved is not None and any(
        e.credit_domain and e.credit_domain not in approved for e in shown
    ):
        lines.append(
            "_(waiting for the site) means DMbot skips it until you approve that site again._"
        )
    return _fit("\n".join(lines), HOMEBREW_MAX)


def entry_text(entry: Homebrew, note: str = "") -> str:
    off = "" if entry.enabled else " It is off, so DMbot skips it. It is still saved."
    credit = credit_label(entry)
    by = f" _{_md(credit)}_" if credit else ""
    head = f"📚 **{_md(entry.name)}** ({entry.kind.capitalize()}).{by}{off}"
    body = _fit(_md(entry.text), HOMEBREW_MAX - 400)
    summary = f"\n_Shown in alerts as:_ {_md(entry.shown())}" if entry.summary else ""
    return _fit((f"{note}\n" if note else "") + f"{head}\n{body}{summary}", HOMEBREW_MAX)


async def _is_dm(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Checked on every press, from the campaign as it is now."""
    guild = interaction.guild
    if guild is None:
        return False
    current = await _bot(interaction).campaigns.get(guild.id, campaign.id)
    return current is not None and interaction.user.id in current.dm_user_ids


def _changed(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Tell the running bot homebrew changed, so a session's rules cards read the new names."""
    notify = getattr(_bot(interaction), "homebrew_changed", None)
    if notify is not None:
        notify(campaign.guild_id, campaign.id)


def _removed(interaction: discord.Interaction, campaign: Campaign) -> list[Homebrew]:
    return _bot(interaction).homebrew_removed.get((campaign.guild_id, campaign.id), [])


async def show_list(
    interaction: discord.Interaction,
    campaign: Campaign,
    *,
    page: int = 0,
    note: str = "",
    first: bool = False,
    kind: str | None = None,
) -> None:
    """Draw the kinds menu, or one kind's list (`kind`): as a new private message (`first`), or
    in place of this one."""
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    entries = await store.list(campaign.guild_id, campaign.id)
    sources = _bot(interaction).sources
    approved = (
        {a.domain for a in await sources.approvals(campaign.guild_id, campaign.id)}
        if sources is not None
        else None
    )
    if kind is not None and not any(e.kind == kind for e in entries):
        kind = None  # the last one of its kind was removed: back to the menu
    mine = entries if kind is None else [e for e in entries if e.kind == kind]
    page = min(max(page, 0), max(0, (len(mine) - 1) // PAGE_ENTRIES))
    menu = ListMenu(campaign, entries, page, _removed(interaction, campaign), kind)
    text = list_text(campaign, entries, page, note, approved, kind)
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
    interaction: discord.Interaction,
    campaign: Campaign,
    *,
    note: str = "",
    page: int = 0,
    kind: str | None = None,
) -> None:
    await _answer_first(interaction, in_place=True)
    current = await _bot(interaction).campaigns.get(campaign.guild_id, campaign.id)
    if current is None:
        await _tell(interaction, CAMPAIGN_GONE)
        return
    if interaction.user.id not in current.dm_user_ids:  # on every press, reads included
        await _tell(interaction, ONLY_DMS)
        return
    await show_list(interaction, current, page=page, note=note, kind=kind)


async def open_menu(interaction: discord.Interaction, campaign: Campaign) -> None:
    """From ⚙️ Manage DMbot or the command, for a DM (the caller checked; presses check again)."""
    await _answer_first(interaction)
    await show_list(interaction, campaign, first=True)


class ListMenu(_Menu):
    """The list's buttons: Add, page turning, a menu to pick an entry, and ↩️ for the last
    entries removed."""

    def __init__(
        self,
        campaign: Campaign,
        entries: list[Homebrew],
        page: int,
        removed: list[Homebrew],
        kind: str | None = None,
    ) -> None:
        super().__init__()
        self.campaign, self.page, self.kind = campaign, page, kind
        if kind is None:
            self._menu(entries, removed)
            return
        self.entries = [e for e in entries if e.kind == kind]
        entries = self.entries
        # Explicit rows, so Newer, Older and Undo together never push a button into the select's
        # row: 0 Add/Link/All kinds, 1 the pick list, 2 paging and Undo, 3 the file and sites.
        self.add_item(_Button(self._add, label="➕ Add", style=discord.ButtonStyle.primary, row=0))
        self.add_item(_Button(self._link, label=LINK_LABEL, row=0))
        self.add_item(_Button(self._all_kinds, label=ALL_KINDS_LABEL, row=0))
        if page > 0:
            self.add_item(_Button(self._newer, label="◀ Newer", row=2))
        if (page + 1) * PAGE_ENTRIES < len(entries):
            self.add_item(_Button(self._older, label="Older ▶", row=2))
        for gone in removed[-UNDO_SHOWN:]:  # the newest removal (the note says which)
            self.add_item(_Button(partial(self._undo, gone), label=_undo_label(gone), row=2))
        self.add_item(_Button(self._download, label=DOWNLOAD_LABEL, row=3))
        self.add_item(_Button(self._load, label=LOAD_LABEL, row=3))
        self.add_item(_Button(self._sites, label=SITES_LABEL, row=3))
        self.add_item(_Button(self._sources, label=homebrew_sources.SOURCES_LABEL, row=3))
        shown = entries[page * PAGE_ENTRIES : (page + 1) * PAGE_ENTRIES]
        if shown:
            self.pick = _Select(
                self._picked,
                row=1,
                placeholder="Pick one to open…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(e.name, logic.PHONE_LABEL_MAX),
                        value=str(e.id),
                        description=logic.shorten(
                            f"{e.kind}: {e.name}{'' if e.enabled else ' (off)'}. {e.shown()}",
                            logic.DESCRIPTION_MAX,
                        ),
                    )
                    for e in shown
                ],
            )
            self.add_item(self.pick)

    def _menu(self, entries: list[Homebrew], removed: list[Homebrew]) -> None:
        """The kinds menu: a button for each kind that has entries, then the actions. The kind
        list is fixed (nine today), so the buttons never need paging: Discord allows 25."""
        self.entries = entries
        total: dict[str, int] = {}
        on: dict[str, int] = {}
        for e in entries:
            total[e.kind] = total.get(e.kind, 0) + 1
            on[e.kind] = on.get(e.kind, 0) + (1 if e.enabled else 0)
        shown = [k for k in KINDS if total.get(k)]
        for n, k in enumerate(shown):
            label = f"{KIND_PLURALS[k]} {on[k]}/{total[k]}"
            self.add_item(_Button(partial(self._open_kind, k), label=label, row=n // 5))
        row = (len(shown) + 4) // 5  # the actions start on the row after the kinds
        self.add_item(
            _Button(self._add, label="➕ Add", style=discord.ButtonStyle.primary, row=row)
        )
        self.add_item(_Button(self._link, label=LINK_LABEL, row=row))
        self.add_item(_Button(self._load, label=LOAD_LABEL, row=row))
        self.add_item(_Button(self._download, label=DOWNLOAD_LABEL, row=row))
        self.add_item(_Button(self._sites, label=SITES_LABEL, row=row))
        self.add_item(_Button(self._packs, label=PACKS_LABEL, row=row + 1))
        self.add_item(_Button(self._sources, label=homebrew_sources.SOURCES_LABEL, row=row + 1))
        for gone in removed[-UNDO_SHOWN:]:  # the newest removal (the note says which)
            self.add_item(_Button(partial(self._undo, gone), label=_undo_label(gone), row=row + 1))

    async def _open_kind(self, kind: str, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, kind=kind)

    async def _sources(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await homebrew_sources.open_sources(interaction, self.campaign)

    async def _all_kinds(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign)

    async def _packs(self, interaction: discord.Interaction) -> None:
        from dmbot.ui import shared_packs

        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await shared_packs.open_menu(interaction, self.campaign)

    async def _add(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.kind is not None:  # already inside a kind: no need to ask which one
            await interaction.response.send_modal(AddForm(self.campaign, self.kind))
            return
        await _show(interaction, KIND_ASK, KindPick(self.campaign, self.page))

    async def _link(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await interaction.response.send_modal(LinkForm(self.campaign, self.page))

    async def _download(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _answer_first(interaction)  # a new private message; the list stays as it is
        store = _store(interaction)
        try:
            entries = await store.list(self.campaign.guild_id, self.campaign.id) if store else None
            if entries is None:
                raise RuntimeError("no store")
            data = homebrew_file.write(
                self.campaign.name, entries, self.campaign.target_ruleset
            ).encode()
            file = discord.File(
                io.BytesIO(data), filename=homebrew_file.filename(self.campaign.name)
            )
            await interaction.followup.send(
                DOWNLOAD_NOTE, file=file, ephemeral=True, allowed_mentions=NO_PINGS
            )
        except Exception:
            log.warning("Couldn't send the homebrew file", exc_info=True)
            await _tell(interaction, COULDNT_FILE)

    async def _load(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await interaction.response.send_modal(LoadForm(self.campaign, self.page))

    async def _sites(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.approved_sites import show_sites

        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _answer_first(interaction, in_place=True)
        await show_sites(interaction, self.campaign)

    async def _newer(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page - 1, kind=self.kind)

    async def _older(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page + 1, kind=self.kind)

    async def _picked(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        wanted = int(self.pick.values[0])
        entry = next((e for e in self.entries if e.id == wanted), None)
        if entry is None:
            await _redraw(
                interaction, self.campaign, note=ENTRY_GONE, page=self.page, kind=self.kind
            )
            return
        packs = await _packs_followed(interaction, self.campaign)
        await _show(
            interaction, entry_text(entry), EntryMenu(self.campaign, entry, self.page, packs)
        )

    async def _undo(self, gone: Homebrew, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        kept = _removed(interaction, self.campaign)
        if gone not in kept:  # put back already (another press, or another DM)
            await _redraw(interaction, self.campaign, page=self.page, kind=self.kind)
            return
        c = self.campaign
        try:
            await store.put_back(c.guild_id, c.id, interaction.user.id, gone)
        except HomebrewError as exc:
            if str(exc) == NO_CAMPAIGN:  # the campaign is gone: its removed entries go too
                _bot(interaction).homebrew_removed.pop((c.guild_id, c.id), None)
            await _redraw(interaction, c, note=str(exc), page=self.page, kind=self.kind)
            return
        kept.remove(gone)
        _changed(interaction, c)
        await _redraw(
            interaction,
            c,
            note=f"↩️ Put back **{_md(gone.name)}**.",
            page=self.page,
            kind=gone.kind,
        )


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
        self.add_item(_Button(self._back, label="↩ To the list"))

    async def _picked(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await interaction.response.send_modal(AddForm(self.campaign, self.pick.values[0]))

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


async def _packs_followed(
    interaction: discord.Interaction, campaign: Campaign
) -> list[tuple[str, str]]:
    """(id, name) of the homebrew packs the campaign follows and still gets updates from, in its
    order: where a DM may suggest an entry (#1537). None when packs aren't available."""
    sets = getattr(_bot(interaction), "rule_sets", None)
    followed = getattr(sets, "followed_packs", None)
    if followed is None:
        return []
    try:
        return [
            (f.set_id, f.name) for f in await followed(campaign.guild_id, campaign.id) if f.updating
        ]
    except Exception:
        log.warning("Couldn't look up the followed packs for a homebrew entry", exc_info=True)
        return []


class EntryMenu(_Menu):
    """One entry: Edit, Turn off or on, its kind, Remove, back, and (when the campaign follows a
    homebrew pack) 💡 Suggest to the pack's publisher."""

    def __init__(
        self,
        campaign: Campaign,
        entry: Homebrew,
        page: int,
        packs: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.campaign, self.entry, self.page = campaign, entry, page
        self.packs = packs or []
        self.add_item(_Button(self._edit, label="✏️ Edit"))
        self.add_item(_Button(self._enabled, label="Turn on" if not entry.enabled else "Turn off"))
        self.add_item(_Button(self._remove, label="🗑 Remove", style=discord.ButtonStyle.danger))
        if entry.source_kind == "link" and entry.page_url:
            self.add_item(_Button(self._update, label=UPDATE_LABEL))
        if self.packs:
            self.add_item(_Button(self._suggest, label=SUGGEST_LABEL))
        self.add_item(_Button(self._back, label="↩ To the list"))
        self.kind = _Select(
            self._kind,
            placeholder=f"Change kind (now: {KIND_LABELS[entry.kind]})",
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
            await _redraw(interaction, c, note=str(exc), page=self.page, kind=self.entry.kind)
            return
        _changed(interaction, c)
        packs = await _packs_followed(interaction, c)
        await _show(
            interaction,
            entry_text(saved, "✅ Saved."),
            EntryMenu(self.campaign, saved, self.page, packs),
        )

    async def _kind(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await self._change(interaction, kind=self.kind.values[0])

    async def _enabled(self, interaction: discord.Interaction) -> None:
        """Turn the entry off or on (#1629): kept either way, asks nothing."""
        if not await self._guard(interaction):
            return
        await _answer_first(interaction, in_place=True)
        store, c, e = _store(interaction), self.campaign, self.entry
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            saved = await store.set_enabled(
                c.guild_id,
                c.id,
                interaction.user.id,
                e.id,
                not e.enabled,
                unchanged_since=e.version,
            )
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page, kind=self.entry.kind)
            return
        _changed(interaction, c)
        packs = await _packs_followed(interaction, c)
        note = (
            "✅ Turned on. DMbot will use it."
            if saved.enabled
            else "✅ Turned off. DMbot will not use it."
        )
        await _show(interaction, entry_text(saved, note), EntryMenu(c, saved, self.page, packs))

    async def _update(self, interaction: discord.Interaction) -> None:
        """🔄 Update from the link (#1645): fetch the page again at this DM's press, show what
        changed, save only on Save."""
        if not await self._guard(interaction):
            return
        await _answer_first(interaction, in_place=True)
        importer = _bot(interaction).importer
        if importer is None:
            await _tell(interaction, NOT_READY)
            return
        c, e = self.campaign, self.entry
        try:
            fresh = await importer.refresh(c.guild_id, c.id, e)
        except ImportRefused as exc:
            await _show(
                interaction,
                entry_text(e, f"⚠️ {exc}"),
                EntryMenu(c, e, self.page, self.packs),
            )
            return
        await _show(interaction, update_preview_text(fresh), UpdatePreview(c, e, self.page, fresh))

    async def _remove(self, interaction: discord.Interaction) -> None:
        if await self._guard(interaction):
            await _show(
                interaction,
                f"Remove **{_md(self.entry.name)}**? You can undo for a little while.",
                ConfirmRemove(self.campaign, self.entry, self.page),
            )

    async def _suggest(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        if self.entry.from_book:  # book text stays in this campaign (CLAUDE.md, IP rule)
            await _tell(interaction, FROM_A_BOOK)
            return
        if len(self.packs) == 1:
            await _show(
                interaction,
                suggest_text(self.entry, self.packs[0][1]),
                ConfirmSuggest(self.campaign, self.entry, self.page, self.packs[0]),
            )
            return
        await _show(
            interaction,
            f"Send **{_md(self.entry.name)}** to which pack? Your name isn't sent.",
            PickPack(self.campaign, self.entry, self.page, self.packs),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page, kind=self.entry.kind)


UPDATE_LABEL = "🔄 Update from the link"
UPDATE_WARNING = "Every table using this page gets the update."


def update_preview_text(fresh: Refresh) -> str:
    """The warning comes before the page's words, so trimming a long page never cuts it."""
    preview = fresh.preview
    lines = [f"🔄 **{_md(preview.name)}**, from {preview.domain}", fresh.note]
    if fresh.changed:
        words = preview.text
        shown = words if len(words) <= PREVIEW_SHOWN else words[: PREVIEW_SHOWN - 1].rstrip() + "…"
        lines.append(f"Not saved yet. {UPDATE_WARNING}")
        lines.append(_md(shown))
        if preview.truncated:
            lines.append("_The page was longer than DMbot keeps, so the end was cut._")
    return _fit("\n".join(lines), HOMEBREW_MAX)


def suggest_text(entry: Homebrew, pack_name: str) -> str:
    """Never send at once: say where it goes, that nobody is named, and that nothing changes."""
    return (
        f"Send **{_md(entry.name)}** to the **{_md(pack_name)}** DM?\n{entry_text(entry)}\n"
        "They see only this text, not your name, table or server. "
        "Check it for anything private you typed in. "
        "Your own copy stays. You'll hear the answer at your next session."
    )


def _short_label(pack_name: str) -> str:
    """A phone cuts button text near 25 characters, and pack names are free text."""
    label = f"To {pack_name}"
    return label if len(label) <= 25 else label[:24] + "…"


class PickPack(_Menu):
    """More than one followed pack: one button for each."""

    def __init__(
        self, campaign: Campaign, entry: Homebrew, page: int, packs: list[tuple[str, str]]
    ) -> None:
        super().__init__()
        self.campaign, self.entry, self.page = campaign, entry, page
        for pack in packs:  # up to five, a whole row; "keep it here" sits on the row below
            self.add_item(_Button(partial(self._pick, pack), label=_short_label(pack[1]), row=0))
        self.add_item(_Button(self._keep, label=KEEP_PRIVATE_LABEL, row=1))

    async def _pick(self, pack: tuple[str, str], interaction: discord.Interaction) -> None:
        await _show(
            interaction,
            suggest_text(self.entry, pack[1]),
            ConfirmSuggest(self.campaign, self.entry, self.page, pack),
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page, kind=self.entry.kind)


class ConfirmSuggest(_Menu):
    def __init__(
        self, campaign: Campaign, entry: Homebrew, page: int, pack: tuple[str, str]
    ) -> None:
        super().__init__()
        self.campaign, self.entry, self.page, self.pack = campaign, entry, page, pack
        self.add_item(_Button(self._yes, label=SEND_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._keep, label=KEEP_PRIVATE_LABEL))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        sets = getattr(bot, "rule_sets", None)
        suggest = getattr(sets, "suggest_entry", None)
        if suggest is None:
            await _tell(interaction, NOT_READY)
            return
        c = self.campaign
        try:
            sid, pub_guild, pub_campaign, pack_name = await suggest(
                c.guild_id,
                c.id,
                interaction.user.id,
                self.entry.id,
                self.entry.version,
                self.pack[0],
            )  # the version the DM was shown: what goes out is what they confirmed
        except CampaignError as exc:  # (HouseRuleError and HomebrewError are both of these)
            await _redraw(interaction, c, note=str(exc), page=self.page, kind=self.entry.kind)
            return
        self.stop()
        pack_suggestion.send_note(bot, pub_guild, pub_campaign, sid, pack_name)
        await _redraw(
            interaction,
            c,
            note="💡 Sent, with no name. You'll hear the answer at your next session.",
            page=self.page,
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page, kind=self.entry.kind)


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
            await _redraw(interaction, c, note=str(exc), page=self.page, kind=self.entry.kind)
            return
        kept = _bot(interaction).homebrew_removed.setdefault((c.guild_id, c.id), [])
        kept.append(gone)
        del kept[:-UNDO_KEPT]
        _changed(interaction, c)
        await _redraw(
            interaction,
            c,
            note=f"🗑 Removed **{_md(gone.name)}**. {CHANGED_MIND}",
            page=self.page,
            kind=self.entry.kind,
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page, kind=self.entry.kind)


class _EntryForm(discord.ui.Modal):
    """The boxes shared by Add and Edit."""

    name: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="Name (your own creation only)",
        max_length=NAME_MAX,
        placeholder="For a published book, use Share a rulebook",
    )
    text: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="What it does",
        style=discord.TextStyle.paragraph,
        max_length=TEXT_MAX,
        placeholder="Write it as you would say it at the table",
    )
    summary: discord.ui.TextInput[_EntryForm] = discord.ui.TextInput(
        label="Short version (optional)",
        required=False,
        max_length=SUMMARY_MAX,
        placeholder="Shown when DMbot mentions it",
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign
        self.page = 0
        self.return_kind: str | None = None  # the kind's list to land on after a save

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
            await _tell(interaction, f"Not saved. {exc}{typed}")
            await _redraw(interaction, self.campaign, page=self.page, kind=self.return_kind)
            return
        _changed(interaction, self.campaign)
        await self.finish(interaction, note)

    async def finish(self, interaction: discord.Interaction, note: str) -> None:
        """After a save: back to the list (a form opened from a card shows the card instead)."""
        await _redraw(interaction, self.campaign, note=note, page=self.page, kind=self.return_kind)

    async def save(self, store: HomebrewStore, user_id: int) -> str:
        raise NotImplementedError


class AddForm(_EntryForm, title="Add homebrew"):
    def __init__(self, campaign: Campaign, kind: str) -> None:
        super().__init__(campaign)
        self.kind = self.return_kind = kind  # (after adding, the list of that kind)

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
            f"➕ Added **{_md(saved.name)}**. DMbot uses it now. To switch it off, open it and "
            "press **Turn off**."
        )


class EditForm(_EntryForm, title="Edit homebrew"):
    def __init__(self, campaign: Campaign, entry: Homebrew, page: int) -> None:
        super().__init__(campaign)
        self.entry, self.page = entry, page
        self.return_kind = entry.kind
        self.saved: Homebrew | None = None
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
            unchanged_since=e.version,
        )
        self.saved = saved
        return f"✏️ Saved **{_md(saved.name)}**."


class MakeMineForm(EditForm):
    """Opened by ✏️ Make it my own on a pack entry's card (#1541), on the copy just made. Saving
    shows that entry's own card in place of the pack's, so the card says it is the campaign's."""

    async def finish(self, interaction: discord.Interaction, note: str) -> None:
        if self.saved is None:
            await super().finish(interaction, note)
            return
        await _show(
            interaction,
            entry_text(self.saved, MINE_NOTE),
            EntryMenu(self.campaign, self.saved, self.page),
        )


def _preview(have: list[Homebrew], entries: tuple[FileEntry, ...]) -> tuple[int, int, int]:
    """(new, changed, same) if these entries were loaded now, by name; the store decides again."""
    old = {name_key(e.name): e for e in have}
    new = changed = same = 0
    for e in entries:
        found = old.get(name_key(e.name))
        if found is None:
            new += 1
            continue
        on = found.enabled if e.enabled is None else e.enabled  # (not said: as it is)
        alike = (found.kind, found.name, found.text, found.summary, found.enabled) == (
            e.kind,
            e.name,
            e.text,
            e.summary,
            on,
        )
        same, changed = (same + 1, changed) if alike else (same, changed + 1)
    return new, changed, same


def preview_text(
    parsed: homebrew_file.Parsed, counts: tuple[int, int, int], campaign_name: str
) -> str:
    new, changed, same = counts
    noun = "entry" if len(parsed.entries) == 1 else "entries"
    lines = [
        f"📄 **Ready to load: {len(parsed.entries)} {noun}** for {_fit(_md(campaign_name), 60)}: "
        f"{new} new, {changed} changed, {same} unchanged. Nothing is saved yet."
    ]
    if parsed.problems:
        lines.append(f"**Left out ({len(parsed.problems)}), because DMbot couldn't read them:**")
        for problem in parsed.problems[:5]:
            where = f"line {problem.line}: " if problem.line else ""
            lines.append(f"• {where}{_fit(_md(problem.text), 50)} ({problem.why})")
        if len(parsed.problems) > 5:
            lines.append("• …and more.")
    lines.append(FROM_BOOK_ASK)
    return _fit("\n".join(lines), HOMEBREW_MAX)


class LoadForm(discord.ui.Modal, title="Load homebrew"):
    file: discord.ui.Label[LoadForm] = discord.ui.Label(
        text="Pick your homebrew file (.txt, from Download file)",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign: Campaign, page: int = 0) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign, self.page = campaign, page

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        if not await _is_dm(interaction, self.campaign):  # before reading anything
            await _tell(interaction, ONLY_DMS)
            return
        upload = cast(discord.ui.FileUpload[LoadForm], self.file.component)
        if not upload.values:
            await _tell(interaction, f"No file was picked. Press {LOAD_LABEL} and pick one.")
            return
        attachment = upload.values[0]
        if attachment.size > FILE_MAX_BYTES:
            await _tell(
                interaction,
                "That file is too big to read. Split it into smaller files, or take some "
                "entries out.",
            )
            return
        try:
            data = await attachment.read()
        except discord.HTTPException:
            await _tell(interaction, "DMbot couldn't download that file. Try again.")
            return
        text = data.decode("utf-8", errors="replace")
        parsed = homebrew_file.parse(text)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        if not parsed.entries:
            if not parsed.problems:
                await _tell(interaction, "The file is empty, so nothing was loaded.")
                return
            reason = "; ".join(f"line {p.line}: {p.why}" for p in parsed.problems[:3])
            await _tell(
                interaction,
                f"Nothing in that file could be read, so nothing was loaded. {reason}. "
                f"Press **{DOWNLOAD_LABEL}** to see a correct example.",
            )
            return
        have = await store.list(self.campaign.guild_id, self.campaign.id)
        await _send(
            interaction,
            preview_text(parsed, _preview(have, parsed.entries), self.campaign.name),
            FromBookAsk(self.campaign, parsed.entries, fingerprint(text), self.page),
        )


class FromBookAsk(_Menu):
    """Before anything is stored: is any of it from a published book? No: the DM's own. Yes: the
    right-to-use confirmation first (who and when are recorded), and the entries are marked."""

    def __init__(
        self, campaign: Campaign, entries: tuple[FileEntry, ...], digest: str, page: int
    ) -> None:
        super().__init__()
        self.campaign, self.entries, self.digest, self.page = campaign, entries, digest, page
        self.add_item(_Button(self._own, label="No, all my own", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._book, label="Yes, from a book"))
        self.add_item(_Button(self._cancel, label="Cancel"))

    async def _own(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _show(
            interaction,
            WHO_MADE_ASK,
            WhoMade(self.campaign, self.entries, self.digest, self.page),
        )

    async def _book(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _show(
            interaction,
            FROM_BOOK_WARNING,
            RightToUse(self.campaign, self.entries, self.digest, self.page),
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, note="Nothing was loaded.", page=self.page)

    used = False  # one answer only: a double press must not record the confirmation twice

    async def _load(
        self,
        interaction: discord.Interaction,
        *,
        from_book: bool,
        edition: str | None = None,
        credit: tuple[str, str | None, str | None] = ("user", None, None),
    ) -> None:
        await _answer_first(interaction, in_place=True)
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.used:
            await _tell(interaction, "That is already being loaded.")
            return
        self.used = True  # before any await below
        store, c = _store(interaction), self.campaign
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            if from_book:
                # The IP rule (CLAUDE.md): who confirmed the right to use it, and when. Recorded
                # before anything is stored; a fingerprint tells files apart, never the words.
                await _bot(interaction).campaigns.record_confirmation(
                    c.guild_id, c.id, interaction.user.id, RULEBOOK, self.digest
                )
                log.info(
                    "Homebrew from a book confirmed: user=%s campaign=%s doc=%s",
                    interaction.user.id,
                    c.id,
                    self.digest[:12],
                )
            done = await store.load_file(
                c.guild_id,
                c.id,
                interaction.user.id,
                self.entries,
                from_book=from_book,
                book_edition=edition,
                credit=credit,
            )
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        self.stop()
        _changed(interaction, c)
        words = f"📥 Loaded: {done.added} new, {done.updated} changed, {done.same} the same."
        if credit[0] == "user":
            words += " Credited to you."
        elif credit[0] == "website":
            words += f" Credited to {credit[2]}."
        elif credit[0] == "name":
            words += f" Credited to {_md(' '.join((credit[1] or '').split()))}."
        if from_book:
            for_rules = {"2024": "2024 book", "2014": "2014 book"}.get(edition or "", "add-on book")
            words += (
                f" Entries from a book are tagged **[Shared book]** (a {for_rules})."
                " Your confirmation is recorded."
            )
        await _redraw(interaction, c, note=words, page=self.page)


class LinkForm(discord.ui.Modal, title="Add from a link"):
    """One box: the address of a published homebrew page (#1634). Only the sites DMbot adds
    homebrew from are accepted; the first time for a site in a campaign, the right-to-use warning
    comes first; then the page is read once and shown before anything is saved."""

    address: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="Link to the published page",
        max_length=500,
        placeholder="https://www.dndbeyond.com/magic-items/…",
    )

    def __init__(self, campaign: Campaign, page: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign, self.page = campaign, page

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        c = self.campaign
        if not await _is_dm(interaction, c):
            await _tell(interaction, ONLY_DMS)
            return
        bot = _bot(interaction)
        if bot.importer is None or bot.sources is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            url, domain = bot.importer.check_address(self.address.value)
        except ImportRefused as exc:
            await _tell(interaction, str(exc))
            return
        if not await bot.sources.approved(c.guild_id, c.id, domain):  # said once before: no warning
            await _show(
                interaction,
                SITE_WARNING.format(site=domain),
                RightToUseLink(c, self.page, url, domain),
            )
            return
        await read_link(interaction, c, self.page, url)


async def read_link(
    interaction: discord.Interaction, campaign: Campaign, page: int, url: str
) -> None:
    """Read the page once (a page kept here is re-used, not fetched again) and show what was
    found with Save / Cancel. Nothing is saved yet."""
    importer = _bot(interaction).importer
    if importer is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        preview = await importer.read(campaign.guild_id, campaign.id, url)
    except ImportRefused as exc:
        await _redraw(interaction, campaign, note=str(exc), page=page)
        return
    await _show(interaction, link_preview_text(preview), LinkPreview(campaign, page, preview))


def link_preview_text(preview: Preview) -> str:
    words = preview.text
    shown = words if len(words) <= PREVIEW_SHOWN else words[: PREVIEW_SHOWN - 1].rstrip() + "…"
    what = "" if preview.kind == "other" else f" ({preview.kind.capitalize()})"
    lines = [f"🔗 **{_md(preview.name)}**{what}, from {preview.domain}", _md(shown)]
    if preview.truncated:
        lines.append("_The page was longer than DMbot keeps, so the end was cut._")
    lines.append("Not saved yet. Press **Save** to add it to your homebrew.")
    return _fit("\n".join(lines), HOMEBREW_MAX)


class RightToUseLink(_Menu):
    """The site warning for a link, and the one button that confirms it: the approval is kept
    for the site in this campaign (who and when), then the page is read."""

    def __init__(self, campaign: Campaign, page: int, url: str, domain: str) -> None:
        super().__init__()
        self.campaign, self.page, self.url, self.domain = campaign, page, url, domain
        self.add_item(_Button(self._cancel, label="Cancel"))  # first: the safe one
        self.add_item(
            _Button(self._confirmed, label="I have the right", style=discord.ButtonStyle.danger)
        )

    used = False

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, note="Nothing was added.", page=self.page)

    async def _confirmed(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        sources = _bot(interaction).sources
        if sources is None or self.used:
            if sources is None:
                await _tell(interaction, NOT_READY)
            return
        self.used = True  # a double press records the confirmation once
        await _answer_first(interaction, in_place=True)
        c = self.campaign
        try:
            await sources.approve(c.guild_id, c.id, interaction.user.id, self.domain)
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        log.info("Site approved for homebrew: user=%s campaign=%s", interaction.user.id, c.id)
        self.stop()
        await read_link(interaction, c, self.page, self.url)


class LinkPreview(_Menu):
    """What was read from the page, with Save and Cancel."""

    def __init__(self, campaign: Campaign, page: int, preview: Preview) -> None:
        super().__init__()
        self.campaign, self.page, self.preview = campaign, page, preview
        self.add_item(_Button(self._save, label="Save", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._cancel, label="Cancel"))

    used = False

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, note="Nothing was added.", page=self.page)

    async def _save(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.used:
            await _tell(interaction, "Already saving this. Give it a second.")
            return
        self.used = True  # (checked and set with no await between: a second press sees it)
        c = self.campaign
        importer = _bot(interaction).importer
        if importer is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            saved = await importer.save(c.guild_id, c.id, interaction.user.id, self.preview)
        except ImportRefused as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        self.stop()
        _changed(interaction, c)
        note = f"🔗 Added **{_md(saved.name)}**. DMbot uses it now."
        await _redraw(interaction, c, note=note, page=self.page)


class UpdatePreview(_Menu):
    """The page fetched again, with Save and Cancel (#1645). When nothing changed there is
    nothing to save."""

    def __init__(self, campaign: Campaign, entry: Homebrew, page: int, fresh: Refresh) -> None:
        super().__init__()
        self.campaign, self.entry, self.page, self.fresh = campaign, entry, page, fresh
        if fresh.changed:
            self.add_item(_Button(self._save, label="Save", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._cancel, label="Cancel" if fresh.changed else "OK"))

    used = False

    async def _again(self, interaction: discord.Interaction, note: str) -> None:
        packs = await _packs_followed(interaction, self.campaign)
        entry = self.entry
        store = _store(interaction)
        if store is not None:  # (the words shown are the page's: read the entry as it is now)
            entry = await store.get(self.campaign.guild_id, self.campaign.id, entry.id) or entry
        await _show(
            interaction, entry_text(entry, note), EntryMenu(self.campaign, entry, self.page, packs)
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await self._again(interaction, "Nothing was changed.")

    async def _save(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.used:
            await _tell(interaction, "Already saving this. Give it a second.")
            return
        self.used = True
        c = self.campaign
        importer = _bot(interaction).importer
        if importer is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            await importer.save_refresh(c.guild_id, c.id, self.fresh.preview)
        except ImportRefused as exc:
            await self._again(interaction, f"⚠️ {exc}")
            return
        self.stop()
        _changed(interaction, c)
        await self._again(interaction, f"✅ Updated. {UPDATE_WARNING}")


class WhoMade(FromBookAsk):
    """Before anything is stored (#1633): who made these? Me (the default), a website (its site
    needs the campaign's once-per-site approval), or someone else, by name."""

    def __init__(
        self, campaign: Campaign, entries: tuple[FileEntry, ...], digest: str, page: int
    ) -> None:
        _Menu.__init__(self)
        self.campaign, self.entries, self.digest, self.page = campaign, entries, digest, page
        self.add_item(_Button(self._me, label="Me", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._site, label="A website"))
        self.add_item(_Button(self._someone, label="Someone else"))
        self.add_item(_Button(self._cancel, label="Cancel"))

    async def _me(self, interaction: discord.Interaction) -> None:
        await self._load(interaction, from_book=False)

    async def _site(self, interaction: discord.Interaction) -> None:
        if await _is_dm(interaction, self.campaign):
            await interaction.response.send_modal(SiteForm(self))
        else:
            await _tell(interaction, ONLY_DMS)

    async def _someone(self, interaction: discord.Interaction) -> None:
        if await _is_dm(interaction, self.campaign):
            await interaction.response.send_modal(NameForm(self))
        else:
            await _tell(interaction, ONLY_DMS)


class NameForm(discord.ui.Modal, title="Who made them?"):
    name: discord.ui.TextInput[NameForm] = discord.ui.TextInput(
        label="Their name", max_length=NAME_MAX, placeholder="A name, like Sam"
    )

    def __init__(self, owner: WhoMade) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.owner = owner

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.owner._load(interaction, from_book=False, credit=("name", self.name.value, None))


class SiteForm(discord.ui.Modal, title="Which website?"):
    site: discord.ui.TextInput[SiteForm] = discord.ui.TextInput(
        label="Website", max_length=200, placeholder="dndbeyond.com"
    )

    def __init__(self, owner: WhoMade) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.owner = owner

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        owner = self.owner
        typed = self.site.value.strip()
        address = normalise_url(typed) if "://" in typed else None
        domain = address[1] if address else normalise_domain(typed)
        if domain is None:
            await _tell(interaction, BAD_SITE)
            return
        if not await _is_dm(interaction, owner.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        sources = _bot(interaction).sources
        if sources is None:
            await _tell(interaction, NOT_READY)
            return
        c = owner.campaign
        if await sources.approved(c.guild_id, c.id, domain):  # said once before: no new warning
            await owner._load(interaction, from_book=False, credit=("website", None, domain))
            return
        await _show(
            interaction,
            SITE_WARNING.format(site=domain),
            RightToUseSite(owner, domain),
        )


class RightToUseSite(FromBookAsk):
    """The site warning, and the one button that confirms it. The approval is kept for the site
    in this campaign, with who confirmed and when."""

    def __init__(self, owner: WhoMade, domain: str) -> None:
        _Menu.__init__(self)
        self.campaign, self.entries = owner.campaign, owner.entries
        self.digest, self.page = owner.digest, owner.page
        self.owner, self.domain = owner, domain
        self.add_item(_Button(self._cancel, label="Cancel"))  # first: the safe one
        self.add_item(
            _Button(self._confirmed, label="I have the right", style=discord.ButtonStyle.danger)
        )

    async def _confirmed(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        sources = _bot(interaction).sources
        if sources is None:
            await _tell(interaction, NOT_READY)
            return
        await _answer_first(interaction, in_place=True)
        c = self.campaign
        try:
            await sources.approve(c.guild_id, c.id, interaction.user.id, self.domain)
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        log.info("Site approved for homebrew: user=%s campaign=%s", interaction.user.id, c.id)
        await self.owner._load(interaction, from_book=False, credit=("website", None, self.domain))


class RightToUse(FromBookAsk):
    """The warning, and the one button that confirms it."""

    def __init__(
        self, campaign: Campaign, entries: tuple[FileEntry, ...], digest: str, page: int
    ) -> None:
        _Menu.__init__(self)
        self.campaign, self.entries, self.digest, self.page = campaign, entries, digest, page
        self.add_item(_Button(self._cancel, label="Cancel"))  # first: the safe one
        self.add_item(
            _Button(self._confirmed, label="I have the right", style=discord.ButtonStyle.danger)
        )

    async def _confirmed(self, interaction: discord.Interaction) -> None:
        # One more question before anything is stored: which rules the book is for, so its
        # entries rank in that edition's place (#1563). The confirmation is recorded with the load.
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.used:  # a double press asks the question once
            return
        self.used = True
        self.stop()
        await _show(
            interaction,
            WHICH_RULES,
            BookEdition(self.campaign, self.entries, self.digest, self.page),
        )


class BookEdition(FromBookAsk):
    """Which rules is this book for? Asked once, after "I have the right"."""

    def __init__(
        self, campaign: Campaign, entries: tuple[FileEntry, ...], digest: str, page: int
    ) -> None:
        _Menu.__init__(self)
        self.campaign, self.entries, self.digest, self.page = campaign, entries, digest, page
        self.add_item(_Button(self._cancel, label="Cancel"))
        self.add_item(_Button(partial(self._pick, "2024"), label="2024 book"))
        self.add_item(_Button(partial(self._pick, "2014"), label="2014 book"))
        self.add_item(_Button(partial(self._pick, "other"), label="Not sure / add-on"))

    async def _pick(self, edition: str, interaction: discord.Interaction) -> None:
        await self._load(interaction, from_book=True, edition=edition)


@dmbot_group.command(
    name="homebrew",
    description="Your campaign's own spells, monsters, items and more (DMs only)",
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
