"""Add a recap someone at the table wrote, from Discord (#2615, the website's twin of #2609).

`/dmbot recap-upload` takes a .txt or .md file (or none, and the words are pasted into a form; a
Discord form holds 4,000 characters, so a longer recap goes by file). The DM picks the campaign and
the session, says who wrote it, and the text is kept whole as that session's recap, approved by
the DM, never rewritten by the AI writer. It is the same database door as the website's
(`RecapStore.upload`), so the refusals are the same: only a DM, the one right-to-use question the
first time in a campaign, the question before replacing a recap the session has (the old one is
kept), and nothing while a recap is being written or sent. The text and the author's name are never
logged. A session with no transcript is added on the recap page: this picker lists the sessions
that have one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

import discord
from discord import app_commands

from dmbot.recaps import uploaded
from dmbot.ui.dmbot_commands import NOT_IN_SERVER, _bot, _tell, dmbot_group
from dmbot.ui.recap_again import NO_SESSIONS, NOT_YOURS, TIMEOUT_S, date_label
from dmbot.ui.recap_share import session_numbers

log = logging.getLogger(__name__)

FILE_ENDINGS = (".txt", ".md", ".markdown")
FILE_MAX_BYTES = 4 * uploaded.MAX_CHARS  # UTF-8 is up to 4 bytes a character
FORM_MAX = 4000  # the most a Discord form field holds
PICK_CAMPAIGN = "Which campaign is this recap for?"
PICK = "Which session is it the recap of? Newest first."
WRONG_FILE = "Send a .txt or .md file, or leave the file out and paste the words in the form."
NOT_TEXT = "I couldn't read that file as text. Save it as plain text (.txt) and try again."
TOO_BIG = "That file is too big. A recap can be up to 50,000 characters."
NO_DOWNLOAD = "I couldn't download that file. Try again."
FAILED = "Something went wrong, so nothing was saved. Try again in a moment."
STORED = (
    "✅ Kept as this session's recap, written by {author}. It's on your recap page. DMbot will "
    "check its names soon. Nothing goes to the table until you say so."
)
ASK_RIGHTS = uploaded.RIGHTS_ASK + " Is that so?"
ASK_REPLACE = "This session already has a recap. Replace it? The old one is kept as an earlier one."
OUTCOMES = {
    "busy": "DMbot is writing or sending this session's recap right now. Try again when it's done.",
    "bad_text": uploaded.BAD_TEXT,
    "not_allowed": NOT_YOURS,
}


@dataclass(frozen=True, slots=True)
class Draft:
    """What is being added. Held in memory for the length of the private conversation only."""

    campaign: Any
    session_at: int
    text: str
    author: str
    title: str
    replace_ok: bool = False
    rights: bool = False


def read_text(raw: bytes) -> str | None:
    """A file's bytes as UTF-8 text (a byte-order mark allowed), or None if they are not text."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


async def submit(interaction: discord.Interaction, draft: Draft) -> None:
    """Give the recap to the database's door and say what came of it, privately."""
    bot = _bot(interaction)
    clean = uploaded.clean_text(draft.text)
    author = uploaded.clean_author(draft.author)
    if clean is None or author is None or bot.recaps is None:
        await _tell(interaction, OUTCOMES["bad_text"] if bot.recaps else FAILED)
        return
    given = " ".join(draft.title.split())[: uploaded.TITLE_MAX]
    title = given or uploaded.title_for(clean, author)
    try:
        outcome = await bot.recaps.upload(
            draft.campaign.guild_id,
            draft.campaign.id,
            draft.session_at,
            interaction.user.id,
            title=title,
            text=clean,
            written_by=author,
            replace_ok=draft.replace_ok,
            rights=draft.rights,
        )
    except Exception:
        log.exception("Add a recap from Discord failed")
        await _tell(interaction, FAILED)
        return
    held = replace(draft, text=clean, author=author, title=title)
    if outcome == "stored":
        await _tell(interaction, STORED.format(author=discord.utils.escape_markdown(author)))
    elif outcome == "needs_rights":
        await _tell(interaction, ASK_RIGHTS, view=AskView(held, "Yes, they said OK", rights=True))
    elif outcome == "needs_confirm":
        await _tell(interaction, ASK_REPLACE, view=AskView(held, "Replace it", rights=False))
    else:
        await _tell(interaction, OUTCOMES.get(outcome, NOT_YOURS))


class AskView(discord.ui.View):
    """The one question the door asked, with a yes and a no. The yes sends the same recap again
    with that answer; the door asks the next question if there is one."""

    def __init__(self, draft: Draft, yes: str, *, rights: bool) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.draft, self.rights = draft, rights
        self.yes.label = yes

    @discord.ui.button(label="Yes, go ahead", style=discord.ButtonStyle.primary)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        # (the door checks the DM again; this only keeps a stranger from a forwarded message)
        if interaction.user.id not in self.draft.campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        answered = (
            replace(self.draft, rights=True)
            if self.rights
            else replace(self.draft, replace_ok=True)
        )
        await submit(interaction, answered)

    @discord.ui.button(label="No, leave it", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await _tell(interaction, "👍 Nothing was saved.")


class WriterModal(discord.ui.Modal, title="Add a recap"):
    """Who wrote it, an optional title, and (when no file was sent) the words themselves."""

    author: discord.ui.TextInput[WriterModal] = discord.ui.TextInput(
        label="Who wrote it?", max_length=uploaded.AUTHOR_MAX
    )
    title_: discord.ui.TextInput[WriterModal] = discord.ui.TextInput(
        label="Title (optional)", required=False, max_length=uploaded.TITLE_MAX
    )

    def __init__(self, campaign: Any, session_at: int, text: str | None) -> None:
        super().__init__()
        self.campaign, self.session_at, self.text_given = campaign, session_at, text
        self.words: discord.ui.TextInput[WriterModal] | None = None
        if text is None:
            self.words = discord.ui.TextInput(
                label="The recap",
                style=discord.TextStyle.paragraph,
                max_length=FORM_MAX,
                placeholder="Paste it here. A longer one goes by file.",
            )
            self.add_item(self.words)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.text_given if self.text_given is not None else self.words.value  # type: ignore[union-attr]
        draft = Draft(
            self.campaign, self.session_at, text, self.author.value, self.title_.value or ""
        )
        await submit(interaction, draft)


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    bot = _bot(interaction)
    guild = interaction.guild
    campaign = await bot.campaigns.get(guild.id, campaign_id) if guild is not None else None
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_YOURS)
        return None
    return campaign


class SessionPick(discord.ui.View):
    """The campaign's sessions, newest first, at most 25 (Discord's limit for a menu)."""

    def __init__(
        self, campaign: Any, sessions: list[Any], numbers: dict[int, int], text: str | None
    ) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id, self.text = campaign.id, text
        options = []
        for s in sessions[:25]:
            n = numbers.get(s.started_at, getattr(s, "number", 0))
            options.append(
                discord.SelectOption(
                    label=(f"Session {n} · " if n else "") + date_label(s.started_at),
                    value=str(s.started_at),
                )
            )
        menu: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Pick a session", options=options
        )
        menu.callback = self._picked  # type: ignore[method-assign]
        self.menu = menu
        self.add_item(menu)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is not None:
            await interaction.response.send_modal(
                WriterModal(campaign, int(self.menu.values[0]), self.text)
            )


class CampaignPick(discord.ui.View):
    def __init__(self, campaigns: list[Any], text: str | None) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.text = text
        menu: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Pick a campaign",
            options=[discord.SelectOption(label=c.name[:100], value=c.id) for c in campaigns[:25]],
        )
        menu.callback = self._picked  # type: ignore[method-assign]
        self.menu = menu
        self.add_item(menu)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _dm_campaign(interaction, self.menu.values[0])
        if campaign is not None:
            await show_sessions(interaction, campaign, self.text)


async def show_sessions(interaction: discord.Interaction, campaign: Any, text: str | None) -> None:
    bot = _bot(interaction)
    if bot.transcripts is None or bot.recaps is None:
        await _tell(interaction, FAILED)
        return
    try:
        sessions = await bot.transcripts.sessions(campaign.guild_id, campaign.id, limit=25)
        numbers = await session_numbers(bot, campaign)
    except Exception:
        log.exception("Couldn't list the sessions for the recap upload")
        await _tell(interaction, FAILED)
        return
    if not sessions:
        await _tell(interaction, NO_SESSIONS)
        return
    await _tell(interaction, PICK, view=SessionPick(campaign, sessions, numbers, text))


@dmbot_group.command(name="recap-upload", description="Add a recap someone at your table wrote")
@app_commands.describe(file="A .txt or .md file with the recap (leave out to paste it instead)")
async def recap_upload_command(
    interaction: discord.Interaction, file: discord.Attachment | None = None
) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    text: str | None = None
    if file is not None:
        if not file.filename.lower().endswith(FILE_ENDINGS):
            await _tell(interaction, WRONG_FILE)
            return
        if file.size > FILE_MAX_BYTES:
            await _tell(interaction, TOO_BIG)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            text = read_text(await file.read())
        except discord.HTTPException:
            await _tell(interaction, NO_DOWNLOAD)
            return
        if text is None:
            await _tell(interaction, NOT_TEXT)
            return
        if uploaded.clean_text(text) is None:
            await _tell(interaction, TOO_BIG if text.strip() else uploaded.BAD_TEXT)
            return
    try:
        campaigns = await _bot(interaction).campaigns.list_campaigns(guild.id)
    except Exception:
        log.exception("Couldn't load the campaigns for the recap upload")
        await _tell(interaction, FAILED)
        return
    mine = [c for c in campaigns if interaction.user.id in c.dm_user_ids]
    if not mine:
        await _tell(interaction, NOT_YOURS)
    elif len(mine) == 1:
        await show_sessions(interaction, mine[0], text)
    else:
        await _tell(interaction, PICK_CAMPAIGN, view=CampaignPick(mine, text))
