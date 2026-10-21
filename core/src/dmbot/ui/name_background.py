"""📜 From a story or background (#2167): a DM gives DMbot their campaign's background or backstory
and DMbot suggests the names in it for 📝 Check new names. Nothing is confirmed without the DM.

The text is the DM's own, read once and never kept; only the names the DM says yes to, and each
name's short quote until it is decided, are stored. A published book or adventure is a different
path (the Shared story switch, with its rights confirmation). Only the campaign's DMs; counted
against the campaign's AI allowance like the other name features; the log has counts only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from functools import partial
from typing import cast

import discord

from dmbot.ai import AIError
from dmbot.campaigns import Campaign
from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.memory import background as bg
from dmbot.memory.models import BACKGROUND_NOTE as BACKGROUND
from dmbot.memory.models import (
    BACKGROUND_SOURCE,
    DESCRIPTION_MAX,
    DM,
    PROPOSED,
    MemoryRuleError,
    NewName,
)
from dmbot.memory.ontology import check_named
from dmbot.memory.store import MemoryStore
from dmbot.mentions import NO_PINGS
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _send, _tell
from dmbot.ui.name_lists import (
    AI_AT_ONCE,
    AI_READS_PER_DAY,
    AI_TIME_LIMIT_S,
    LINK_MAX,
    NO_AI_FOR_DOCUMENTS,
    PASTE_MAX,
    Upload,
    _ai_busy,
    _ai_reads,
    read_attachment,
    read_link_once,
)
from dmbot.ui.names import (
    REVIEW_LABEL_MAX,
    _campaign_for,
    _md,
    _memory,
    changed,
    start_review,
)

log = logging.getLogger(__name__)

NOT_A_BOOK = "your own notes or background, not a published book"
ONLY_DMS = "Only a DM of this campaign can do that."
MAX_BULK_BUTTONS = 4


def rights_text() -> str:
    return (
        f"Use this for {NOT_A_BOOK}. (For a book or adventure, use the **Shared story** "
        "switch.)\n**Pressing 🤖 Find names says this is your own writing, and you may use "
        "it.** The text is sent once to Anthropic (an AI company) to be read. DMbot does not "
        "keep it, only a short line beside each name until you answer it."
    )


class BackgroundMenu(_Menu):
    """How to give DMbot the background: paste it, upload it, or paste a link."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._paste, label="✍️ Paste it here", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._upload, label="📎 Upload a file", style=grey))
        self.add_item(_Button(self._link, label="🔗 Paste a link", style=grey))

    async def _open(self, interaction: discord.Interaction, modal: discord.ui.Modal) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        self.origin = None
        await interaction.response.send_modal(modal)

    async def _paste(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, PasteBackground(self.campaign_id))

    async def _upload(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, UploadBackground(self.campaign_id))

    async def _link(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, LinkBackground(self.campaign_id))


class PasteBackground(discord.ui.Modal, title="Your story or background"):
    story: discord.ui.TextInput[PasteBackground] = discord.ui.TextInput(
        label="Paste your background (long? upload a file)",
        style=discord.TextStyle.paragraph,
        placeholder="Lord Brannoch rules Vale Keep from his tower…",
        max_length=PASTE_MAX,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await offer(interaction, self.campaign_id, Upload(self.story.value, "your background"))


class LinkBackground(discord.ui.Modal, title="Your background, from a link"):
    link: discord.ui.TextInput[LinkBackground] = discord.ui.TextInput(
        label="Link to a document or web page",
        placeholder="https://docs.google.com/document/d/…",
        max_length=LINK_MAX,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        got, problem = await read_link_once(campaign.guild_id, self.link.value)
        if got is None:
            await _tell(interaction, problem or "DMbot couldn't read that link.")
            return
        await offer(interaction, self.campaign_id, got)


class UploadBackground(discord.ui.Modal, title="Your background, from a file"):
    file: discord.ui.Label[UploadBackground] = discord.ui.Label(
        text="A PDF, Word, text or web page file",
        component=discord.ui.FileUpload(max_values=1),
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return  # checked before downloading anything
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        upload = cast(discord.ui.FileUpload[UploadBackground], self.file.component)
        if not upload.values:
            await _tell(interaction, "No file was added. Press 📎 Upload a file and pick one.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        got, problem = await read_attachment(upload.values[0])
        if got is None:
            await _tell(interaction, problem or "DMbot couldn't read that.")
            return
        await offer(interaction, self.campaign_id, got)


async def offer(interaction: discord.Interaction, campaign_id: str, upload: Upload) -> None:
    """Say what reading it will do and cost, and wait for the DM's press."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if interaction.user.id not in campaign.dm_user_ids:  # on every path in
        await _tell(interaction, ONLY_DMS)
        return
    if _bot(interaction).background_ai is None:
        await _tell(interaction, NO_AI_FOR_DOCUMENTS)
        return
    cents = bg.estimate_cents(len(upload.text))
    await _send(
        interaction,
        f"📜 **Find the names in {_md(upload.label)}?** Cost: {bg.cost_text(cents)} of your AI "
        "allowance. Nothing is added until you say yes.\n"
        "DMbot's AI reads it and suggests names (people, places, groups, gods…). You check each "
        "one in 📝 Check new names.\n"
        f"{rights_text()}",
        BackgroundOffer(campaign.id, upload),
    )


class BackgroundOffer(_Menu):
    def __init__(self, campaign_id: str, upload: Upload) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.upload = upload
        self.add_item(_Button(self._read, label="🤖 Find names", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def _read(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # the checks below are database reads (#88, #351)
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        bot = _bot(interaction)
        ai = bot.background_ai
        memory = _memory(interaction)
        if ai is None or memory is None:
            await _tell(
                interaction,
                "DMbot's AI was switched off. Nothing was added. Ask your server owner to "
                "turn it on.",
            )
            return
        refused = await bot.plan_gate("ai", campaign.guild_id, campaign, interaction.user.id)
        if refused is not None:
            await _tell(interaction, refused)
            return
        guild = campaign.guild_id
        day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
        if guild in _ai_busy:
            await _tell(interaction, "DMbot is already reading a document for this server. "
                        "Try again when it's done.")  # fmt: skip
            return
        if _ai_reads.get((guild, day), 0) >= AI_READS_PER_DAY:
            await _tell(interaction, "This server has used today's document reads. Try again "
                        "tomorrow.")  # fmt: skip
            return
        self.stop()
        _ai_busy.add(guild)
        for old in [k for k in _ai_reads if k[1] != day]:
            del _ai_reads[old]
        _ai_reads[(guild, day)] = _ai_reads.get((guild, day), 0) + 1
        try:
            await interaction.edit_original_response(
                content=f"🤖 Reading {_md(self.upload.label)}… this can take a few minutes for a "
                "long background.",
                view=None,
                allowed_mentions=NO_PINGS,
            )
            document = fingerprint(self.upload.text)
            # The DM said it is their own writing; a lasting record, before anything is read.
            await bot.campaigns.record_confirmation(
                guild, campaign.id, interaction.user.id, OWN_WRITING, document
            )
            log.info(
                "Background confirmed for AI reading: campaign=%s doc=%s",
                campaign.id,
                document[:12],
            )
            known = await memory.known_keys(guild, campaign.id)

            async def one(piece: str) -> list[bg.Found]:
                async with AI_AT_ONCE:
                    reply = await ai.complete(
                        bg.instructions(), bg.request(piece), max_tokens=4000, schema=bg.SCHEMA
                    )
                return bg.read_answer(reply.text, piece)

            async with asyncio.timeout(AI_TIME_LIMIT_S):
                answers = await asyncio.gather(*(one(p) for p in bg.pieces(self.upload.text)))
            found = bg.merge(
                answers, known, rules=(campaign.target_ruleset, campaign.fallback_ruleset)
            )
            stored = await store(memory, campaign, found)
            log.info("Background read: %d names suggested", sum(map(len, stored.values())))
            await summary(interaction, campaign, self.upload.label, stored)
        except (AIError, CampaignError) as exc:  # CampaignError: the campaign is gone
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(content=str(exc))
        except TimeoutError:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="Reading took too long. Nothing was added. Split the background into "
                    "smaller parts and try again."
                )
        except Exception:
            log.exception("Reading a background with the AI failed")
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="Something went wrong. Nothing was added. Try again later."
                )
        finally:
            _ai_busy.discard(guild)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. Nothing was sent or added.", view=None
        )


async def store(
    memory: MemoryStore, campaign: Campaign, found: list[bg.Found]
) -> dict[str, list[str]]:
    """Each name as a suggestion with its quote (never confirmed), its other spellings with it and
    a disguise kept as a secret name. One change for the whole list: one reload of the live names,
    one Undo. Returns the new suggestions' IDs for each kind."""
    names: list[NewName] = []
    for item in found:
        try:
            check_named(item.kind, item.name)  # one refused name must not stop the others
        except MemoryRuleError as exc:
            log.info("Skipped a name from a background: %s", exc)
            continue
        note = f'{BACKGROUND}: "{item.quote}"' if item.quote else BACKGROUND
        if len(note) > DESCRIPTION_MAX:
            note = BACKGROUND  # a long quote goes, the name stays
        names.append(
            NewName(
                item.name, item.kind, PROPOSED, others=item.also, secrets=item.secret, note=note
            )
        )
    stored: dict[str, list[str]] = {}
    if not names:
        return stored
    try:
        written = await memory.add_names(
            campaign.guild_id, campaign.id, names, source=DM, entity_source=BACKGROUND_SOURCE
        )
    except MemoryRuleError as exc:
        log.info("Skipped the names from a background: %s", exc)
        return stored
    for name, entity_id in zip(names, written.value, strict=False):
        if entity_id is not None:
            stored.setdefault(name.type, []).append(entity_id)
    return stored


def _plural(kind: str, count: int) -> str:
    one, many = bg.LABELS.get(kind, (kind, kind + "s"))
    return f"{count} {one if count == 1 else many}"


async def summary(
    interaction: discord.Interaction,
    campaign: Campaign,
    label: str,
    stored: dict[str, list[str]],
) -> None:
    total = sum(map(len, stored.values()))
    if not total:
        await interaction.edit_original_response(
            content=f"🤖 DMbot found no new names in {_md(label)}. Nothing was added.",
            allowed_mentions=NO_PINGS,
        )
        return
    parts = ", ".join(
        _plural(k, len(ids)) for k, ids in sorted(stored.items(), key=lambda x: -len(x[1]))
    )
    view = BulkConfirm(campaign.id, stored)
    await interaction.edit_original_response(
        content=(
            f"📜 **{total} name{'' if total == 1 else 's'} to check** from {_md(label)}: "
            f"{parts}.\nThey wait in 📝 Check new names, each with its line from your text. "
            "Nothing is added until you say yes, and **Add all** adds every name of that "
            "group. DMbot can miss names or put one in the wrong group, so look them over."
        ),
        view=view,
        allowed_mentions=NO_PINGS,
    )
    view.origin = interaction


class BulkConfirm(_Menu):
    """Say yes to every name of a kind from this reading at once ("All 14 places"), undo it, or
    open the checking. Only the names this reading found: never an earlier reading's."""

    def __init__(self, campaign_id: str, stored: dict[str, list[str]]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.ids = {k: list(v) for k, v in stored.items()}
        self.last: tuple[str, int, list[str]] | None = None  # (kind, batch, ids) to undo
        self._build()

    def _build(self) -> None:
        self.clear_items()
        by_size = sorted(self.ids.items(), key=lambda x: -len(x[1]))
        for kind, ids in by_size[:MAX_BULK_BUTTONS]:
            if kind == "concept":
                continue  # an idea needs a look: no yes-to-all
            self.add_item(
                _Button(
                    partial(self._all, kind),
                    label=logic.shorten(f"✅ Add all {_plural(kind, len(ids))}", REVIEW_LABEL_MAX),
                    style=discord.ButtonStyle.success,
                )
            )
        self.add_item(
            _Button(
                self._open,
                label="📝 Check them one by one",
                style=discord.ButtonStyle.primary,
                row=1,
            )
        )
        if self.last is not None:
            self.add_item(
                _Button(
                    self._undo,
                    label=logic.shorten(f"↩️ Undo {_plural(self.last[0], len(self.last[2]))}", 25),
                    style=discord.ButtonStyle.secondary,
                    row=1,
                )
            )

    async def _open(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=None)
        await start_review(interaction, self.campaign_id)

    async def _all(self, kind: str, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # many writes follow (#351)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        ids = self.ids.get(kind, [])
        try:
            written = await memory.confirm_kinds(
                campaign.guild_id, campaign.id, ids, kind, source=DM
            )  # this reading's names only; one already answered is left alone
        except MemoryRuleError:
            await _tell(interaction, "DMbot couldn't add those. Open 📝 Check new names instead.")
            return
        changed(interaction, campaign)
        self.ids.pop(kind, None)
        if written.value and written.batch is not None:
            self.last = (kind, written.batch, ids)
        self._build()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(view=self, allowed_mentions=NO_PINGS)
        done = (
            f"✅ Added {_plural(kind, written.value)}."
            if written.value
            else "Nothing was added: those names were already answered."
        )
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                done,
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )

    async def _undo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None or self.last is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        kind, batch, ids = self.last
        try:
            await memory.undo(campaign.guild_id, campaign.id, batch)
        except MemoryRuleError:
            await _tell(interaction, "That can't be undone now: something changed since.")
            return
        changed(interaction, campaign)
        self.ids[kind] = ids
        self.last = None
        self._build()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(view=self, allowed_mentions=NO_PINGS)
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                f"↩️ Took back {_plural(kind, len(ids))}. They're back in 📝 Check new names.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
