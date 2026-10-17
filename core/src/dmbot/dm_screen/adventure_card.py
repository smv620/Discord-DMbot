"""📖 Give DMbot your adventure, on the Shared story card (#1698; docs/STORY_MEMORY.md, section 6):
the DM gives one file at a time (PDF, Word, text), DMbot keeps its words for this campaign only,
and the card lists what it holds with a 🗑 Remove on each (one confirmation).

Only when the Shared story switch is on and a DM said here that they have the right to use the
material (#1692, CLAUDE.md IP rule): the button is not drawn otherwise, and every press checks it
again, so an old card can't be used after the switch went off. Only the campaign's DMs, checked
on every press. Nothing reads the text yet and no AI is called: the file is read by the same
process-with-a-time-limit the names screen uses (`memory.document_reader`), and nothing but its
words is kept.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import PurePath
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import STORY_OFF
from dmbot.dm_screen.story_mode import COULDNT, _dm_campaign
from dmbot.logs import set_log_context
from dmbot.memory.adventure import (
    DOCUMENTS_MAX,
    FILE_MAX_BYTES,
    READ_REPLY_BYTES,
    READ_TIME_S,
    AdventureError,
    Document,
    adventure_text,
)
from dmbot.memory.document_reader import READING, read_document
from dmbot.memory.name_documents import DocumentError, kind_of_file

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
_DOC = r"(?P<doc>[0-9a-f]{32})"
NO_MENTIONS = discord.AllowedMentions.none()
INTRO = "📖 **Your adventure**"
NOTE = (
    "Nothing reads this yet, so it changes no alerts. DMbot keeps the text of your files, not the "
    "files, for this campaign only (it is in this campaign's backup too). Remove one and DMbot "
    "forgets it."
)
EMPTY = "Nothing yet. Press Give a file to add one."
NOT_ON = (
    "Not yet. In Settings, turn on Shared story and say you have the right to use the material."
)
TOO_BIG = "That file is over 10 MB. Try a smaller file, or save a text copy (.txt)."
WRONG_TYPE = "DMbot can read PDF, Word (.docx) and text (.txt, .md) files. Save it as one of those."
NO_FILE = "No file was picked. Press Give a file again and pick one."
DOWNLOAD_FAILED = "DMbot couldn't download that file, and kept nothing. Try again."
ADD_LABEL = "Give a file"
FILE_TYPES = (".pdf", ".docx", ".txt", ".md", ".text")
CHARS_PER_PAGE = 2000  # for "about N pages"


def pages_of(characters: int) -> int:
    return max(1, round(characters / CHARS_PER_PAGE))


def _pages(characters: int) -> str:
    return "page" if pages_of(characters) == 1 else "pages"


NAME_SHOWN = 30  # characters of a file name on the card: twenty lines must fit one message
FULL_LINES = 8  # up to this many documents get who and when; more get the short line


def card_text(documents: list[Document]) -> str:
    lines = [INTRO, NOTE]
    if not documents:
        lines.append(EMPTY)
    for n, d in enumerate(documents, 1):
        name = discord.utils.escape_markdown(d.file_name[:NAME_SHOWN])
        size = f"about {pages_of(d.characters)} {_pages(d.characters)}"
        if len(documents) <= FULL_LINES:
            lines.append(
                f"{n}. **{name}** ({size}), added by <@{d.added_by}> on <t:{d.added_at}:d>"
            )
        else:
            lines.append(f"{n}. **{name}** ({size})")
    if len(documents) >= DOCUMENTS_MAX:
        lines.append(
            f"That is the most DMbot keeps here ({DOCUMENTS_MAX}). Remove one to add more."
        )
    return "\n".join(lines)


def card_view(campaign: Campaign, documents: list[Document]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for n, d in enumerate(documents[:DOCUMENTS_MAX], 1):
        view.add_item(RemoveButton(campaign.id, d.id, n))
    if len(documents) < DOCUMENTS_MAX:
        view.add_item(AddButton(campaign.id, row=4))
    return view


async def _allowed(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if the person is one of its DMs, the switch is on and a DM said here they
    have the right to the material; else they are told why and it is None."""
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return None
    store = interaction.client.campaigns  # type: ignore[attr-defined]
    try:
        said = campaign.story_mode != STORY_OFF and await store.story_confirmed(
            campaign.guild_id, campaign.id
        )
    except Exception:
        log.exception("Couldn't read the Shared story confirmation")
        await interaction.response.send_message(COULDNT, ephemeral=True)
        return None
    if not said:
        await interaction.response.send_message(NOT_ON, ephemeral=True)
        return None
    return campaign


def _store(interaction: discord.Interaction) -> Any:
    return getattr(interaction.client, "adventure", None)


async def _card(
    interaction: discord.Interaction, campaign: Campaign
) -> tuple[str, discord.ui.View]:
    docs = await _store(interaction).documents(campaign.guild_id, campaign.id)
    return card_text(docs), card_view(campaign, docs)


class AdventureButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:adv:{_ID}",
):
    """📖 Give DMbot your adventure, on the Shared story card: opens the list."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Give your adventure",
                emoji="📖",
                style=discord.ButtonStyle.primary,
                row=1,
                custom_id=f"dmbot:adv:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AdventureButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        if _store(interaction) is None:
            await interaction.response.send_message(COULDNT, ephemeral=True)
            return
        text, view = await _card(interaction, campaign)
        await interaction.response.send_message(
            text, view=view, ephemeral=True, allowed_mentions=NO_MENTIONS
        )


class AddButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advadd:{_ID}",
):
    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=ADD_LABEL,
                emoji="➕",
                style=discord.ButtonStyle.success,
                row=row,
                custom_id=f"dmbot:advadd:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AddButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.send_modal(AdventureForm(campaign))


class AdventureForm(discord.ui.Modal, title="Give DMbot your adventure"):
    file: discord.ui.Label[AdventureForm] = discord.ui.Label(
        text="Your adventure (PDF, Word or text)",  # 45 characters at most
        description="One file, up to 10 MB. DMbot keeps the text, not the file.",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=900)
        self.campaign = campaign

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("The adventure form failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(COULDNT, ephemeral=True)
        else:
            await interaction.response.send_message(COULDNT, ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        set_log_context(guild_id=interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)  # reading takes a while
        say = interaction.followup.send
        # Checked again, now: the form may have been open for minutes.
        store = _store(interaction)
        campaigns = interaction.client.campaigns  # type: ignore[attr-defined]
        guild = interaction.guild
        fresh = await campaigns.get(guild.id, self.campaign.id) if guild else None
        if fresh is None or interaction.user.id not in fresh.dm_user_ids or store is None:
            await say("Only this campaign's DMs can do that.", ephemeral=True)
            return
        upload = cast(discord.ui.FileUpload[AdventureForm], self.file.component)
        if not upload.values:
            await say(NO_FILE, ephemeral=True)
            return
        attachment = upload.values[0]
        if attachment.size > FILE_MAX_BYTES:
            await say(TOO_BIG, ephemeral=True)
            return
        if PurePath(attachment.filename).suffix.casefold() not in FILE_TYPES or (
            kind_of_file(attachment.filename) == "unknown"
        ):
            await say(WRONG_TYPE, ephemeral=True)
            return
        try:
            raw = await attachment.read()
        except discord.HTTPException:
            await say(DOWNLOAD_FAILED, ephemeral=True)
            return
        began = time.monotonic()
        try:
            async with READING:  # the same limit as the names screen
                text = await read_document(
                    attachment.filename,
                    raw,
                    parse=adventure_text,
                    limit_s=READ_TIME_S,
                    max_reply=READ_REPLY_BYTES,
                )
            await store.add(
                fresh.guild_id, fresh.id, interaction.user.id, attachment.filename, text
            )
        except (DocumentError, AdventureError) as exc:
            await say(str(exc), ephemeral=True)
            return
        # Counts and seconds only: never the file's name or words.
        log.info(
            "Adventure text kept: %d characters in %.1f s", len(text), time.monotonic() - began
        )
        card, view = await _card(interaction, fresh)
        await say(f"Kept.\n\n{card}", view=view, ephemeral=True, allowed_mentions=NO_MENTIONS)


class RemoveButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrm:{_ID}:{_DOC}",
):
    """🗑 Remove, one for each document: asks once."""

    def __init__(self, campaign_id: str, document_id: str, number: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"Remove {number}" if number else "Remove",
                emoji="🗑",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advrm:{campaign_id}:{document_id}",
            )
        )
        self.campaign_id, self.document_id = campaign_id, document_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveButton:
        return cls(match["campaign"], match["doc"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        docs = await _store(interaction).documents(campaign.guild_id, campaign.id)
        doc = next((d for d in docs if d.id == self.document_id), None)
        if doc is None:
            await interaction.response.edit_message(
                content=f"Already removed.\n\n{card_text(docs)}",
                view=card_view(campaign, docs),
            )
            return
        view = discord.ui.View(timeout=None)
        view.add_item(RemoveYesButton(campaign.id, doc.id))
        view.add_item(RemoveNoButton(campaign.id))
        name = discord.utils.escape_markdown(doc.file_name[:NAME_SHOWN])
        await interaction.response.edit_message(
            content=(
                f"Remove **{name}**? DMbot will forget the text it kept from it. "
                "You can add the file again later."
            ),
            view=view,
        )


class RemoveYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrmyes:{_ID}:{_DOC}",
):
    def __init__(self, campaign_id: str, document_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Yes, remove it",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:advrmyes:{campaign_id}:{document_id}",
            )
        )
        self.campaign_id, self.document_id = campaign_id, document_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveYesButton:
        return cls(match["campaign"], match["doc"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # (removing needs only a DM)
        if campaign is None:
            return
        store = _store(interaction)
        note = "Removed. DMbot forgot it."
        try:
            await store.remove(
                campaign.guild_id, campaign.id, interaction.user.id, self.document_id
            )
        except AdventureError as exc:
            note = str(exc)
        except Exception:
            log.exception("Couldn't remove an adventure document")
            note = COULDNT
        text, view = await _card(interaction, campaign)
        await interaction.response.edit_message(content=f"{note}\n\n{text}", view=view)


class RemoveNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:advrmno:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep it",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:advrmno:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RemoveNoButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        text, view = await _card(interaction, campaign)
        await interaction.response.edit_message(content=text, view=view)


ADVENTURE_BUTTONS = (
    AdventureButton,
    AddButton,
    RemoveButton,
    RemoveYesButton,
    RemoveNoButton,
)
