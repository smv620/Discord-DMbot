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

import io
import logging
from functools import partial
from typing import cast

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import RULEBOOK, fingerprint
from dmbot.logs import set_log_context
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
    name_key,
)
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NO_PINGS,
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
UNDO_SHOWN = 1  # ...and the newest is the one button on the list

NOT_READY = "Homebrew isn't available right now. Please try again in a moment."
ONLY_DMS = "Only this campaign's DM can open its homebrew. Ask your DM."
CAMPAIGN_GONE = "That campaign isn't here any more."
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
NOT_YOUR_CAMPAIGN = (
    "Only a campaign's DM can open its homebrew. You aren't a DM of one in this server."
)
ENTRY_GONE = "That entry is gone, maybe removed by another DM. Here is the list now."
INTRO = (
    "Your own spells, monsters, items and rules. Players can't see an entry until you press 👀. "
    "Hidden entries are for you only; DMbot's sidebar and table answers don't use them. "
    "DMbot only keeps what you write. For a published book, use **Share a rulebook**."
)
SHORT_INTRO = "Your own creations only. Hidden entries are for you only."
NONE_YET = "No homebrew yet. Press **➕ Add** to write the first one."
KIND_ASK = (
    "What are you adding? Pick one and a form opens.\n"
    "Hidden entries are for you only; DMbot's sidebar and table answers don't use them."
)
CHANGED_MIND = "Press ↩️ **Undo** to bring it back."

FILE_MAX_BYTES = 4 * homebrew_file.MAX_FILE_CHARS  # UTF-8 is up to 4 bytes a character
DOWNLOAD_LABEL = "📤 Download file"
LOAD_LABEL = "📥 Load file"
DOWNLOAD_NOTE = (
    "Your campaign's homebrew as a text file. Only you can see this message. Edit it anywhere, "
    "then press **Load file** to bring it back."
)
COULDNT_FILE = "Couldn't make the file just now. Try again in a moment."
FROM_BOOK_ASK = "**Is any of this from a published book?**"
FROM_BOOK_WARNING = (
    "**Pressing “I have the right” confirms you have the right to use this material.** DMbot "
    "doesn't check this. It is kept only in this campaign, never shared with other campaigns "
    "or servers, and you can remove it. It goes when the campaign is deleted."
)

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
    lines.append(INTRO if not entries else SHORT_INTRO)
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
    if interaction.user.id not in current.dm_user_ids:  # on every press, reads included
        await _tell(interaction, ONLY_DMS)
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
            self.add_item(_Button(self._newer, label="◀ Newer"))
        if (page + 1) * PAGE_ENTRIES < len(entries):
            self.add_item(_Button(self._older, label="Older ▶"))
        for gone in removed[-UNDO_SHOWN:]:  # the newest removal (the note says which)
            self.add_item(_Button(partial(self._undo, gone), label="↩️ Undo last remove"))
        self.add_item(_Button(self._download, label=DOWNLOAD_LABEL, row=2))
        self.add_item(_Button(self._load, label=LOAD_LABEL, row=2))
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
                            f"{mark(e)} {e.kind}: {e.name}. {e.shown()}", logic.DESCRIPTION_MAX
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
            data = homebrew_file.write(self.campaign.name, entries).encode()
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
            if str(exc) == NO_CAMPAIGN:  # the campaign is gone: its removed entries go too
                _bot(interaction).homebrew_removed.pop((c.guild_id, c.id), None)
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        kept.remove(gone)
        _changed(interaction, c)
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
        self.add_item(_Button(self._back, label="↩ To the list"))

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
                label="🙈 Only me" if entry.players_may_know else "👀 Players can know",
            )
        )
        self.add_item(_Button(self._remove, label="🗑 Remove", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._back, label="↩ To the list"))
        self.kind = _Select(
            self._kind,
            placeholder=f"Change kind (now: {entry.kind})",
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
        _changed(interaction, c)
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
                f"Remove **{_md(self.entry.name)}**? You can undo for a little while.",
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
        _changed(interaction, c)
        await _redraw(
            interaction, c, note=f"🗑 Removed **{_md(gone.name)}**. {CHANGED_MIND}", page=self.page
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


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
            await _redraw(interaction, self.campaign, page=self.page)
            return
        _changed(interaction, self.campaign)
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
            f"➕ Added **{_md(saved.name)}**. Only you see it. Open it and press 👀 when "
            "players may know."
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


def _preview(have: list[Homebrew], entries: tuple[FileEntry, ...]) -> tuple[int, int, int]:
    """(new, changed, same) if these entries were loaded now, by name; the store decides again."""
    old = {name_key(e.name): e for e in have}
    new = changed = same = 0
    for e in entries:
        found = old.get(name_key(e.name))
        if found is None:
            new += 1
            continue
        known = found.players_may_know if e.players_may_know is None else e.players_may_know
        alike = (found.kind, found.name, found.text, found.summary, found.players_may_know) == (
            e.kind,
            e.name,
            e.text,
            e.summary,
            known,
        )
        same, changed = (same + 1, changed) if alike else (same, changed + 1)
    return new, changed, same


def preview_text(
    parsed: homebrew_file.Parsed, counts: tuple[int, int, int], campaign_name: str
) -> str:
    new, changed, same = counts
    noun = "entry" if len(parsed.entries) == 1 else "entries"
    lines = [
        f"📄 **The file has {len(parsed.entries)} {noun}** for {_fit(_md(campaign_name), 60)}: "
        f"{new} new, {changed} changed, {same} the same."
    ]
    if parsed.problems:
        lines.append(f"**{len(parsed.problems)} part(s) could not be used and are left out:**")
        for problem in parsed.problems[:5]:
            where = f"line {problem.line}: " if problem.line else ""
            lines.append(f"• {where}{_fit(_md(problem.text), 50)} ({problem.why})")
        if len(parsed.problems) > 5:
            lines.append("• …and more.")
    lines.append(FROM_BOOK_ASK)
    return _fit("\n".join(lines), 1900)


class LoadForm(discord.ui.Modal, title="Load homebrew"):
    file: discord.ui.Label[LoadForm] = discord.ui.Label(
        text="Your homebrew file (a .txt file)",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign: Campaign, page: int = 0) -> None:
        super().__init__(timeout=30 * 60)
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
            await _tell(interaction, f"No file was added. Press {LOAD_LABEL} and pick one.")
            return
        attachment = upload.values[0]
        if attachment.size > FILE_MAX_BYTES:
            await _tell(interaction, "That file is too big to read.")
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
            reason = "; ".join(f"line {p.line}: {p.why}" for p in parsed.problems[:3]) or "empty"
            await _tell(
                interaction, f"Nothing in that file could be used ({reason}). Nothing was loaded."
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
        self.add_item(_Button(self._book, label="Yes, some is from a book"))
        self.add_item(_Button(self._cancel, label="Cancel"))

    async def _own(self, interaction: discord.Interaction) -> None:
        await self._load(interaction, from_book=False)

    async def _book(self, interaction: discord.Interaction) -> None:
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _show(
            interaction,
            f"{FROM_BOOK_WARNING}",
            RightToUse(self.campaign, self.entries, self.digest, self.page),
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, note="Nothing was loaded.", page=self.page)

    async def _load(self, interaction: discord.Interaction, *, from_book: bool) -> None:
        await _answer_first(interaction, in_place=True)
        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
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
                c.guild_id, c.id, interaction.user.id, self.entries, from_book=from_book
            )
        except HomebrewError as exc:
            await _redraw(interaction, c, note=str(exc), page=self.page)
            return
        self.stop()
        _changed(interaction, c)
        words = f"📥 Loaded: {done.added} new, {done.updated} changed, {done.same} the same."
        await _redraw(interaction, c, note=words, page=self.page)


class RightToUse(FromBookAsk):
    """The warning, and the one button that confirms it."""

    def __init__(
        self, campaign: Campaign, entries: tuple[FileEntry, ...], digest: str, page: int
    ) -> None:
        _Menu.__init__(self)
        self.campaign, self.entries, self.digest, self.page = campaign, entries, digest, page
        self.add_item(
            _Button(
                self._confirmed, label="I have the right: load it", style=discord.ButtonStyle.danger
            )
        )
        self.add_item(_Button(self._cancel, label="Cancel"))

    async def _confirmed(self, interaction: discord.Interaction) -> None:
        await self._load(interaction, from_book=True)


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
