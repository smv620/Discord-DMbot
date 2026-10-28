"""Sending a campaign's house-rules file (#969): `house-rules-<campaign>.txt`, privately.

After every change a DM makes in Discord (Add, Edit, Remove, an Override, a rule said or
typed to DMbot) the DM gets the updated file, to put wherever the table keeps its rules.
The 📥 **Download** button on the `/dmbot houserules` list gives anyone in the server the
same file: they may already read the list. The file holds this campaign's house rules and
no one else's. The format is `dmbot.rules.house_file`.

Sending the file is never worth failing the change for: a problem is logged and skipped.
"""

from __future__ import annotations

import contextlib
import io
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.name_documents import MAX_DOCUMENT_BYTES, DocumentError
from dmbot.mentions import NO_PINGS
from dmbot.rules import house_ai, house_file, house_reader
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _send, _tell
from dmbot.ui.name_background import message_is_gone
from dmbot.waiting import WaitingMessage

log = logging.getLogger(__name__)

AFTER_CHANGE = "Put this in your house-rules file so everyone can see it."
DOWNLOAD_LABEL = "📥 Download rules"
DOWNLOAD_NOTE = (
    "Your table's house rules, as a text file. Only you can see this message. "
    "Save it anywhere you like."
)
COULDNT = "Couldn't make the file just now. Try again in a moment."


async def build(interaction: discord.Interaction, campaign: Campaign) -> discord.File | None:
    """The file for this campaign's current rules, or None if they can't be read."""
    store = _bot(interaction).house_rules
    if store is None:
        return None
    try:
        rules = await store.list(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't read the house rules for the file")
        return None
    data = house_file.write(campaign.name, rules).encode()
    return discord.File(io.BytesIO(data), filename=house_file.filename(campaign.name))


async def send_after_change(
    interaction: discord.Interaction, campaign: Campaign, change: str
) -> None:
    """The updated file, privately, after a change. `change` says what happened ("Added
    house rule 12."). Never raises."""
    try:
        file = await build(interaction, campaign)
        if file is None:
            return
        await interaction.followup.send(
            f"{change} {AFTER_CHANGE}", file=file, ephemeral=True, allowed_mentions=NO_PINGS
        )
    except Exception:
        log.warning("Couldn't send the house-rules file", exc_info=True)


async def send_download(interaction: discord.Interaction, campaign: Campaign) -> None:
    """The 📥 Download button: the file as it is now, privately, for anyone."""
    file = await build(interaction, campaign)
    if file is None:
        await interaction.followup.send(COULDNT, ephemeral=True)
        return
    try:
        await interaction.followup.send(
            DOWNLOAD_NOTE, file=file, ephemeral=True, allowed_mentions=NO_PINGS
        )
    except discord.HTTPException:
        log.warning("Couldn't send the house-rules file", exc_info=True)
        await interaction.followup.send(COULDNT, ephemeral=True)


# ---- the linked file and the upload (#969, part 2) -----------------------------------

FILE_LABEL = "📄 House-rules file"
LINK_LABEL = "🔗 Connect a file"
CHANGE_LABEL = "🔗 Use another file"
CHECK_LABEL = "🔄 Compare now"
UNLINK_LABEL = "Stop using this file"
UPLOAD_LABEL = "📎 Upload a file once"
ONLY_DMS = "Only this campaign's DMs can set up the house-rules file. Ask your DM."
PICK_FIRST = (
    "Which campaign is this file for? Open `/dmbot houserules`, pick the campaign, then press "
    "**📄 House-rules file**."
)
GONE = "That campaign isn't here any more. Use `/dmbot houserules` to start again."
NOT_READY = "The house-rules file isn't available right now. Please try again in a moment."
NOT_LINKED = (
    "📄 **House-rules file**\nKeep your house rules in a PDF, Word, Excel or text file, a Google "
    "Doc or a web page. DMbot reads it at the start of each session and shows you what's new.\n"
    "**Connect a file** keeps it up to date. For a Google Doc or web page, set sharing to "
    "“Anyone with the link can view” first.\n**Upload a file** reads it once and doesn't keep "
    "it.\nNothing is saved until you press a button.\n"
    "Your own house rules only. For a published book, use Share a rulebook."
)


def linked_text(site: str) -> str:
    return (
        f"📄 **House-rules file**\nConnected: **{discord.utils.escape_markdown(site)}**. DMbot "
        "compares it with your house rules every time you start a session, and tells you what "
        "differs. Nothing changes unless you press a button."
    )


def _links(interaction: discord.Interaction) -> Any:
    return getattr(_bot(interaction), "house_file_links", None)


async def _dm_campaign(interaction: discord.Interaction, campaign: Campaign) -> Campaign | None:
    """The campaign as it is now, if this person is one of its DMs; else they are told."""
    current = await _bot(interaction).campaigns.get(campaign.guild_id, campaign.id)
    if current is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in current.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return None
    return current


READING = "📄 Reading your house-rules file…"
STEP_READING = "Step 1 of 2: getting the file and reading its words"
STALL_AFTER_S = 90.0  # one AI call; nothing changing for this long is worth saying
DONE_READING = "Finished. What it found, if anything, is below."
FAILED_READING = "Stopped: DMbot couldn't finish reading the file. Nothing was changed. Try again."
UNREADABLE = "Stopped: that file couldn't be read. Nothing was changed. The reason is below."


class Reading:
    """The DM's waiting message and the line that ends it. The caller sets `closing` when it knows
    the read went another way than the neutral default."""

    def __init__(self, waiting: WaitingMessage) -> None:
        self.waiting = waiting
        self.closing = DONE_READING

    def step(self, words: str, *, keeps: bool = True) -> None:
        self.waiting.step(words, keeps=keeps)


@contextlib.asynccontextmanager
async def _waiting(interaction: discord.Interaction) -> AsyncIterator[Reading]:
    """The DM's wait for a file read (#2564): one private message edited in place with the step,
    how long it has been, and a stall line (that says when DMbot stops by itself), ended with a
    plain result. Fixed words only: nothing of the file can reach it. If the message can't be sent
    the read goes on without one."""
    clicked = time.monotonic()
    message: discord.WebhookMessage | None = None

    async def show(text: str) -> None:
        if message is not None:
            await message.edit(content=text, allowed_mentions=NO_PINGS)

    waiting = WaitingMessage(
        show,
        READING,
        stall_after_s=STALL_AFTER_S,
        stops_at_s=house_ai.TIMEOUT_S + 30,  # the one AI call's own limit, and the reading before
        gone=message_is_gone,
        started=clicked,
    )
    waiting.step(STEP_READING)
    try:
        message = await interaction.followup.send(
            waiting.text(), ephemeral=True, wait=True, allowed_mentions=NO_PINGS
        )
    except discord.HTTPException:
        log.warning("Couldn't send the house-rules waiting message", exc_info=True)
    reading = Reading(waiting)
    try:
        async with waiting:
            yield reading
    except BaseException:
        reading.closing = FAILED_READING
        raise
    finally:
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(content=reading.closing, allowed_mentions=NO_PINGS)


def _private_send(interaction: discord.Interaction) -> Any:
    """Offer the comparison as a private message (the same buttons as on the DM screen)."""

    async def send(text: str, view: discord.ui.View) -> bool:
        try:
            if view.children:
                await interaction.followup.send(
                    text, view=view, ephemeral=True, allowed_mentions=NO_PINGS
                )
            else:
                await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.warning("Couldn't send the house-rules comparison", exc_info=True)
            return False
        return True

    return send


async def open_menu(interaction: discord.Interaction, campaign: Campaign) -> None:
    """📄 House-rules file: what is linked, and the ways to change it. The campaign's DMs
    only; the answer is private."""
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    if current is None:
        return
    links = _links(interaction)
    if links is None:
        await _tell(interaction, NOT_READY)
        return
    link = await links.get(current.guild_id, current.id)
    text = linked_text(link.site) if link is not None else NOT_LINKED
    await _send(interaction, text, FileMenu(current, linked=link is not None))


MAX_UPLOAD_BYTES = MAX_DOCUMENT_BYTES  # 10 MB, as for a names file
COOLDOWN_S = 30.0  # between two comparisons of one campaign's file asked for by hand
_busy: set[tuple[int, str]] = set()  # campaigns being compared right now
_last: dict[tuple[int, str], float] = {}  # when each was last compared by hand
TOO_OFTEN = "I just compared that file. Try again in a few seconds."
ALREADY = "I'm already comparing that file. One moment."


def _take(campaign: Campaign) -> str | None:
    """Words if this campaign's file can't be compared by hand right now (it is being
    compared, or was a moment ago); else None, and the turn is taken."""
    key = (campaign.guild_id, campaign.id)
    if key in _busy:
        return ALREADY
    now = time.monotonic()
    if now - _last.get(key, -COOLDOWN_S) < COOLDOWN_S:
        return TOO_OFTEN
    _busy.add(key)
    _last[key] = now
    if len(_last) > 500:  # strangers can't reach this, but keep it small anyway
        for old in [k for k, at in _last.items() if now - at > COOLDOWN_S]:
            del _last[old]
    return None


def _done(campaign: Campaign) -> None:
    _busy.discard((campaign.guild_id, campaign.id))


async def check_now(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Read the linked file now and offer what differs, privately."""
    from dmbot.dm_screen import house_sync

    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    if current is None:
        return
    wait = _take(current)
    if wait is not None:
        await _tell(interaction, wait)
        return
    try:
        bot = _bot(interaction)
        async with _waiting(interaction) as waiting:
            await house_sync.check_linked(
                bot,
                current,
                _private_send(interaction),
                quiet_if_same=False,
                progress=lambda words: waiting.step(words, keeps=False),
            )
    finally:
        _done(current)


async def link_file(interaction: discord.Interaction, campaign: Campaign, link: str) -> None:
    """Link the file (the campaign's DMs only), then check it at once."""
    from dmbot.rules.house import HouseRuleError

    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    links = _links(interaction)
    if current is None:
        return
    if links is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        saved = await links.set_link(current.guild_id, current.id, interaction.user.id, link)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await _tell(
        interaction,
        f"✅ Connected: **{discord.utils.escape_markdown(saved.site)}**. DMbot will compare it "
        "with your house rules every time you start a session.",
    )
    await check_now(interaction, current)


class LinkForm(discord.ui.Modal, title="Connect your house-rules file"):
    link: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="Link to your rules (Doc, PDF, web page)",  # 45 characters at most
        placeholder="Paste the link. Sharing must be “Anyone with the link”.",
        max_length=2000,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await link_file(interaction, self.campaign, self.link.value)


class UploadForm(discord.ui.Modal, title="Read a house-rules file"):
    file: discord.ui.Label[UploadForm] = discord.ui.Label(
        text="Your rules (PDF, Word, Excel, text)",  # 45 characters at most
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from dmbot.dm_screen import house_sync

        await _answer_first(interaction)
        current = await _dm_campaign(interaction, self.campaign)  # before reading anything
        if current is None:
            return
        upload = cast(discord.ui.FileUpload[UploadForm], self.file.component)
        if not upload.values:
            await _tell(interaction, f"No file was added. Press {UPLOAD_LABEL} and pick one.")
            return
        attachment = upload.values[0]
        if attachment.size > MAX_UPLOAD_BYTES:
            await _tell(interaction, house_sync.TOO_BIG)
            return
        wait = _take(current)
        if wait is not None:
            await _tell(interaction, wait)
            return
        words: str | None = None
        try:
            async with _waiting(interaction) as waiting:  # the download is part of the wait
                try:
                    data = await attachment.read()
                except discord.HTTPException:
                    waiting.closing = FAILED_READING
                    words = "DMbot couldn't download that file. Try again."
                else:
                    try:
                        text = await house_reader.read_file(attachment.filename, data)
                    except DocumentError as exc:  # a scanned PDF, an empty or damaged file
                        waiting.closing = UNREADABLE
                        words = str(exc)
                    else:
                        words = await house_sync.compare_text(
                            _bot(interaction),
                            current,
                            text,
                            _private_send(interaction),
                            from_link=False,
                            file_name=attachment.filename,
                            progress=lambda step: waiting.step(step, keeps=False),
                        )
        finally:
            _done(current)  # whatever went wrong, the next try is not "already comparing"
        if words:
            await _tell(interaction, words)


class FileMenu(_Menu):
    """The house-rules file's buttons: link or change the link, check it now, unlink, or
    upload a file for one comparison."""

    def __init__(self, campaign: Campaign, *, linked: bool) -> None:
        super().__init__()
        self.campaign = campaign
        self.add_item(
            _Button(
                self._link,
                label=CHANGE_LABEL if linked else LINK_LABEL,
                style=discord.ButtonStyle.secondary if linked else discord.ButtonStyle.primary,
            )
        )
        if linked:
            self.add_item(
                _Button(self._check, label=CHECK_LABEL, style=discord.ButtonStyle.primary)
            )
            self.add_item(
                _Button(self._unlink, label=UNLINK_LABEL, style=discord.ButtonStyle.danger)
            )
        self.add_item(_Button(self._upload, label=UPLOAD_LABEL))

    async def _link(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LinkForm(self.campaign))

    async def _upload(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(UploadForm(self.campaign))

    async def _check(self, interaction: discord.Interaction) -> None:
        await check_now(interaction, self.campaign)

    async def _unlink(self, interaction: discord.Interaction) -> None:
        from dmbot.rules.house import HouseRuleError

        await _answer_first(interaction)
        current = await _dm_campaign(interaction, self.campaign)
        links = _links(interaction)
        if current is None or links is None:
            return
        try:
            had = await links.clear(current.guild_id, current.id, interaction.user.id)
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        await _tell(
            interaction,
            "Done. DMbot won't read that file any more. Your house rules are as they were."
            if had
            else "No file was connected.",
        )
