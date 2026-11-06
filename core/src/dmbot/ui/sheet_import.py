"""A player's own sheet document, read into their character (#1125; dmbot.memory.sheet_import).

On the 📜 panel a player without a D&D Beyond sheet can give DMbot **a public document link**
(Google Docs or Drive, or a PDF address) or **upload a file** (Word, PDF or text, at most 5 MB).
The text is read out and the file thrown away: nothing but the checked snapshot is kept, nothing
of the document goes in a log, a transcript or another campaign. The AI reads it once per import
(CAREFUL tier), then the import status card says what was read and what to look at.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, cast

import discord

from dmbot import fetch
from dmbot.ai import AIError
from dmbot.memory import sheet_import
from dmbot.memory.document_reader import READING, RESTARTING, TOO_SLOW, read_document
from dmbot.memory.name_documents import (
    NO_TEXT,
    SCANNED_PDF,
    TYPES_HELP,
    UNREADABLE,
    DocumentError,
)
from dmbot.memory.sheet_store import PlayerCharacter, SheetRefused
from dmbot.ui import sheets as ui
from dmbot.ui.dmbot_commands import VIEW_TIMEOUT_S, _answer_first, _failed, _tell

log = logging.getLogger(__name__)

NO_AI = "Reading documents isn't available here yet. Press **Type in my character** instead."
WAIT = "Please wait 30 seconds, then press the button again."
BUSY = "DMbot is busy reading another sheet. Try again in a minute."
DAILY = (
    "This server has used up today's document reads. Try again tomorrow, or press **Type in "
    "my character**."
)
TOO_BIG = "That file is over 5 MB. Save a smaller copy, or press **Type in my character**."
WRONG_TYPE = (
    "DMbot reads Word (.docx), PDF and text (.txt) files. Save your sheet as one of those and "
    "try again."
)
NO_WORDS = (
    "DMbot found no words in that file. Check it's the right one, or press **Type in my "
    "character**."
)
CANT_READ = (
    "DMbot couldn't read that. Save your sheet as Word (.docx), PDF or text (.txt) and try "
    "again, or press **Type in my character**."
)
# The names screen's wording points at its own buttons ("📋 Paste a list") and a 10 MB limit.
# A sheet's refusals say what to do on *this* panel, ending with a next step.
_UPLOAD = "upload the file with **Upload my sheet file**"
_TYPE_IT = "or press **Type in my character**"
SHEET_WORDS = {
    fetch.NOT_A_LINK: (
        "That doesn't look like a link. Copy the whole link to your sheet and paste it "
        f"here, {_TYPE_IT}."
    ),
    fetch.ONLY_HTTPS: (
        "DMbot can only open links that start with https://. Try the same link with "
        f"https://, or {_UPLOAD}."
    ),
    fetch.NOT_PUBLIC: f"DMbot can't open that link. Use a link anyone can open, or {_UPLOAD}.",
    fetch.NOT_SHARED: (
        "DMbot can't open that link: it may be private or mistyped. If it's a Google Doc, press "
        f'Share and set General access to "Anyone with the link", then try again. Or {_UPLOAD}.'
    ),
    fetch.DRIVE_PAGE: (
        "DMbot can't open that Google Drive file: it may be private (Share > \"Anyone with the "
        f'link") or too big to share by link. Download it and {_UPLOAD}.'
    ),
    fetch.TOO_BIG: "That's over 5 MB. Save a smaller copy, " + _TYPE_IT + ".",
    fetch.WRONG_TYPE: (
        "DMbot reads web pages and PDF, Word or text files. That link is something else. "
        f"Save your sheet as one of those and {_UPLOAD}."
    ),
    fetch.UNREACHABLE: fetch.UNREACHABLE,  # (no button named in it)
    TYPES_HELP: WRONG_TYPE,
    UNREADABLE: UNREADABLE,
    NO_TEXT: NO_WORDS,
    SCANNED_PDF: sheet_import.PICTURES,
    TOO_SLOW: "That took too long to read. Try a smaller file, " + _TYPE_IT + ".",
    RESTARTING: RESTARTING,
}
NO_FILE = "No file came through. Press **Upload my sheet file** and choose one before sending."
TOO_LONG = "That took too long, so nothing was saved. Try again, or press **Type in my character**."
GENERIC = (
    "Something went wrong and nothing was saved. Try again, or press **Type in my character**."
)
READ_OK = (
    "✅ Read **{name}** from {where}. DMbot kept the names and numbers, not the file. Check "
    "the card below, since it may have misread something."
)
SHORTENED = " Only the first part of a long document was read."
COOLDOWN_S = 30  # per person
READS_PER_DAY = 20  # per server, for the operator's bill (bring-your-own keys: #50)
TIME_LIMIT_S = 240  # well inside Discord's 15 minutes to answer
FILE_TYPES = (".docx", ".pdf", ".txt", ".md", ".text")

_last: dict[tuple[int, int], float] = {}
_busy: set[int] = set()  # servers with a sheet being read right now
_reads: dict[tuple[int, str], int] = {}  # (server, day) → AI reads


class DocumentLinkForm(discord.ui.Modal, title="Read my sheet from a link"):
    link: discord.ui.Label[DocumentLinkForm] = discord.ui.Label(
        text="Link to your sheet",
        description="Anyone with the link must be able to open it. DMbot keeps no document.",
        component=discord.ui.TextInput(
            placeholder="https://docs.google.com/document/d/…", max_length=400
        ),
    )

    def __init__(self, guild_id: int, character: PlayerCharacter) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character = guild_id, character

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        field = cast(discord.ui.TextInput[DocumentLinkForm], self.link.component)
        await import_sheet(interaction, self.guild_id, self.character, link=field.value.strip())


class DocumentFileForm(discord.ui.Modal, title="Upload your sheet"):
    file: discord.ui.Label[DocumentFileForm] = discord.ui.Label(
        text="Your sheet file (Word, PDF or text)",  # 45 characters at most
        description="Up to 5 MB. DMbot reads it once and doesn't keep the file.",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, guild_id: int, character: PlayerCharacter) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character = guild_id, character

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        upload = cast(discord.ui.FileUpload[DocumentFileForm], self.file.component)
        if not upload.values:
            await _tell(interaction, NO_FILE)
            return
        await import_sheet(interaction, self.guild_id, self.character, attachment=upload.values[0])


def _refusal_for_file(file: discord.Attachment) -> str | None:
    if file.size > sheet_import.FILE_MAX_BYTES:
        return TOO_BIG
    if PurePath(file.filename).suffix.casefold() not in FILE_TYPES:
        return WRONG_TYPE
    return None


async def _text_of(
    *, link: str | None, attachment: discord.Attachment | None
) -> tuple[str | None, str | None]:
    """(text, a problem in plain words). The file's bytes live only in this call; nothing
    of them is logged."""
    try:
        if attachment is not None:
            problem = _refusal_for_file(attachment)
            if problem is not None:
                return None, problem
            try:
                raw = await attachment.read()
            except discord.HTTPException:
                return None, "DMbot couldn't download that file. Try again."
            filename = attachment.filename
        else:
            got = await fetch.fetch(link or "")
            raw, filename = got.data, got.filename
            if len(raw) > sheet_import.FILE_MAX_BYTES:
                return None, TOO_BIG
        async with READING:  # the same limit as the names screen
            return await read_document(filename, raw), None
    except (fetch.LinkError, DocumentError) as exc:
        return None, SHEET_WORDS.get(str(exc), CANT_READ)


def _give_back(guild_id: int, day: str) -> None:
    """A read that never reached the AI doesn't count against the day. (Another server's import
    may already have cleared yesterday's key just after midnight.)"""
    key = (guild_id, day)
    _reads[key] = max(0, _reads.get(key, 0) - 1)


async def import_sheet(
    interaction: discord.Interaction,
    guild_id: int,
    character: PlayerCharacter,
    *,
    link: str | None = None,
    attachment: discord.Attachment | None = None,
) -> None:
    """Read a player's document into their character's sheet: checked, then the AI (once),
    then saved as a typed sheet with the import status card. Every refusal is one plain line
    and saves nothing."""
    bot = ui._bot(interaction)
    ai = bot.sheet_ai
    if ai is None:
        await _tell(interaction, NO_AI)
        return
    user = interaction.user.id
    if attachment is not None:
        problem = _refusal_for_file(attachment)
        if problem is not None:  # before downloading anything
            await _tell(interaction, problem)
            return
    await _answer_first(interaction)  # the lookups below can pass Discord's 3 seconds
    campaign = await bot.campaigns.get(guild_id, character.campaign_id)
    if campaign is None:
        await _tell(interaction, ui.NOT_YOURS)
        return
    refused = await bot.plan_gate("ai", guild_id, campaign, user)
    if refused is not None:
        await _tell(interaction, refused)
        return
    day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
    # From here to `_busy.add` there is no await: two imports for one server can't both pass
    # (the server's one-at-a-time rule and its daily cap would both be broken). The person's
    # wait starts only now, so a wrong file or a busy server doesn't cost them 30 seconds.
    if guild_id in _busy:
        await _tell(interaction, BUSY)
        return
    if _reads.get((guild_id, day), 0) >= READS_PER_DAY:
        await _tell(interaction, DAILY)
        return
    if ui._too_soon(_last, (guild_id, user), COOLDOWN_S):
        await _tell(interaction, WAIT)
        return
    _busy.add(guild_id)
    for old in [k for k in _reads if k[1] != day]:
        del _reads[old]  # only today counts
    _reads[(guild_id, day)] = _reads.get((guild_id, day), 0) + 1
    try:
        if not await ui._still_member(interaction, guild_id):
            _give_back(guild_id, day)
            return
        text, problem = await _text_of(link=link, attachment=attachment)
        if text is None:
            _give_back(guild_id, day)  # nothing was sent to the AI: no read used
            await _tell(interaction, problem or "DMbot couldn't read that.")
            return
        shortened = len(text) > sheet_import.TEXT_MAX
        try:
            async with asyncio.timeout(TIME_LIMIT_S):
                snapshot = await sheet_import.read_character(ai, text)
        except AIError as exc:
            await _tell(interaction, str(exc))
            return
        except TimeoutError:
            await _tell(interaction, TOO_LONG)
            return
        if snapshot is None:
            await _tell(interaction, sheet_import.NOTHING_FOUND)
            return
        await _keep(interaction, guild_id, character, snapshot, link is not None, shortened)
    except Exception:
        log.exception("Reading a sheet document failed")  # no text: the cause only
        await _tell(interaction, GENERIC)
    finally:
        _busy.discard(guild_id)


async def _keep(
    interaction: discord.Interaction,
    guild_id: int,
    character: PlayerCharacter,
    snapshot: dict[str, Any],
    from_link: bool,
    shortened: bool = False,
) -> None:
    store = ui._store(interaction)
    assert store is not None
    c = character
    before = await store.sheet(guild_id, c.campaign_id, c.entity_id, player=interaction.user.id)
    try:
        await store.save(
            guild_id,
            c.campaign_id,
            c.entity_id,
            snapshot,
            int(time.time()),
            player=interaction.user.id,
        )
    except SheetRefused:
        await _tell(interaction, ui.NOT_YOURS)
        return
    ui._bot(interaction).sheets_changed(guild_id, c.campaign_id)
    log.info("A sheet document was read: guild=%s campaign=%s", guild_id, c.campaign_id)
    said = READ_OK.format(
        name=ui._md(str(snapshot["name"])), where="your link" if from_link else "your file"
    )
    if shortened:
        said += SHORTENED
    if before is not None and before.url is not None:
        said += ui.TYPED_REPLACED
    await ui.show_panel(interaction, guild_id, c, note=said)
    card = await ui.status_card(ui._bot(interaction), guild_id, c.campaign_id, snapshot)
    await ui._send_card(interaction, [card])
