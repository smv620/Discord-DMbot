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
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, cast

import discord

from dmbot.ai import AIError
from dmbot.campaigns import Campaign
from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.memory import background as bg
from dmbot.memory.backstory_links import NewLink, Waiting
from dmbot.memory.link_words import connection_words
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import BACKGROUND_NOTE as BACKGROUND
from dmbot.memory.models import (
    BACKGROUND_SOURCE,
    CONFIRMED,
    DESCRIPTION_MAX,
    DM,
    PROPOSED,
    MemoryRuleError,
    NewName,
    name_key,
)
from dmbot.memory.ontology import ACTIVE, check_named
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
QUOTE_SHOWN = 200  # a long quote must not push the buttons off a phone
MAX_BULK_BUTTONS = 4
CUT_NOTE = (
    "\nLong text: DMbot may have missed some names or connections. Reading it in smaller parts "
    "finds more."
)
CONNECTIONS_NOTE = (
    '\n📜 {count} connection{s} found too (like "A is the boss of B"). You\'ll be asked about '
    "each one after both its names are answered, or press 📜 Check the connections."
)


def rights_text() -> str:
    return (
        f"Use this for {NOT_A_BOOK}. (For a book or adventure, use the **Shared story** "
        "switch.)\n**Pressing 🤖 Find names says this is your own writing, and you may use "
        "it.** The text is sent once to Anthropic (an AI company) to be read. DMbot does not "
        "keep it, only a short line beside each name until you answer it."
    )


async def open_background(interaction: discord.Interaction, campaign_id: str) -> None:
    """📜 Upload campaign lore (the Main Menu's button, and 📝 Names > Add many): the menu to
    paste, upload or link it. For the campaign's DMs, checked here on every press."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return
    await _send(
        interaction,
        "📜 **Upload campaign lore.** Paste it, upload a file, or paste a link. DMbot suggests the "
        "names in it and how they connect; you keep what's right.\n"
        f"{rights_text()}",
        BackgroundMenu(campaign_id),
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
    await _send(interaction, offer_text(upload, False), BackgroundOffer(campaign.id, upload))


TRUTH_OFF = "Story: asserted"
TRUTH_ON = "Story: 📌 100% truth"


def offer_text(upload: Upload, truth: bool) -> str:
    """The question before the AI reads a lore upload. The switch is asked once per upload
    (#2447): asserted lore is what the story says, which play can still change; 📌 100% truth is
    the DM's word, and DMbot tells the DM when a session says otherwise."""
    cents = bg.estimate_cents(len(upload.text))
    how = (
        "📌 **100% truth**: the connections you keep are pinned. DMbot tells you, privately, if "
        "a session says otherwise."
        if truth
        else "**Asserted**: the story says so, and play can still change it."
    )
    return (
        f"📜 **Find the names in {_md(upload.label)}?** Cost: {bg.cost_text(cents)} of your AI "
        "allowance. Nothing is added until you say yes.\n"
        "DMbot's AI reads it and suggests names (people, places, groups, gods…) and the "
        "connections your text states, each with its line from the text. You check each one: "
        "the names in 📝 Check new names, then the connections.\n"
        f"Is this lore {how} Press the story button to switch.\n"
        f"{rights_text()}"
    )


class BackgroundOffer(_Menu):
    def __init__(self, campaign_id: str, upload: Upload) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.upload = upload
        self.truth = False  # asked once per upload (#2447): "Asserted" by default
        self.add_item(_Button(self._read, label="🤖 Find names", style=discord.ButtonStyle.primary))
        self.switch = _Button(self._flip, label=TRUTH_OFF, style=discord.ButtonStyle.secondary)
        self.add_item(self.switch)
        self.add_item(_Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def _flip(self, interaction: discord.Interaction) -> None:
        """Asserted (a claim the story can still change) or 📌 100% truth (the DM's word that it
        is true: the connections found are pinned when kept). Shown again with the new choice."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        self.truth = not self.truth
        self.switch.label = TRUTH_ON if self.truth else TRUTH_OFF
        await interaction.response.edit_message(
            content=offer_text(self.upload, self.truth), view=self, allowed_mentions=NO_PINGS
        )

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

            own = await own_kinds(memory, campaign)
            spent = [0]  # retries so far: a reading has a small budget of them

            async def one(piece: str) -> Piece:
                return await read_piece(ai, piece, own, spent)

            async with asyncio.timeout(AI_TIME_LIMIT_S):
                both = await asyncio.gather(*(one(p) for p in bg.pieces(self.upload.text)))
            found = bg.merge(
                [p.names for p in both],
                known,
                rules=(campaign.target_ruleset, campaign.fallback_ruleset),
            )
            stored = await store(memory, campaign, found)
            queued = await queue_links(
                bot, memory, campaign, found, [p.links for p in both], set(own), truth=self.truth
            )
            cut = any(p.cut for p in both)
            log.info(
                "Background read: %d names and %d connections suggested%s",
                sum(map(len, stored.values())),
                queued,
                ", an answer cut off" if cut else "",
            )
            await summary(interaction, campaign, self.upload.label, stored, queued, cut=cut)
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
                    content="Something went wrong. Nothing was added. Try again later, and if it "
                    "keeps happening, tell your server owner."
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


def _waiting_note(queued: int) -> str:
    if not queued:
        return ""
    return CONNECTIONS_NOTE.format(count=queued, s="" if queued == 1 else "s")


async def summary(
    interaction: discord.Interaction,
    campaign: Campaign,
    label: str,
    stored: dict[str, list[str]],
    queued: int = 0,
    *,
    cut: bool = False,
) -> None:
    total = sum(map(len, stored.values()))
    if not total:
        await interaction.edit_original_response(
            content=f"🤖 DMbot found no new names in {_md(label)}. Nothing was added."
            + (CUT_NOTE if cut else ""),
            allowed_mentions=NO_PINGS,
        )
        if queued:  # the names were all known already: the connections may be ready
            await offer_ready(interaction, campaign.id)
        return
    parts = ", ".join(
        _plural(k, len(ids)) for k, ids in sorted(stored.items(), key=lambda x: -len(x[1]))
    )
    view = BulkConfirm(campaign.id, stored, queued)
    await interaction.edit_original_response(
        content=(
            f"📜 **{total} name{'' if total == 1 else 's'} to check** from {_md(label)}: "
            f"{parts}.\nThey wait in 📝 Check new names, each with its line from your text. "
            "Nothing is added until you say yes, and **Add all** adds every name of that "
            "group. DMbot can miss names or put one in the wrong group, so look them over."
            + (
                CONNECTIONS_NOTE.format(count=queued, s="" if queued == 1 else "s")
                if queued
                else ""
            )
            + (CUT_NOTE if cut else "")
        ),
        view=view,
        allowed_mentions=NO_PINGS,
    )
    view.origin = interaction


async def own_kinds(memory: MemoryStore, campaign: Campaign) -> dict[str, str]:
    """The campaign's own relationships (key → how it reads), offered to the AI beside the
    built-in kinds, at most MAX_OWN_KINDS."""
    onto = await memory.ontology(campaign.guild_id, campaign.id)
    taken = set(bg.CONNECTIONS.values())
    own = {
        p.key: p.label
        for p in onto.predicates.values()
        if not p.core and p.status == ACTIVE and p.key not in taken
    }
    return dict(sorted(own.items())[: bg.MAX_OWN_KINDS])


@dataclass(frozen=True, slots=True)
class Piece:
    names: list[bg.Found]
    links: list[bg.Connection]
    cut: bool  # the answer was cut off, even after the second try


RETRIES_PER_READING = 4  # cut answers asked for again; more than this and the rest stay as cut


async def read_piece(ai: Any, piece: str, own: dict[str, str], spent: list[int]) -> Piece:
    """One piece of the text read, with room sized from the piece. An answer cut off is asked for
    once more with twice the room while the reading's retry budget lasts; one still cut is read as
    far as it goes and the DM is told it may be missing some."""
    room = bg.room_for(piece)
    reply = None
    for attempt in range(2):
        async with AI_AT_ONCE:
            reply = await ai.complete(
                bg.instructions(own),
                bg.request(piece),
                max_tokens=min(room * (2**attempt), bg.RETRY_MAX),
                schema=bg.schema(own),
            )
        if not reply.cut or spent[0] >= RETRIES_PER_READING:
            break
        if attempt == 0:
            spent[0] += 1
    assert reply is not None
    return Piece(
        bg.read_answer(reply.text, piece),
        bg.read_connections(reply.text, piece, set(own)),
        bool(reply.cut),
    )


async def queue_links(
    bot: Any,
    memory: MemoryStore,
    campaign: Campaign,
    found: list[bg.Found],
    answers: list[list[bg.Connection]],
    own: set[str],
    *,
    truth: bool = False,
) -> int:
    """Keep the reading's connections for later (`BackstoryLinks`), as entry IDs. An end is a
    name of this reading or a confirmed public name of the campaign; a pair the campaign has
    already is left out before the cap. Returns how many wait."""
    store = getattr(bot, "backstory_links", None)
    if store is None or not any(answers):
        return 0
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    ends: dict[str, str] = {}
    for key, entries in lookup.by_key.items():
        for e in entries:
            if e.confirmed and not e.secret and not e.person:
                ends.setdefault(key, lookup.entities[e.entity_id].name)

    def entry(name: str) -> str | None:
        ids = {e.entity_id for e in lookup.by_key.get(name_key(name), ()) if not e.person}
        return next(iter(ids)) if len(ids) == 1 else None

    have = {
        (r.subject_id, r.predicate, r.object_id)
        for r in await memory.relations(campaign.guild_id, campaign.id, include_secret=True)
    }
    symmetric = await symmetric_kinds(memory, campaign)

    def known(c: bg.Connection) -> bool:
        a, b = entry(c.a), entry(c.b)
        if a is None or b is None:
            return True  # an end that is no entry of the campaign (a name that was refused)
        return (a, c.how, b) in have or (c.how in symmetric and (b, c.how, a) in have)

    kept = bg.keep_connections(answers, found, ends, cap=bg.MAX_CONNECTIONS, skip=known)
    rows = [
        NewLink(a, c.how, b, c.quote, c.secret, truth)
        for c in kept
        if (a := entry(c.a)) is not None and (b := entry(c.b)) is not None
    ]
    return int(await store.add(campaign.guild_id, campaign.id, rows)) if rows else 0


async def symmetric_kinds(memory: MemoryStore, campaign: Campaign) -> set[str]:
    onto = await memory.ontology(campaign.guild_id, campaign.id)
    return {p.key for p in onto.predicates.values() if p.symmetric}


async def ask_connections(interaction: discord.Interaction, campaign_id: str) -> None:
    """The 📜 button (on the names summary and on the 📇 Names card): the connections that are
    ready, or why none is."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    if not await offer_ready(interaction, campaign_id):
        await _tell(
            interaction,
            "None to check yet. Each connection waits until both its names are answered. "
            "Answer more names (✅ Add all, or 📝 Check new names), then press 📜 Check the "
            "connections again.",
        )


async def offer_ready(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The connections whose two names are answered, one at a time in a private message. Called
    when the DM has answered names (Add all, the end of 📝 Check new names) and by the 📜 button.
    Returns whether there was anything to ask."""
    try:
        view = ConnectionOffer(campaign_id)
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the connections from a background")
        return False


class ConnectionOffer(_Menu):
    """Keep each connection the DM's text states, one by one: ✅ Keep, 🤫 Keep as secret, ❌ No,
    or leave out the rest. They wait in the campaign's `backstory_links` until then, so a restart
    or a closed message loses nothing; only ones whose two names are answered are asked. Every
    card and every press reads them again: a connection to a refused name goes quietly, one to a
    merged name follows the merge, one the campaign has already is not asked. A kept connection is
    the DM's word (never proposed). One that the text wrote with a secret spelling can only be kept
    as secret."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.kept = 0
        self.left_out = 0
        self.current: Waiting | None = None
        self.left = 0
        self.shown: list[Waiting] = []
        self.total = 0
        self.words: dict[str, str] = {}
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        grey = discord.ButtonStyle.secondary
        self.keep = _Button(
            partial(self._answer, False), label="✅ Keep", style=discord.ButtonStyle.success
        )
        self.add_item(self.keep)
        self.add_item(_Button(partial(self._answer, True), label="🤫 Keep as secret", style=grey))
        self.add_item(_Button(self._no, label="❌ No", style=grey))
        self.add_item(_Button(self._skip, label="⏭️ Skip the rest", style=grey, row=1))

    async def _ready(self, interaction: discord.Interaction, campaign: Campaign) -> list[Waiting]:
        """The connections to ask now, oldest first (the queue read and cleaned again)."""
        memory = _memory(interaction)
        store = getattr(_bot(interaction), "backstory_links", None)
        if memory is None or store is None:
            return []
        onto = await memory.ontology(campaign.guild_id, campaign.id)
        self.words = {k: p.label for k, p in onto.predicates.items() if not p.core}
        symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
        waiting = await store.waiting(campaign.guild_id, campaign.id, symmetric=symmetric)
        return [w for w in waiting if w.ready]

    def _set(self, ready: list[Waiting]) -> None:
        self.shown = list(ready)
        self.left = len(ready)
        self.total = max(self.total, self.kept + self.left_out + self.left)
        self.current = ready[0] if ready else None
        self.keep.disabled = bool(self.current and self.current.secret)

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self._set(await self._ready(interaction, campaign))
        return self.current is not None

    def text(self) -> str:
        c = self.current
        if c is None:
            left = f" Left out {self.left_out}." if self.left_out else ""
            return f"📜 Done. Kept {self.kept} connection{'' if self.kept == 1 else 's'}.{left}"
        how = connection_words(c.predicate)
        if how == c.predicate.replace("_", " "):
            how = self.words.get(c.predicate, how)
        note = (
            "It uses a secret name, so players won't see it. Only 🤫 Keep as secret works."
            if c.secret
            else "🤫 Keep as secret hides it from players."
        )
        return (
            f"📜 **Connection {self.kept + self.left_out + 1} of {self.total}.** Keep this?\n"
            f"**{_md(c.subject)}** {how} **{_md(c.object)}**\n"
            f"From your text: \u201c{_md(logic.shorten(c.quote, QUOTE_SHOWN))}\u201d\n{note}"
        )

    async def _show(self, interaction: discord.Interaction, ready: list[Waiting]) -> None:
        self._set(ready)
        if self.current is None:
            self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=self.text(),
                view=self if self.current else None,
                allowed_mentions=NO_PINGS,
            )

    async def _dm(self, interaction: discord.Interaction) -> Campaign | None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return None
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return None
        return campaign

    async def _same(self, interaction: discord.Interaction, campaign: Campaign) -> Waiting | None:
        """The card the DM saw, read again; None (and the next card shown) if it changed."""
        ready = await self._ready(interaction, campaign)
        shown = self.current
        now = next(
            (
                w
                for w in ready
                if shown is not None
                and (w.subject_id, w.predicate, w.object_id)
                == (shown.subject_id, shown.predicate, shown.object_id)
            ),
            None,
        )
        if now is None:  # refused, merged, or kept elsewhere meanwhile: carry on with the next
            await self._show(interaction, ready)
            return None
        self._set(ready)
        return now

    async def _answer(self, secret: bool, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self.busy:
            campaign = await self._dm(interaction)
            memory = _memory(interaction)
            store = getattr(_bot(interaction), "backstory_links", None)
            if campaign is None or memory is None or store is None or self.current is None:
                return
            c = await self._same(interaction, campaign)
            if c is None:
                return
            # A card that turned secret since it was drawn (an end became a name the players
            # don't know) is kept as secret whichever button was pressed: never a public
            # connection naming a hidden end.
            try:
                kept = await memory.add_relation(
                    campaign.guild_id, campaign.id, c.subject_id, c.predicate, c.object_id,
                    source=DM, confidence=1.0, status=CONFIRMED, secret=secret or c.secret,
                    unrevealed=True,  # a backstory connection waits to be said at the table (#2404)
                )  # fmt: skip
                if c.truth:  # the DM called this upload 100% truth when they uploaded it (#2447)
                    await memory.pin(
                        campaign.guild_id, campaign.id, kept.value[0].id, True, source=DM
                    )
            except MemoryRuleError:
                log.info("A connection from a background was refused")
                await _tell(
                    interaction,
                    "DMbot couldn't keep that one. Press ❌ No to move on, or try again.",
                )
                return
            changed(interaction, campaign)
            await store.remove(campaign.guild_id, campaign.id, c)
            self.kept += 1
            await self._show(interaction, await self._ready(interaction, campaign))

    async def _no(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self.busy:
            campaign = await self._dm(interaction)
            store = getattr(_bot(interaction), "backstory_links", None)
            if campaign is None or store is None or self.current is None:
                return
            c = await self._same(interaction, campaign)
            if c is None:
                return
            await store.remove(campaign.guild_id, campaign.id, c)
            self.left_out += 1
            await self._show(interaction, await self._ready(interaction, campaign))

    async def _skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self.busy:
            campaign = await self._dm(interaction)
            store = getattr(_bot(interaction), "backstory_links", None)
            if campaign is None or store is None:
                return
            ready = await self._ready(interaction, campaign)
            seen = {(w.subject_id, w.predicate, w.object_id) for w in self.shown}
            # only what this message has listed: one that turned ready meanwhile is still unseen
            gone = [w for w in ready if (w.subject_id, w.predicate, w.object_id) in seen]
            await store.remove_all(campaign.guild_id, campaign.id, gone)
            self.left_out += len(gone)
            await self._show(interaction, [])


class BulkConfirm(_Menu):
    """Say yes to every name of a kind from this reading at once ("All 14 places"), undo it, or
    open the checking. Only the names this reading found: never an earlier reading's."""

    def __init__(self, campaign_id: str, stored: dict[str, list[str]], queued: int = 0) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.queued = queued  # connections waiting for these names to be answered
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
        if self.queued:
            self.add_item(
                _Button(
                    self._connections,
                    label="📜 Check the connections",
                    style=discord.ButtonStyle.secondary,
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

    async def _connections(self, interaction: discord.Interaction) -> None:
        await ask_connections(interaction, self.campaign_id)

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
        if written.value and self.queued:  # their names may be answered now
            await offer_ready(interaction, self.campaign_id)

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
