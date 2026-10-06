"""Many names at a time (docs/PLAN.md, "Names at scale", step 3; #126): 📚 Browse by
kind, 📥 Add many (paste a list, or upload a file with `/dmbot names file:`, with a
template to start from) and 📤 Download all. Only the campaign's DMs and server
managers, privately; secret names only for the campaign's DMs.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup, NameEntry
from dmbot.memory.models import CONFIRMED, DM, PROPOSED, MemoryRuleError, NewName, name_key
from dmbot.memory.name_list import (
    MAX_FILE_BYTES,
    MAX_LINES,
    MAX_NAMES,
    OutName,
    Parsed,
    parse,
    render,
    template,
)
from dmbot.memory.sounds import sound_codes
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import NO_PINGS, _bot, _Button, _Menu, _replace, _Select, _send, _tell
from dmbot.ui.names import (
    KIND_SHORT,
    KINDS,
    NOT_AVAILABLE,
    PC,
    ReviewButton,
    _campaign_for,
    _md,
    _memory,
    changed,
    sees_secrets,
)

log = logging.getLogger(__name__)

PER_PAGE = 20
PAGE_MAX = 1800  # the page's text, under Discord's 2,000 characters
SHOWN_PROBLEMS = 5
PASTE_MAX = 4000  # what a form field holds
TEMPLATE_FILE = "dmbot-names-template.txt"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "campaign"


def _file(text: str, filename: str) -> discord.File:
    return discord.File(io.BytesIO(text.encode("utf-8")), filename=filename)


async def _names(interaction: discord.Interaction, campaign: Campaign) -> CampaignLookup | None:
    cache = _bot(interaction).lookup
    if cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return None
    try:
        return await cache.get(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't load the campaign's names")
        await _tell(interaction, "DMbot couldn't load the names just now. Try again in a moment.")
        return None


# ---- 📚 Browse by kind ------------------------------------------------------------------


def kinds_in(names: CampaignLookup) -> list[tuple[str, int]]:
    """(kind, how many confirmed names), most first."""
    counts: dict[str, int] = {}
    for e in names.entities.values():
        if e.status == CONFIRMED:
            counts[e.type] = counts.get(e.type, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], KIND_SHORT.get(kv[0], kv[0])))


def browse_order(names: CampaignLookup, kind: str, by_heard: bool) -> list[str]:
    """Confirmed entries of one kind: last heard first (never heard last), or A to Z."""
    ids = [e.id for e in names.entities.values() if e.status == CONFIRMED and e.type == kind]

    def az(entity_id: str) -> str:
        return name_key(names.entities[entity_id].name)

    if not by_heard:
        return sorted(ids, key=az)

    def heard(entity_id: str) -> tuple[int, str]:
        h = names.heard.get(entity_id)
        return (-(h.last_session_at or 0) if h else 1, az(entity_id))

    return sorted(ids, key=heard)


def pages(names: CampaignLookup, ids: list[str]) -> list[list[str]]:
    """Up to 20 names a page, fewer when the lines are long."""
    out: list[list[str]] = [[]]
    size = 0
    for entity_id in ids:
        line = len(_line(names, entity_id)) + 1
        if out[-1] and (len(out[-1]) >= PER_PAGE or size + line > PAGE_MAX):
            out.append([])
            size = 0
        out[-1].append(entity_id)
        size += line
    return out


def _line(names: CampaignLookup, entity_id: str) -> str:
    entity = names.entities[entity_id]
    h = names.heard.get(entity_id)
    when = f"heard <t:{h.last_session_at}:R>" if h and h.last_session_at else "not heard yet"
    return f"**{_md(logic.shorten(entity.name, 100))}** · {when}"


class Browse(_Menu):
    def __init__(
        self,
        campaign_id: str,
        names: CampaignLookup,
        kind: str | None = None,
        *,
        by_heard: bool = True,
        page: int = 0,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.kind = kind
        self.by_heard = by_heard
        self.page = page
        counts = kinds_in(names)
        self.empty = not counts
        self.open: _Select | None = None
        self.pick = _Select(
            self._kind_picked,
            placeholder="Pick a kind…",
            options=[
                discord.SelectOption(
                    label=f"{KIND_SHORT.get(k, k)} ({n})", value=k, default=k == kind
                )
                for k, n in counts[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        if counts:  # Discord needs at least one choice in a menu
            self.add_item(self.pick)
        self.shown: list[str] = []
        self.total = 0
        if kind is not None:
            book = pages(names, browse_order(names, kind, by_heard))
            self.total = len(book)
            self.page = max(0, min(page, self.total - 1))
            self.shown = book[self.page]
            if self.shown:
                self.open = _Select(
                    self._open,
                    placeholder="Open a name…",
                    options=[
                        discord.SelectOption(
                            label=logic.shorten(names.entities[e].name, logic.OPTION_LABEL_MAX),
                            value=e,
                        )
                        for e in self.shown
                    ],
                    row=1,
                )
                self.add_item(self.open)
            grey = discord.ButtonStyle.secondary
            self.add_item(
                _Button(self._back_page, label="◀", style=grey, row=2, disabled=self.page == 0)
            )
            self.add_item(
                _Button(
                    self._next_page,
                    label="▶",
                    style=grey,
                    row=2,
                    disabled=self.page >= self.total - 1,
                )
            )
            self.add_item(
                _Button(
                    self._sort,
                    label="Show A to Z" if by_heard else "Show last heard first",
                    style=grey,
                    row=2,
                )
            )

    def text(self, names: CampaignLookup, campaign: Campaign) -> str:
        if self.kind is None:
            if self.empty:
                return (
                    "📚 DMbot doesn't know any names yet. Add some with ➕ Add a name or "
                    "📥 Add many."
                )
            return f"📚 **Names for {_md(campaign.name)}:** pick a kind."
        kind = KIND_SHORT.get(self.kind, self.kind)
        order = "last heard first" if self.by_heard else "A to Z"
        lines = [f"📚 **{kind}** · {order} · page {self.page + 1} of {max(self.total, 1)}"]
        lines += [_line(names, e) for e in self.shown]
        return "\n".join(lines)

    async def _redraw(self, interaction: discord.Interaction, **changes: Any) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        names = await _names(interaction, campaign)
        if names is None:
            return
        state: dict[str, Any] = {
            "kind": self.kind,
            "by_heard": self.by_heard,
            "page": self.page,
        } | changes
        self.stop()
        view = Browse(
            campaign.id, names, state["kind"], by_heard=state["by_heard"], page=state["page"]
        )
        await _replace(interaction, view.text(names, campaign), view)

    async def _kind_picked(self, interaction: discord.Interaction) -> None:
        await self._redraw(interaction, kind=self.pick.values[0], page=0)

    async def _back_page(self, interaction: discord.Interaction) -> None:
        await self._redraw(interaction, page=self.page - 1)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        await self._redraw(interaction, page=self.page + 1)

    async def _sort(self, interaction: discord.Interaction) -> None:
        await self._redraw(interaction, by_heard=not self.by_heard, page=0)

    async def _open(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        if self.open is not None:
            await show_card(interaction, self.campaign_id, self.open.values[0])


async def show_browse(interaction: discord.Interaction, campaign_id: str) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    counts = kinds_in(names)
    kind = counts[0][0] if len(counts) == 1 else None  # one kind: straight to its names
    view = Browse(campaign.id, names, kind)
    if view.empty:
        await _tell(interaction, view.text(names, campaign))
        return
    await _send(interaction, view.text(names, campaign), view)


# ---- 📥 Add many ------------------------------------------------------------------------


def format_help(*, secrets: bool) -> str:
    parts = "name | kind | other names | secret names" if secrets else "name | kind | other names"
    return (
        "📥 **Add many names at once.** Press **📋 Paste a list**, or **📄 Get the template** "
        "to fill in and upload with `/dmbot names` (the **file** box).\n"
        f"One name per line: `{parts}`. Only the name is needed. Put a `,` or `;` between "
        "several other names. Kinds: NPC, place, group, creature, item, god, spell, event, "
        "other. A name with no kind waits in 📝 Check new names."
    )


class AddMany(_Menu):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(
            _Button(self._paste, label="📋 Paste a list", style=discord.ButtonStyle.primary)
        )
        self.add_item(
            _Button(
                self._template, label="📄 Get the template", style=discord.ButtonStyle.secondary
            )
        )

    async def _paste(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            self.origin = None  # the form's answer is a new message; cancel keeps this one
            await interaction.response.send_modal(
                PasteForm(self.campaign_id, secrets=sees_secrets(campaign, interaction.user.id))
            )

    async def _template(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            secrets = sees_secrets(campaign, interaction.user.id)
            await interaction.response.send_message(
                "📄 **The names template.** Lines starting with `###` explain it. Change the "
                "examples to your own names, save it as a .txt file, then type `/dmbot names` "
                "and add the file in the **file** box.",
                file=_file(template(secrets=secrets), TEMPLATE_FILE),
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )


class PasteForm(discord.ui.Modal, title="Add many names"):
    lines: discord.ui.TextInput[PasteForm] = discord.ui.TextInput(
        label="One name per line",
        style=discord.TextStyle.paragraph,
        placeholder="Belleros | NPC | Bell, the old knight\nBryn Shander | place\nUlfgar",
        max_length=PASTE_MAX,
    )

    def __init__(self, campaign_id: str, *, secrets: bool) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id
        if not secrets:
            self.lines.label = "One name per line (no secret names)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await import_list(interaction, self.campaign_id, self.lines.value)


def read_upload(raw: bytes) -> tuple[str | None, str | None]:
    """(text, problem) for an uploaded list: UTF-8, not too big, not too many lines."""
    if len(raw) > MAX_FILE_BYTES:
        return None, "That file is too big (up to 256 KB). Split it into smaller files."
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return (
            None,
            "DMbot can't read that file. Save it as a .txt file (not Word or PDF) and try again.",
        )
    if len(text.splitlines()) > MAX_LINES:
        return None, f"That file has more than {MAX_LINES:,} lines. Split it into smaller files."
    return text, None


def _sounds_known(names: CampaignLookup, name: str) -> bool:
    """It sounds like a different name everyone may know (Bell Eros and Belleros). Uses
    the copy's sound index: one look-up per sound, never a scan of every name."""
    key = name_key(name)
    return any(
        entry.confirmed and not entry.secret and entry.key != key
        for code in sound_codes(name)
        for entry in names.by_sound.get(code, ())
    )


async def import_list(interaction: discord.Interaction, campaign_id: str, text: str) -> None:
    """Save a list of names in one go, then say what happened, with one Undo for all."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets_ok = sees_secrets(campaign, interaction.user.id)
    parsed = parse(text, secrets=secrets_ok)
    # Names everyone may know. For anyone but the campaign's DMs, a clash with a secret
    # name looks exactly like no clash: they never learn one exists.
    taken = {e.key for e in names.names if secrets_ok or not e.secret}
    new: list[NewName] = []
    groups: dict[str, list[int]] = {}  # kind word ("" for none) → positions in `new`
    known = look = dropped = 0
    for line in parsed.lines:
        key = name_key(line.name)
        if key in taken:
            known += 1
            continue
        others = tuple(o for o in line.others if name_key(o) not in taken)
        secret = tuple(x for x in line.secrets if name_key(x) not in taken)
        dropped += len(line.others) - len(others) + len(line.secrets) - len(secret)
        # No kind, or it sounds like a known name: saved as a suggestion, waiting in
        # 📝 Check new names.
        sounds_known = _sounds_known(names, line.name)
        unclear = line.kind is None or sounds_known
        look += unclear
        if line.kind is None and not sounds_known:
            # Asked once per word after saving: "every wizard is an NPC".
            groups.setdefault(" ".join(line.kind_word.casefold().split()), []).append(len(new))
        new.append(
            NewName(
                line.name, line.kind or "concept", PROPOSED if unclear else CONFIRMED,
                others, secret,
            )
        )  # fmt: skip
        taken.update({key, *map(name_key, others), *map(name_key, secret)})
    room = MAX_NAMES - len(names.entities)
    if len(new) > room:
        await _tell(
            interaction,
            f"Nothing was added: this campaign has room for {max(room, 0):,} more names (up to "
            f"{MAX_NAMES:,}). Split the list and add part of it.",
        )
        return
    batch = None
    added = 0
    if new:
        try:
            written = await memory.add_names(
                campaign.guild_id, campaign.id, new, source=DM, secret_clashes=secrets_ok
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Nothing was added. {exc}")
            return
        except Exception:
            log.exception("Adding a list of names failed")
            await _tell(interaction, "Nothing was added: something went wrong. Try again.")
            return
        batch = written.batch
        added = sum(1 for i in written.value if i is not None)
        # Saved by someone else at the same moment: counted as already known.
        known += len(new) - added
        look -= sum(
            1 for n, i in zip(new, written.value, strict=True) if i is None and n.status == PROPOSED
        )
        changed(interaction, campaign)
    saved = written.value if new else []
    kinds = {
        word: ids
        for word, positions in groups.items()
        if (ids := [i for p in positions if (i := saved[p]) is not None])
    }
    await _send_summary(interaction, campaign, parsed, added, look, known, dropped, batch, kinds)


async def _send_summary(
    interaction: discord.Interaction,
    campaign: Campaign,
    parsed: Parsed,
    added: int,
    look: int,
    known: int,
    dropped: int,
    batch: int | None,
    kinds: dict[str, list[str]],
) -> None:
    lines = [summary_text(added, look, known, parsed.repeated, dropped, parsed.refused)]
    asked = sorted(kinds.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:KIND_QUESTIONS]
    if asked:
        words = ", ".join(
            f"**{_md(w)}** ({len(ids)})" if w else f"no kind ({len(ids)})" for w, ids in asked
        )
        lines.append(
            f"❓ DMbot doesn't know these kinds: {words}. Pick what each one is below to set "
            "all its names at once."
        )
        if len(kinds) > len(asked):
            lines.append(f"{len(kinds) - len(asked)} more wait in 📝 Check new names.")
    # The buttons keep working after a restart; the kind menus while DMbot keeps running.
    view = KindQuestions(campaign.id, asked)
    if look:
        view.add_item(ReviewButton(campaign.id))
    if batch is not None and added:
        view.add_item(UndoListButton(campaign.id, batch))
    for item in view.children:
        if not isinstance(item, KindSelect):
            item.row = 4
    await interaction.followup.send(
        "\n".join(lines),
        view=view,
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


def summary_text(
    added: int,
    look: int,
    known: int,
    repeated: int,
    dropped: int,
    refused: list[tuple[int, str]],
) -> str:
    """What happened, what needs doing first."""

    def n(count: int, word: str) -> str:
        return f"{count:,} {word}{'' if count == 1 else 's'}"

    lines = [f"📥 **Added {n(added, 'name')}.**" if added else "📥 **No new names were added.**"]
    if refused:
        lines.append(
            f"⚠️ **{n(len(refused), 'line')} {'wasn' if len(refused) == 1 else 'weren'}'t "
            "added.** Fix them and add just those lines again:"
        )
        lines += [f"• line {line}: {why}." for line, why in refused[:SHOWN_PROBLEMS]]
        if len(refused) > SHOWN_PROBLEMS:
            lines.append(f"• … and {len(refused) - SHOWN_PROBLEMS} more.")
    if look:
        they = "it sounds" if look == 1 else "they sound"
        lines.append(
            f"📝 **{n(look, 'name')} {'needs' if look == 1 else 'need'} you to check "
            f"{'it' if look == 1 else 'them'}** (no kind, or {they} like a name DMbot already "
            "knows). Press 📝 Check new names."
        )
    skipped = []
    if known:
        skipped.append(f"{n(known, 'name')} DMbot already knows")
    if repeated:
        skipped.append(f"{n(repeated, 'name')} listed twice")
    if dropped:
        skipped.append(f"{n(dropped, 'other name')} already used by another name")
    if skipped:
        lines.append(f"Skipped: {', '.join(skipped)}.")
    if added:
        lines.append(
            "Wrong list? **Undo** takes the whole list back, until you check or change any of "
            "those names."
        )
    return "\n".join(lines)


KIND_QUESTIONS = 4  # one menu per unknown kind word; the 5th row holds the buttons


class KindSelect(discord.ui.Select["KindQuestions"]):
    def __init__(self, questions: KindQuestions, word: str, ids: list[str], row: int) -> None:
        what = f"every “{logic.shorten(word, 40)}”" if word else "the names with no kind"
        super().__init__(
            placeholder=logic.shorten(f"What is {what} ({len(ids)})?", 150),
            options=[
                discord.SelectOption(label=label, value=kind)
                for kind, label in KINDS.items()
                if kind != PC
            ],
            row=row,
        )
        self.questions = questions
        self.word = word
        self.ids = ids

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.questions.picked(self, interaction)


class KindQuestions(discord.ui.View):
    """ "wizard (50): what is it?" once per unknown kind word, under the summary."""

    def __init__(self, campaign_id: str, asked: list[tuple[str, list[str]]]) -> None:
        super().__init__(timeout=60 * 60)
        self.campaign_id = campaign_id
        for row, (word, ids) in enumerate(asked):
            self.add_item(KindSelect(self, word, ids, row))

    async def picked(self, select: KindSelect, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        kind = select.values[0]
        try:
            written = await memory.confirm_kinds(
                campaign.guild_id, campaign.id, select.ids, kind, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change them. {exc}")
            return
        changed(interaction, campaign)
        self.remove_item(select)
        what = f"Every **{_md(select.word)}**" if select.word else "The names with no kind"
        done = written.value
        note = (
            f"✅ {what}: {done} name{'' if done == 1 else 's'} set to **{_md(KINDS[kind])}**."
            + ("" if done == len(select.ids) else " (The rest were checked or changed already.)")
        )
        content = (interaction.message.content if interaction.message else "") + "\n" + note
        await interaction.response.edit_message(
            content=content[:2000], view=self, allowed_mentions=NO_PINGS
        )


class UndoListButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:undolist:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Takes a whole list of names back; keeps working after a restart."""

    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:undolist:{campaign_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoListButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        await interaction.response.defer()  # a long list takes a while to take back
        try:
            await memory.undo_names(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError:
            await _tell(
                interaction,
                "Couldn't undo the whole list: some of those names were checked, changed or "
                "heard since (or it was already undone). Remove the wrong ones from their cards "
                "instead.",
            )
            return
        changed(interaction, campaign)
        await interaction.edit_original_response(
            content="↩️ Took the whole list back.", view=None, allowed_mentions=NO_PINGS
        )


# ---- 📤 Download all --------------------------------------------------------------------


def download_text(names: CampaignLookup, campaign: Campaign, *, secrets: bool) -> tuple[str, int]:
    """The campaign's confirmed names as a list file (the same format Add many reads).
    Misheard spellings DMbot learned aren't other names, so they're left out."""
    by_entity: dict[str, list[NameEntry]] = {}
    for a in names.names:
        if a.confirmed and a.kind != "misheard":
            by_entity.setdefault(a.entity_id, []).append(a)
    out: list[OutName] = []
    for e in names.entities.values():
        if e.status != CONFIRMED:
            continue
        own = name_key(e.name)
        mine = by_entity.get(e.id, [])
        others = tuple(a.text for a in mine if not a.secret and a.key != own)
        hidden = tuple(a.text for a in mine if a.secret) if secrets else ()
        out.append(OutName(e.name, e.type, others, hidden))
    return render(out, campaign=campaign.name, secrets=secrets), len(out)


async def send_download(interaction: discord.Interaction, campaign_id: str) -> None:
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    text, count = await asyncio.to_thread(download_text, names, campaign, secrets=secrets)
    if not count:
        await _tell(
            interaction,
            f"DMbot doesn't know any names for {_md(campaign.name)} yet. Press 📥 Add many to "
            "start from the template.",
        )
        return
    day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
    warning = "⚠️ Includes secret names: don't share this file with players. " if secrets else ""
    await interaction.followup.send(
        f"📤 **All {count:,} name{'' if count == 1 else 's'} for {_md(campaign.name)}.** "
        f"{warning}Names still waiting in 📝 Check new names aren't included. Edit it and add "
        "it again with `/dmbot names` (names DMbot already knows are skipped).",
        file=_file(text, f"names-{_slug(campaign.name)}-{day}.txt"),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )
