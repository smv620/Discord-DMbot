"""Many names at a time (docs/PLAN.md, "Names at scale", step 3; #126): 📚 Browse by
kind, 📥 Add many (paste a list, a link to any document or page, or upload a file; with
a template to start from) and 📤 Download all. Only the campaign's DMs and server
managers, privately; secret names only for the campaign's DMs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import discord

from dmbot import fetch
from dmbot.ai import AIError, AnthropicClient, Reply
from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup, NameEntry
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    TooLateToUndo,
    days,
    name_key,
)
from dmbot.memory.name_documents import (
    MAX_DOCUMENT_BYTES,
    TYPES_HELP,
    DocumentError,
    chunks,
    clean_reply,
    decode_text,
    instructions,
    kind_of_file,
    merge_lists,
    request_text,
    text_of,
)
from dmbot.memory.name_list import (
    MAX_FILE_BYTES,
    MAX_LINES,
    MAX_NAMES,
    OutName,
    Parsed,
    header,
    parse,
    render,
    template,
)
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import NO_PINGS, _bot, _Button, _Menu, _replace, _Select, _send, _tell
from dmbot.ui.list_matches import UNKNOWN_KIND, KindDiffers, Near, plan
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
SHOWN_SWAPPED = 3
PASTE_MAX = 4000  # what a form field holds
TEMPLATE_FILE = "dmbot-names-template.txt"
LINK_MAX = 2000  # a form field for one link
UPLOAD = "📥 Add many > 📎 Upload a file"
# One link and nothing else: with https://, www., or bare (docs.google.com/document/…).
_LONE_LINK = re.compile(
    r"^(?:<?https?://\S+|www\.\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*)$", re.IGNORECASE
)


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
                    placeholder="✏️ Fix, change or remove a name…",
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
        "📥 **Add many names at once.** Paste a list, paste a link, or upload a file (PDF, "
        "Word, text or web page).\n"
        "A list written like the template is added straight away. For anything else, "
        "DMbot's AI finds the names and shows you the list first: nothing is added until "
        "you press **Add these names**.\n"
        f"Template: one name per line, `{parts}`. Only the name is needed. Separate other "
        "names with `,` or `;`. Kinds: NPC, place, group, creature, item, god, spell, event, "
        "other. Names with no kind wait in 📝 Check new names.\n"
        "If 📎 Upload a file shows no place to pick a file, type `/dmbot names` and add the "
        "file in its **file** box."
    )


class AddMany(_Menu):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._paste, label="📋 Paste a list", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._link, label="🔗 Paste a link", style=grey))
        self.add_item(_Button(self._upload, label="📎 Upload a file", style=grey))
        # On its own row, so four buttons never get squeezed on a phone.
        self.add_item(_Button(self._template, label="📄 Get the template", style=grey, row=1))

    async def _paste(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            self.origin = None  # the form's answer is a new message; cancel keeps this one
            await interaction.response.send_modal(
                PasteForm(self.campaign_id, secrets=sees_secrets(campaign, interaction.user.id))
            )

    async def _link(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            self.origin = None
            await interaction.response.send_modal(LinkForm(self.campaign_id))

    async def _upload(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            self.origin = None
            await interaction.response.send_modal(UploadForm(self.campaign_id))

    async def _template(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            secrets = sees_secrets(campaign, interaction.user.id)
            await interaction.response.send_message(
                "📄 **The names template.** Lines starting with `###` explain it. Change the "
                f"examples to your own names, then add it with {UPLOAD}, or copy the lines "
                "into 📋 Paste a list.",
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
        text = self.lines.value.strip()
        if _LONE_LINK.match(text):  # a link, not a list: read what it points to
            await take_link(interaction, self.campaign_id, text)
            return
        await take_list(interaction, self.campaign_id, Upload(self.lines.value, "your list"))


class LinkForm(discord.ui.Modal, title="Add names from a link"):
    link: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="Link to a document or web page",
        placeholder="https://docs.google.com/document/d/…",
        max_length=LINK_MAX,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await take_link(interaction, self.campaign_id, self.link.value)


class UploadForm(discord.ui.Modal, title="Add names from a file"):
    file: discord.ui.Label[UploadForm] = discord.ui.Label(
        text="A list, or a PDF, Word, text or web page file",  # 45 characters at most
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id) is None:
            return  # checked before downloading anything
        upload = cast(discord.ui.FileUpload[UploadForm], self.file.component)
        if not upload.values:
            await _tell(interaction, "No file was added. Press 📎 Upload a file and pick one.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        got, problem = await read_attachment(upload.values[0])
        if got is None:
            await _tell(interaction, problem or "DMbot couldn't read that.")
            return
        await take_list(interaction, self.campaign_id, got)


NO_AI_FOR_DOCUMENTS = (
    "DMbot can't read documents or links here (its AI isn't switched on). Copy the names "
    "into the template instead: 📥 Add many > 📄 Get the template."
)
_link_busy: set[int] = set()  # servers opening a link right now
LINK_BUSY = (
    "DMbot is already opening another link for this server. Wait a few seconds and try again."
)


async def read_link_once(guild_id: int, link: str) -> tuple[Upload | None, str | None]:
    """read_link, one link at a time per server: every way in (🔗 Paste a link, a link
    pasted as a list, `/dmbot names link:`) goes through here."""
    if guild_id in _link_busy:
        return None, LINK_BUSY
    _link_busy.add(guild_id)
    try:
        return await read_link(link)
    finally:
        _link_busy.discard(guild_id)


async def take_link(interaction: discord.Interaction, campaign_id: str, link: str) -> None:
    """Read a link (it can take a while, so answer "thinking" first), then offer its
    text to the AI. Text from a link is never read as a names list (#264). One link at a
    time per server, and nothing is fetched when there's no AI to read it."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if _bot(interaction).ai is None:
        await _tell(interaction, NO_AI_FOR_DOCUMENTS)
        return
    if campaign.guild_id in _link_busy:
        await _tell(interaction, LINK_BUSY)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    upload, problem = await read_link_once(campaign.guild_id, link)
    if upload is None:
        await _tell(interaction, problem or "DMbot couldn't read that.")
        return
    await take_list(interaction, campaign_id, upload)


@dataclass(frozen=True, slots=True)
class Upload:
    """Names to add: a list (pasted or a .txt file), or a document's text."""

    text: str
    label: str  # "report.pdf", "your list", "the linked page or file"
    document: bool = False  # not meant as a names list: straight to the AI


_PARSING = asyncio.Semaphore(2)  # documents read at once, across all servers


async def _document(filename: str, raw: bytes, label: str) -> tuple[Upload | None, str | None]:
    async with _PARSING:
        try:
            return Upload(await asyncio.to_thread(text_of, filename, raw), label, True), None
        except DocumentError as exc:
            return None, str(exc)


async def read_attachment(file: discord.Attachment) -> tuple[Upload | None, str | None]:
    """(upload, problem) for `/dmbot names file:`: a list, or a document's text. A text
    file too big for a list is read as a document."""
    label = logic.shorten(file.filename, 60)
    kind = kind_of_file(file.filename)
    if kind == "unknown":
        return None, TYPES_HELP
    if file.size > MAX_DOCUMENT_BYTES:
        return None, "That file is too big (up to 10 MB). Split it into smaller files."
    try:
        raw = await file.read()
    except discord.HTTPException:
        return None, "DMbot couldn't download that file. Try again."
    if kind == "text":
        text, _ = read_upload(raw)
        if text is not None:
            return Upload(text, label), None
    return await _document(file.filename, raw, label)


async def read_link(link: str) -> tuple[Upload | None, str | None]:
    """(upload, problem) for a link: any document or web page anyone can open, fetched
    with every address checked (dmbot.fetch). Always a document, never a names list."""
    try:
        got = await fetch.fetch(link)
    except fetch.LinkError as exc:
        return None, str(exc)
    return await _document(got.filename, got.data, "the linked page or file")


def read_upload(raw: bytes) -> tuple[str | None, str | None]:
    """(text, problem) for an uploaded list: not too big, not too many lines (bigger
    files are read as documents instead)."""
    if len(raw) > MAX_FILE_BYTES:
        return None, "That file is too big for a list (up to 256 KB)."
    text = decode_text(raw)
    if len(text.splitlines()) > MAX_LINES:
        return None, f"That file has more than {MAX_LINES:,} lines."
    return text, None


ONLY_DM_SECRETS = "only the campaign's DM can add secret names"
AI_READS_PER_DAY = 20  # per server, for the operator's bill (bring-your-own keys: #50)
AI_TIME_LIMIT_S = 600  # well inside Discord's 15 minutes to answer
_ai_busy: set[int] = set()  # servers with an AI read running
_ai_reads: dict[tuple[int, str], int] = {}  # (server, day) → reads


async def take_list(interaction: discord.Interaction, campaign_id: str, upload: Upload) -> None:
    """A list DMbot can read all of is added straight away. Anything else (a document, or
    a list with any line that doesn't fit) goes to the AI, which writes the list for the
    DM to check first (owner's decision, 2026-10-06)."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    parsed = None
    if not upload.document:
        parsed = parse(upload.text, secrets=sees_secrets(campaign, interaction.user.id))
        if not parsed.lines and not parsed.refused:
            await _tell(
                interaction,
                f"There are no names in {_md(upload.label)}. Lines starting with # are "
                "skipped. Put one name per line and try again.",
            )
            return
        # Every line fits, or the only problem is secret names from someone who may not
        # add them (the AI must never turn those into names everyone sees).
        if all(why.startswith(ONLY_DM_SECRETS) for _, why in parsed.refused):
            await import_list(interaction, campaign.id, upload.text)
            return
    if _bot(interaction).ai is None:
        if upload.document:
            await _tell(interaction, NO_AI_FOR_DOCUMENTS)
        else:
            await import_list(interaction, campaign.id, upload.text)  # what fits, and why not
        return
    fits = len(parsed.lines) if parsed else 0
    await _send(
        interaction,
        _ai_offer_text(upload, parsed),
        AIOffer(campaign.id, upload, fits, fits > len(parsed.refused) if parsed else False),
    )


def _ai_offer_text(upload: Upload, parsed: Parsed | None) -> str:
    rights = (
        "**Pressing 🤖 Find names confirms you have the right to use this material.** DMbot "
        "doesn't check this. Its text goes to Anthropic (an AI company) to be read."
    )
    if parsed is None:
        return (
            f"📄 **Find the names in {_md(upload.label)}?** DMbot's AI reads it and makes a "
            f"list for you to check. Nothing is added until you say so.\n{rights}"
        )
    bad = len(parsed.refused)
    shown = "; ".join(f"line {n}: {why}" for n, why in parsed.refused[:2])
    more = " …" if bad > 2 else ""
    return (
        f"📄 **{bad} line{'' if bad == 1 else 's'} in {_md(upload.label)} "
        f"{'doesn' if bad == 1 else 'don'}'t fit** ({shown}{more}). DMbot's AI can read the "
        "whole list and make a clean one for you to check first"
        + (f". Or add just the {len(parsed.lines)} that fit." if parsed.lines else ".")
        + f"\n{rights}"
    )


class AIOffer(_Menu):
    def __init__(self, campaign_id: str, upload: Upload, fits: int, fits_first: bool) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.upload = upload
        blue, grey = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
        self.add_item(
            _Button(
                self._read,
                label="🤖 Find names",  # the message says what pressing it confirms
                style=grey if fits_first else blue,
            )
        )
        if fits:
            self.add_item(
                _Button(
                    self._as_is,
                    label=f"Add the {fits} that fit",
                    style=blue if fits_first else grey,
                )
            )
        self.add_item(_Button(self._cancel, label="Cancel", style=grey))

    async def _read(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        ai = _bot(interaction).ai
        if ai is None:
            await _tell(interaction, "DMbot's AI was switched off. Nothing was added.")
            return
        guild = campaign.guild_id
        day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
        if guild in _ai_busy:
            await _tell(interaction, "DMbot is already reading a document for this server. "
                        "Try again when it's done.")  # fmt: skip
            return
        if _ai_reads.get((guild, day), 0) >= AI_READS_PER_DAY:
            await _tell(interaction, "This server has used today's document reads. Try "
                        "again tomorrow, or use the template.")  # fmt: skip
            return
        self.stop()
        _ai_busy.add(guild)
        for old in [k for k in _ai_reads if k[1] != day]:
            del _ai_reads[old]  # only today counts
        _ai_reads[(guild, day)] = _ai_reads.get((guild, day), 0) + 1
        try:
            await interaction.response.edit_message(
                content=f"🤖 Reading {_md(self.upload.label)}… this can take a few minutes for "
                "a long document.",
                view=None,
                allowed_mentions=NO_PINGS,
            )
            # The IP rule (CLAUDE.md): who confirmed the right to use it, and when. The
            # file's name stays out of the log (it may hold names); a short fingerprint
            # tells documents apart.
            log.info(
                "Shared material confirmed for AI reading: user=%s campaign=%s doc=%s",
                interaction.user.id,
                campaign.id,
                hashlib.sha256(self.upload.text.encode()).hexdigest()[:12],
            )
            secrets = sees_secrets(campaign, interaction.user.id)
            async with asyncio.timeout(AI_TIME_LIMIT_S):
                listed, cut = await ai_names_list(ai, self.upload.text, secrets=secrets)
            await show_ai_list(interaction, campaign, self.upload, listed, cut, secrets=secrets)
        except AIError as exc:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(content=str(exc))
        except TimeoutError:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="Reading took too long. Nothing was added. Split the document into "
                    "smaller parts and try again."
                )
        except Exception:
            log.exception("Reading a document with the AI failed")
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="Something went wrong. Nothing was added. Try again later."
                )
        finally:
            _ai_busy.discard(guild)

    async def _as_is(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Adding the lines that fit…", view=None, allowed_mentions=NO_PINGS
        )
        await import_list(interaction, self.campaign_id, self.upload.text)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. Nothing was sent or added.", view=None
        )


AI_AT_ONCE = asyncio.Semaphore(3)  # AI requests running at once, across all servers


async def ai_names_list(ai: AnthropicClient, text: str, *, secrets: bool) -> tuple[str, bool]:
    """The AI's names list for a document (pieces read side by side, merged so each name
    appears once), and whether any answer was cut off."""
    system = instructions(secrets=secrets)

    async def one(piece: str) -> Reply:
        async with AI_AT_ONCE:
            return await ai.complete(system, request_text(piece))

    replies = await asyncio.gather(*(one(piece) for piece in chunks(text)))
    merged = merge_lists([clean_reply(r.text) for r in replies])
    return merged, any(r.cut for r in replies)


PREVIEW_LINES = 15


async def show_ai_list(
    interaction: discord.Interaction,
    campaign: Campaign,
    upload: Upload,
    listed: str,
    cut: bool,
    *,
    secrets: bool,
) -> None:
    """The AI's list, in full as a file and the start in the message, with Add and Cancel.
    Nothing is saved until the DM presses Add."""
    parsed = parse(listed, secrets=secrets)
    if not parsed.lines:
        await interaction.edit_original_response(
            content=f"🤖 DMbot's AI found no names in {_md(upload.label)}. Nothing was added."
        )
        return
    lines = [line for line in listed.splitlines() if line.strip()]
    shown = "\n".join(logic.shorten(line, 90) for line in lines[:PREVIEW_LINES]).replace("`", "'")
    extra = len(lines) - PREVIEW_LINES
    count = len(parsed.lines)
    hidden = sum(1 for line in parsed.lines if line.secrets)
    notes = []
    if hidden:
        notes.append(f"Includes {hidden} with secret names (only you see them).")
    if cut:
        notes.append("The document was long, so the list may be missing some names.")
    text = (
        f"🤖 **DMbot's AI found {count} name{'' if count == 1 else 's'} in "
        f"{_md(upload.label)}.** It only lists names written there, but it can miss some or "
        "get a kind wrong. Nothing is added until you press **Add these names**. To change "
        f"many, edit the attached file and add it with {UPLOAD}; or add them and fix "
        "single names on their cards (or **Undo**)."
        + (" " + " ".join(notes) if notes else "")
        + f"\n```\n{shown}\n```"
        + (f"… and {extra} more in the file." if extra > 0 else "")
    )
    body = (
        header(secrets=secrets)
        + f"###\n### Made by DMbot's AI from {' '.join(upload.label.split())}. Check it, then "
        "add it with Add many > Upload a file.\n" + "\n".join(lines) + "\n"
    )
    view = AIPreview(campaign.id, listed, count)
    await interaction.edit_original_response(
        content=text[:2000],
        attachments=[_file(body, f"names-from-{_slug(upload.label)}.txt")],
        view=view,
        allowed_mentions=NO_PINGS,
    )
    view.origin = interaction


class AIPreview(_Menu):
    def __init__(self, campaign_id: str, listed: str, count: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.listed = listed
        label = f"✅ Add these {count} names" if count != 1 else "✅ Add this name"
        self.add_item(_Button(self._add, label=label, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def _add(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=None)
        await import_list(interaction, self.campaign_id, self.listed)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. Nothing was added.", view=None, attachments=[]
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
    # Same names fold in, near names are asked about (#369). For anyone but the campaign's
    # DMs, a clash with a secret name looks exactly like no clash: they never learn one
    # exists.
    # Pure CPU, bounded but up to a second or so on a big list: off the event loop.
    p = await asyncio.to_thread(plan, parsed.lines, names, secrets=secrets_ok)
    new = p.new
    room = MAX_NAMES - len(names.entities)
    if len(new) > room:
        await _tell(
            interaction,
            f"Nothing was added: this campaign has room for {max(room, 0):,} more names (up to "
            f"{MAX_NAMES:,}). Split the list and add part of it.",
        )
        return
    batch = None
    saved: list[str | None] = []
    if new or p.more:
        try:
            written = await memory.add_names(
                campaign.guild_id, campaign.id, new, source=DM, secret_clashes=secrets_ok,
                more=p.more,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _tell(interaction, f"Nothing was added. {exc}")
            return
        except Exception:
            log.exception("Adding a list of names failed")
            await _tell(interaction, "Nothing was added: something went wrong. Try again.")
            return
        batch = written.batch
        saved = written.value[: len(new)]
        # What was really added; for anyone but the DMs, what was asked for, since the
        # store quietly skips a secret name the entry has: no hint one exists.
        enriched = (
            sum(1 for i in written.value[len(new) :] if i is not None)
            if secrets_ok
            else len(p.more)
        )
        changed(interaction, campaign)
    else:
        enriched = 0
    added = sum(1 for i in saved if i is not None)
    # Saved by someone else at the same moment: counted as already known.
    known = p.known + len(new) - added
    near = [
        n
        for n in p.near
        if saved[n.position] is not None
        and (n.like_position is None or saved[n.like_position] is not None)
    ]
    asked = {n.position for n in p.near}  # every planned one: not counted in `look`
    look = p.look - sum(
        1
        for i, (n, got) in enumerate(zip(new, saved, strict=True))
        if got is None and n.status == PROPOSED and i not in asked
    )
    kinds = {
        word: ids
        for word, positions in p.groups.items()
        if (ids := [i for pos in positions if (i := saved[pos]) is not None])
    }
    counts = Counts(added, look, known, p.dropped, enriched, p.swapped, p.repeated)
    await _send_summary(
        interaction, campaign, parsed, counts, batch, kinds, near, p.kinds, memory.keep_days
    )
    if near:
        await NearQuestions.send(interaction, campaign.id, near, saved)
    if p.kinds:
        await KindDiffQuestions.send(interaction, campaign.id, p.kinds)


@dataclass(frozen=True, slots=True)
class Counts:
    added: int
    look: int
    known: int
    dropped: int
    enriched: int
    swapped: list[tuple[str, str]]
    repeated: int  # lines that named a name earlier in the list by another of its names


async def _send_summary(
    interaction: discord.Interaction,
    campaign: Campaign,
    parsed: Parsed,
    c: Counts,
    batch: int | None,
    kinds: dict[str, list[str]],
    near: list[Near],
    differ: list[KindDiffers],
    undo_days: int,
) -> None:
    lines = [
        summary_text(
            c.added, c.look, c.known, parsed.repeated + c.repeated, c.dropped, parsed.refused,
            enriched=c.enriched, near=len(near), kinds=len(differ), swapped=c.swapped,
            undo_days=undo_days,
        )
    ]  # fmt: skip
    asked = sorted(kinds.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:KIND_QUESTIONS]
    if asked:
        words = ", ".join(
            f"**{_md(w)}** ({len(ids)})" if w else f"no kind ({len(ids)})" for w, ids in asked
        )
        lines.append(
            f"❓ **What kind are these?** {words}. Pick one in each menu below to set them all "
            "at once, or leave them for 📝 Check new names."
        )
        if len(kinds) > len(asked):
            lines.append(f"{len(kinds) - len(asked)} more wait in 📝 Check new names.")
    # The buttons keep working after a restart; the kind menus while DMbot keeps running.
    view = KindQuestions(campaign.id, asked)
    if c.look or near:
        view.add_item(ReviewButton(campaign.id))
    if batch is not None and (c.added or c.enriched):
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
    *,
    undo_days: int = 30,
    enriched: int = 0,
    near: int = 0,
    kinds: int = 0,
    swapped: Sequence[tuple[str, str]] = (),
) -> str:
    """What happened, what needs doing first. `enriched`: known names that got new other
    names; `near` and `kinds`: questions sent below; `swapped`: (other name, main name)
    for lines that gave a known name's other name as the name (#369)."""

    def n(count: int, word: str) -> str:
        return f"{count:,} {word}{'' if count == 1 else 's'}"

    head = f"Added {n(added, 'name')}" if added else "No new names were added"
    parts: list[str] = []
    if known:
        parts.append(
            f"{known:,} already known"
            + (f" ({enriched:,} got new other names)" if enriched else "")
        )
    if near:
        parts.append(f"{near:,} look like known names" if near > 1 else "1 looks like a known name")
    if kinds:
        parts.append(f"{kinds:,} kinds differ" if kinds > 1 else "1 kind differs")
    if repeated:
        parts.append(f"{repeated:,} listed twice")
    below = " Questions below." if near or kinds else ""
    lines = [f"📥 **{head}** · " + " · ".join(parts) + "." + below if parts else f"📥 **{head}.**"]
    for other, main in swapped[:SHOWN_SWAPPED]:
        lines.append(
            f"• **{_short(other)}** is already another name for **{_short(main)}**. "
            "Nothing changed."
        )
    if len(swapped) > SHOWN_SWAPPED:
        lines.append(f"• … and {len(swapped) - SHOWN_SWAPPED} more like that.")
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
            f"{'it' if look == 1 else 'them'}** (no kind, or {they} like a known name when said "
            "out loud). Press 📝 Check new names."
        )
    if dropped:
        lines.append(f"Left out: {n(dropped, 'other name')} already used by another name.")
    if added or enriched:
        lines.append(
            f"Wrong list? Press **Undo** to take it all back. Undo stops working after "
            f"{days(undo_days)}, or once you check or change any of these names, or one is "
            "said in a session."
        )
        # After Undo, since fixing one name ends Undo for the list (#353 review).
        lines.append("Only one name wrong? Fix or remove it with 🔍 Find a name in `/dmbot names`.")
    return _fit(lines)


KIND_QUESTIONS = 4  # one menu per unknown kind word; the 5th row holds the buttons


class KindSelect(discord.ui.Select["KindQuestions"]):
    def __init__(self, questions: KindQuestions, word: str, ids: list[str], row: int) -> None:
        n = len(ids)
        question = (
            f"What is every “{logic.shorten(word, 40)}”? ({n} name{'' if n == 1 else 's'})"
            if word
            else f"What {'is the name' if n == 1 else f'are the {n} names'} with no kind?"
        )
        super().__init__(
            placeholder=logic.shorten(question, 150),
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


# ---- after 📥 Add many: names that look like known ones, kinds that differ (#369) -------

QUESTION_ROWS = 4  # one question per row; the 5th row holds the "for all" buttons
SAVING = "\n⏳ Saving…"
SHORT = 40  # a name in a question line: long ones are cut, so the message always fits
ENDS_UNDO = "Wrong list? Press **Undo** on the message above first: answering here ends Undo."
WENT_WRONG = "something went wrong, so it was left as it is."


def _short(name: str) -> str:
    return _md(logic.shorten(name, SHORT))


def _fit(lines: list[str], limit: int = 1990) -> str:
    """At most `limit` characters (under Discord's 2,000), cut between lines, never
    inside one; the "…" line that says so counts too."""
    out: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit - 2:
            out.append("…")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


class _Questions(discord.ui.View):
    """Questions under the summary, answered while DMbot keeps running. Each answer saves
    with the buttons taken away (no second press), and the message always comes back."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=60 * 60)
        self.campaign_id = campaign_id
        self.busy = False
        self.answered_any = False

    def text(self) -> str:
        raise NotImplementedError

    def build(self) -> None:
        raise NotImplementedError

    def done(self) -> bool:
        raise NotImplementedError

    async def run(self, interaction: discord.Interaction, work: Any) -> None:
        if self.busy:  # pressed again while saving
            await interaction.response.defer()
            return
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.busy = True
        try:
            await interaction.response.edit_message(
                content=_fit(self.text().split("\n"), 1990 - len(SAVING)) + SAVING, view=None,
                allowed_mentions=NO_PINGS,
            )  # fmt: skip
            await work(interaction, campaign, memory)
            changed(interaction, campaign)
        finally:
            self.busy = False
            self.answered_any = True
            self.build()
            if self.done():
                self.stop()
            await interaction.edit_original_response(
                content=self.text(), view=None if self.done() else self,
                allowed_mentions=NO_PINGS,
            )  # fmt: skip


@dataclass(slots=True)
class _NearItem:
    entity_id: str  # the new name, saved as a suggestion
    name: str
    like: str
    like_id: str  # the known name's entry
    answer: str = ""  # what happened, once answered


class NearQuestions(_Questions):
    """ "Aurill → Auril?": the same name, different, or remove it, four at a time.
    Unanswered ones wait in 📝 Check new names (which offers Same as…), so nothing is
    lost. DMbot never joins names by sound on its own."""

    def __init__(self, campaign_id: str, items: list[_NearItem]) -> None:
        super().__init__(campaign_id)
        self.items = items
        self.page = 0
        self.build()

    @classmethod
    async def send(
        cls,
        interaction: discord.Interaction,
        campaign_id: str,
        near: list[Near],
        saved: list[str | None],
    ) -> None:
        items = []
        for n in near:
            new_id, like_id = saved[n.position], n.entity_id
            if like_id is None and n.like_position is not None:
                like_id = saved[n.like_position]
            if new_id is not None and like_id is not None:
                items.append(_NearItem(new_id, n.name, n.like, like_id))
        if not items:
            return
        view = cls(campaign_id, items)
        await interaction.followup.send(
            view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )

    def pending(self) -> list[int]:
        return [i for i, item in enumerate(self.items) if not item.answer]

    def done(self) -> bool:
        return not self.pending()

    def _shown(self) -> list[int]:
        start = self.page * QUESTION_ROWS
        return self.pending()[start : start + QUESTION_ROWS]

    def text(self) -> str:
        count = len(self.items)
        lines = [
            f"🔎 **{count} names look like names DMbot already knows.**"
            if count > 1
            else "🔎 **1 name looks like a name DMbot already knows.**",
            "**Same** makes it another name for the known one. **Different** keeps both. "
            "**Remove** drops it: DMbot forgets that spelling.",
        ]
        if not self.answered_any:
            lines.append(ENDS_UNDO)
        answered = [i for i, item in enumerate(self.items) if item.answer]
        if len(answered) > 6:
            lines.append(f"✅ {len(answered)} answered.")
        else:
            lines += [
                f"{i + 1}. **{_short(self.items[i].name)}**: {self.items[i].answer}"
                for i in answered
            ]
        shown = self._shown()
        lines += [
            f"{i + 1}. **{_short(self.items[i].name)}** → **{_short(self.items[i].like)}**?"
            for i in shown
        ]
        pending = self.pending()
        if not pending:
            lines.append("All checked.")
        else:
            if len(pending) > len(shown):
                lines.append(f"{len(pending) - len(shown)} more after these: press **More ▶**.")
            lines.append("Not now? They wait in 📝 Check new names.")
        return _fit(lines)

    def build(self) -> None:
        self.clear_items()
        for row, i in enumerate(self._shown()):
            for word, style, act in (
                ("Same", discord.ButtonStyle.primary, self._same),
                ("Different", discord.ButtonStyle.secondary, self._different),
                ("Remove", discord.ButtonStyle.secondary, self._remove),
            ):
                self.add_item(
                    _Button(self._answer(act, [i]), label=f"{i + 1}. {word}", style=style, row=row)
                )
        pending = self.pending()
        if len(pending) > 2:
            n = len(pending)
            self.add_item(
                _Button(
                    self._answer(self._same, pending), label=f"Same for all {n}", row=QUESTION_ROWS
                )
            )
            self.add_item(
                _Button(
                    self._answer(self._different, pending), label=f"Different for all {n}",
                    row=QUESTION_ROWS,
                )
            )  # fmt: skip
        if len(pending) > QUESTION_ROWS:
            self.add_item(_Button(self._more, label="More ▶", row=QUESTION_ROWS))

    def _answer(self, act: Any, which: list[int]) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            async def work(it: discord.Interaction, campaign: Campaign, memory: Any) -> None:
                for i in which:
                    item = self.items[i]
                    if item.answer:
                        continue
                    try:
                        await act(it, campaign, memory, item, len(which) == 1)
                    except Exception:
                        log.exception("Answering a name that looks like a known one failed")
                        item.answer = WENT_WRONG

            self.page = 0
            await self.run(interaction, work)
            if act == self._same and len(which) > 1:  # no Undo for a whole page of joins
                joined = sum(1 for i in which if self.items[i].answer.startswith("🔗"))
                if joined:
                    await interaction.followup.send(
                        f"Joined {joined} name{'' if joined == 1 else 's'}. Wrong? Open the known "
                        "name with 🔍 Find a name and use Edit other names to drop a spelling.",
                        ephemeral=True, allowed_mentions=NO_PINGS,
                    )  # fmt: skip

        return handler

    async def _same(
        self, it: discord.Interaction, campaign: Campaign, memory: Any, item: _NearItem, one: bool
    ) -> None:
        from dmbot.ui.name_card import UndoButton

        try:
            written = await memory.merge(
                campaign.guild_id, campaign.id, item.like_id, item.entity_id, source=DM,
                dm_said_same=True,
            )  # fmt: skip
            # The DM said so: its spelling is a confirmed other name now (as in the review).
            key = name_key(item.name)
            for alias in await memory.aliases(
                campaign.guild_id, campaign.id, entity_id=item.like_id
            ):
                if alias.key == key and alias.status != CONFIRMED:
                    await memory.update_alias(
                        campaign.guild_id, campaign.id, alias.id, status=CONFIRMED, source=DM
                    )
        except MemoryRuleError as exc:
            item.answer = f"couldn't join them. {exc}"
            return
        item.answer = f"🔗 now another name for **{_short(item.like)}**."
        if one and written.batch is not None:  # its own message, so Undo outlives this one
            view = discord.ui.View(timeout=None)
            view.add_item(UndoButton(campaign.id, item.entity_id, written.batch))
            await it.followup.send(
                f"Joined **{_short(item.name)}** to **{_short(item.like)}**. Wrong? **Undo** "
                "splits them again.",
                view=view, ephemeral=True, allowed_mentions=NO_PINGS,
            )  # fmt: skip

    async def _different(
        self, it: discord.Interaction, campaign: Campaign, memory: Any, item: _NearItem, one: bool
    ) -> None:
        entity = await memory.entity(campaign.guild_id, campaign.id, item.entity_id)
        if entity is None or entity.status != PROPOSED:
            item.answer = "changed by someone else meanwhile, so DMbot left it."
            return
        if entity.type == UNKNOWN_KIND:  # no kind yet: 📝 Check new names asks for it
            item.answer = "kept both. It waits in 📝 Check new names to say what it is."
            return
        try:
            await memory.set_entity_status(
                campaign.guild_id, campaign.id, item.entity_id, CONFIRMED, source=DM
            )
        except MemoryRuleError:
            item.answer = "changed by someone else meanwhile, so DMbot left it."
            return
        item.answer = "✅ kept both."

    async def _remove(
        self, it: discord.Interaction, campaign: Campaign, memory: Any, item: _NearItem, one: bool
    ) -> None:
        from dmbot.ui.name_card import UndoButton

        try:
            written = await memory.set_entity_status(
                campaign.guild_id, campaign.id, item.entity_id, REJECTED, source=DM
            )
        except MemoryRuleError:
            item.answer = "changed by someone else meanwhile, so DMbot left it."
            return
        item.answer = "🗑 removed."
        if written.batch is not None:  # its own message, so Undo outlives this one
            view = discord.ui.View(timeout=None)
            view.add_item(UndoButton(campaign.id, item.entity_id, written.batch))
            await it.followup.send(
                f"Removed **{_short(item.name)}**. Wrong? **Undo** brings it back.",
                view=view, ephemeral=True, allowed_mentions=NO_PINGS,
            )  # fmt: skip

    async def _more(self, interaction: discord.Interaction) -> None:
        pages = -(-len(self.pending()) // QUESTION_ROWS)
        self.page = (self.page + 1) % max(pages, 1)
        self.build()
        await interaction.response.edit_message(
            content=self.text(), view=self, allowed_mentions=NO_PINGS
        )


def _who(names: list[KindDiffers], shown: int = 3) -> str:
    out = ", ".join(f"**{_short(d.name)}**" for d in names[:shown])
    return out + (f" and {len(names) - shown} more" if len(names) > shown else "")


class KindDiffQuestions(_Questions):
    """ "Varrow: DMbot has god, your list says place." once per pair of kinds. DMbot
    keeps the kind it has unless the DM says otherwise."""

    def __init__(self, campaign_id: str, pairs: list[tuple[str, str, list[KindDiffers]]]) -> None:
        super().__init__(campaign_id)
        self.pairs = pairs[:QUESTION_ROWS]
        self.later = [d for _, _, names in pairs[QUESTION_ROWS:] for d in names]
        self.answers: dict[int, str] = {}
        self.build()

    @classmethod
    async def send(
        cls, interaction: discord.Interaction, campaign_id: str, differ: list[KindDiffers]
    ) -> None:
        grouped: dict[tuple[str, str], list[KindDiffers]] = {}
        for d in differ:
            grouped.setdefault((d.have, d.want), []).append(d)
        pairs = sorted(
            ((have, want, names) for (have, want), names in grouped.items()),
            key=lambda p: (-len(p[2]), p[0], p[1]),
        )
        view = cls(campaign_id, pairs)
        await interaction.followup.send(
            view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )

    def done(self) -> bool:
        return len(self.answers) == len(self.pairs)

    def text(self) -> str:
        count = sum(len(names) for _, _, names in self.pairs) + len(self.later)
        lines = [
            f"🏷️ **{count} names have a different kind in your list.**"
            if count > 1
            else "🏷️ **1 name has a different kind in your list.**",
            "DMbot kept the kind it already had. Change any? Leave this and nothing changes.",
        ]
        if not self.answered_any:
            lines.append(ENDS_UNDO)
        for i, (have, want, names) in enumerate(self.pairs):
            line = (
                f"{i + 1}. {_who(names)}: DMbot has {KIND_SHORT.get(have, have)}, your list "
                f"says {KIND_SHORT.get(want, want)}."
            )
            lines.append(line + (f" {self.answers[i]}" if i in self.answers else ""))
        if self.later:
            lines.append(
                f"Also kept as they are: {_who(self.later)}. To change one, open its card."
            )
        if self.done():
            lines.append("All checked.")
        return _fit(lines)

    def build(self) -> None:
        self.clear_items()
        for i, (have, want, _) in enumerate(self.pairs):
            if i in self.answers:
                continue
            keep = f"{i + 1}. Keep {KIND_SHORT.get(have, have)}"
            use = f"{i + 1}. Change to {KIND_SHORT.get(want, want)}"
            self.add_item(_Button(self._answer([i], False), label=keep[:80], row=i))
            self.add_item(
                _Button(
                    self._answer([i], True), label=use[:80], style=discord.ButtonStyle.primary,
                    row=i,
                )
            )  # fmt: skip
        left = [i for i in range(len(self.pairs)) if i not in self.answers]
        if len(left) > 1:
            self.add_item(_Button(self._answer(left, False), label="Keep all", row=QUESTION_ROWS))
            self.add_item(_Button(self._answer(left, True), label="Change all", row=QUESTION_ROWS))

    def _answer(self, which: list[int], change: bool) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            async def work(it: discord.Interaction, campaign: Campaign, memory: Any) -> None:
                for i in which:
                    if i in self.answers:
                        continue
                    have, want, names = self.pairs[i]
                    if not change:
                        self.answers[i] = f"✅ Kept {KIND_SHORT.get(have, have)}."
                        continue
                    done = 0
                    try:
                        for d in names:
                            try:
                                await memory.set_entity_type(
                                    campaign.guild_id, campaign.id, d.entity_id, want, source=DM
                                )
                            except MemoryRuleError:
                                continue  # changed or removed since
                            done += 1
                    except Exception:
                        log.exception("Changing kinds after a names list failed")
                    self.answers[i] = f"✅ Changed {done} to {KIND_SHORT.get(want, want)}." + (
                        "" if done == len(names) else " (The rest were changed or removed already.)"
                    )

            await self.run(interaction, work)

        return handler


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
        except TooLateToUndo as exc:
            await _tell(
                interaction, f"{exc} Remove the wrong names from their cards (`/dmbot names`)."
            )
            return
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
        f"it again with {UPLOAD} (names DMbot already knows aren't added twice; they get any "
        "new other names).",
        file=_file(text, f"names-{_slug(campaign.name)}-{day}.txt"),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )
