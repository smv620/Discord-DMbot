"""Sending a campaign's house-rules file (#969): `house-rules-<campaign>.txt`, privately.

After every change a DM makes in Discord (Add, Edit, Remove, an Override, a rule said or
typed to DMbot) the DM gets the updated file, to put wherever the table keeps its rules.
The 📥 **Download** button on the `/dmbot houserules` list gives anyone in the server the
same file: they may already read the list. The file holds this campaign's house rules and
no one else's. The format is `dmbot.rules.house_file`.

Sending the file is never worth failing the change for: a problem is logged and skipped.
"""

from __future__ import annotations

import io
import logging
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import house_file
from dmbot.ui.dmbot_commands import NO_PINGS, _answer_first, _bot, _Button, _Menu, _send, _tell

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
LINK_LABEL = "🔗 Link a file"
CHANGE_LABEL = "🔗 Change link"
CHECK_LABEL = "🔄 Check it now"
UNLINK_LABEL = "Unlink"
UPLOAD_LABEL = "📎 Upload a file"
ONLY_DMS = "Only this campaign's DMs can set up the house-rules file. Ask your DM."
GONE = "That campaign isn't here any more. Use `/dmbot houserules` to start again."
NOT_READY = "The house-rules file isn't available right now. Please try again in a moment."
NOT_LINKED = (
    "📄 **House-rules file**\nNo file is linked. Link a Google Doc, a Drive, Dropbox or "
    "OneDrive file (anyone with the link can open it) and DMbot compares it with your "
    "house rules every time you start a session. Or upload a file now.\n"
    "Nothing changes unless you press a button."
)


def linked_text(site: str) -> str:
    return (
        f"📄 **House-rules file**\nLinked: **{site}**. DMbot compares it with your house "
        "rules every time you start a session, and tells you what differs. Nothing changes "
        "unless you press a button."
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


async def check_now(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Read the linked file now and offer what differs, privately."""
    from dmbot.dm_screen import house_sync

    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    if current is None:
        return
    bot = _bot(interaction)
    await house_sync.check_linked(bot, current, _private_send(interaction), quiet_if_same=False)


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
        f"✅ Linked: **{saved.site}**. DMbot will compare it with your house rules every "
        "time you start a session. Checking it now…",
    )
    await check_now(interaction, current)


class LinkForm(discord.ui.Modal, title="Link your house-rules file"):
    link: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="Share link to your house-rules file",  # 45 characters at most
        placeholder="https://docs.google.com/document/d/…",
        max_length=2000,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await link_file(interaction, self.campaign, self.link.value)


class UploadForm(discord.ui.Modal, title="Compare a house-rules file"):
    file: discord.ui.Label[UploadForm] = discord.ui.Label(
        text="Your house-rules file (a .txt file)",  # 45 characters at most
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
        if attachment.size > house_sync.READ_MAX_BYTES:
            await _tell(interaction, house_sync.TOO_BIG)
            return
        try:
            data = await attachment.read()
        except discord.HTTPException:
            await _tell(interaction, "DMbot couldn't download that file. Try again.")
            return
        words = await house_sync.compare_text(
            _bot(interaction),
            current,
            data.decode("utf-8", errors="replace"),
            _private_send(interaction),
            from_link=False,
        )
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
            "Unlinked. DMbot won't read the file any more. Your house rules are as they were."
            if had
            else "No file was linked.",
        )
