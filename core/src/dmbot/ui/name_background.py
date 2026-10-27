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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any, cast

import discord

from dmbot.ai import AIError
from dmbot.campaigns import Campaign
from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.memory import background as bg
from dmbot.memory import backstory_keep as keep
from dmbot.memory import kin, lore_context, lore_read, pronouns
from dmbot.memory.backstory_links import (
    DONE,
    FULL,
    UPLOAD_MAX,
    NewLink,
    NewWhich,
    Waiting,
    WhichWaiting,
)
from dmbot.memory.end_match import FILLER, bare
from dmbot.memory.link_words import link_words
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
from dmbot.waiting import WaitingMessage

log = logging.getLogger(__name__)

# No change for this long is named as a stall in the waiting message (#2564): a pass-1 call may
# take minutes (the careful model), so it is longer than the 10 s between edits by a good margin.
STALL_AFTER_S = 240.0


def message_is_gone(exc: BaseException) -> bool:
    """The DM dismissed the message, or its 15 minutes ran out: it cannot be edited any more."""
    return isinstance(exc, discord.NotFound) or (
        isinstance(exc, discord.HTTPException) and exc.code == 50027  # invalid webhook token
    )


NOT_A_BOOK = "your own notes or background, not a published book"
ONLY_DMS = "Only a DM of this campaign can do that."
QUOTE_SHOWN = 200  # a long quote must not push the buttons off a phone
MAX_BULK_BUTTONS = 4
CUT_NOTE = (
    "\nPart of this text couldn't be read in time, so some names or connections may be missing. "
    "Run the same background again to get the rest: names already found are skipped."
)
CUT_NOTHING = (
    "🤖 DMbot ran out of time reading {label}, so it has nothing to show yet. "
    "Nothing was added. Run it again in a minute."
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
    memory = _memory(interaction)
    choices = (
        await characters(memory, campaign)
        if memory is not None and pronouns.says_i(upload.text)
        else []
    )
    lone = memory is not None and not choices and pronouns.says_i(upload.text)
    await _send(
        interaction,
        offer_text(upload, False, ask=bool(choices)) + (NO_CHARACTERS if lone else ""),
        BackgroundOffer(campaign.id, upload, choices),
    )


TRUTH_OFF = "Lore: may change"
TRUTH_ON = "Lore: 📌 100% truth"


def offer_text(
    upload: Upload, truth: bool, *, ask: bool = False, speaker: str | None = None
) -> str:
    """The question before the AI reads a lore upload. Asked once per upload (#2447): lore that
    may change in play (the default), or 📌 100% truth, the DM's word, so DMbot tells the DM when
    a session goes against it."""
    cents = bg.estimate_cents(len(upload.text))
    how = (
        "📌 **100% truth.** Connections you keep are pinned, and DMbot tells you privately if a "
        "session goes against one."
        if truth
        else "**May change in play.** (Default)"
    )
    return (
        f"📜 **Find the names in {_md(upload.label)}?** Cost: {bg.cost_text(cents)} of your AI "
        "allowance. Nothing is added until you say yes.\n"
        "DMbot's AI suggests names and the connections your text states, each with its line. "
        "You check each one.\n\n"
        f"**Treat this lore as:** {how} Tap the lore button to switch.\n\n"
        + (whose_text(speaker) if ask else "")
        + f"{rights_text()}"
    )


NO_CHARACTERS = (
    '\n\nIt says "I" or "my", but no character is confirmed yet. Confirm the character first, '
    'or connections that say "I" are left out.'
)
NOT_ONE = "Not one character's"
NOT_ONE_VALUE = "-"


def whose_text(speaker: str | None) -> str:
    """The question for a text that says "I" or "my" (#2650). Never guessed: until the DM picks,
    a connection that needs "I" is left out and counted."""
    if speaker:
        return (
            f'**Whose backstory is this?** {_md(speaker)}\'s. "I" and "my" mean them. '
            "(Change it in the menu.)\n\n"
        )
    return (
        '**Whose backstory is this?** It says "I" or "my". Pick the character in the menu below. '
        'If you don\'t, connections that say "I" are left out.\n\n'
    )


async def characters(memory: MemoryStore, campaign: Campaign) -> list[tuple[str, str]]:
    """The campaign's confirmed characters (id, name) the DM may pick from, by name, at most as
    many as one menu holds. Players' own names (people) and every other kind are not offered."""
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    found = {
        e.id: e.name
        for e in lookup.entities.values()
        if e.type in pronouns.CHARACTER_TYPES and e.status == CONFIRMED and not e.merged_into
    }
    ordered = sorted(found.items(), key=lambda item: item[1].casefold())
    return ordered[: pronouns.MAX_CHOICES]


class BackgroundOffer(_Menu):
    def __init__(
        self, campaign_id: str, upload: Upload, choices: Sequence[tuple[str, str]] = ()
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.upload = upload
        self.choices = dict(choices)  # entity id -> name
        self.speaker: str | None = None  # whose backstory it is, once the DM says (#2650)
        if self.choices:
            self.add_item(self._select())
        self.truth = False  # asked once per upload (#2447): "may change in play" by default
        self.add_item(
            _Button(self._read, label="🤖 Find names", style=discord.ButtonStyle.primary, row=1)
        )
        self.switch = _Button(
            self._flip, label=TRUTH_OFF, style=discord.ButtonStyle.secondary, row=1
        )
        self.add_item(self.switch)
        self.add_item(
            _Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary, row=1)
        )

    def _select(self) -> discord.ui.Select[Any]:
        options = [discord.SelectOption(label=NOT_ONE, value=NOT_ONE_VALUE, default=True)] + [
            discord.SelectOption(label=logic.shorten(name, 25), value=entity_id)
            for entity_id, name in self.choices.items()
        ]
        select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Whose backstory is this?", options=options, row=0
        )
        select.callback = self._whose  # type: ignore[method-assign]
        self.whose = select
        return select

    async def _whose(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        picked = self.whose.values[0]
        self.speaker = self.choices.get(picked)
        for option in self.whose.options:
            option.default = option.value == picked
        await interaction.response.edit_message(
            content=offer_text(self.upload, self.truth, ask=True, speaker=self.speaker),
            view=self,
            allowed_mentions=NO_PINGS,
        )

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
            content=offer_text(
                self.upload, self.truth, ask=bool(self.choices), speaker=self.speaker
            ),
            view=self,
            allowed_mentions=NO_PINGS,
        )

    async def _read(self, interaction: discord.Interaction) -> None:
        clicked = time.monotonic()  # the elapsed time the DM sees starts here
        await interaction.response.defer()  # the checks below are database reads (#88, #351)
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        truth = self.truth  # as it was when Find names was pressed, not when the AI is done
        speaker = self.speaker
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
        reading_text = (
            f"🤖 Reading {_md(self.upload.label)}… this can take a few minutes for a long "
            "background."
        )
        self.stop()
        _ai_busy.add(guild)
        for old in [k for k in _ai_reads if k[1] != day]:
            del _ai_reads[old]
        _ai_reads[(guild, day)] = _ai_reads.get((guild, day), 0) + 1
        try:
            await interaction.edit_original_response(
                content=reading_text,
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
            aliases = await confirmed_aliases(memory, campaign)
            # Two passes (#2454): the careful model reads the whole text into a plain list, the
            # fast model shapes it, and the code checks every name and quote against the text.
            read_ai = getattr(bot, "background_read_ai", None)
            if read_ai is None:  # never for a real bot: said once so a misconfiguration shows
                log.warning("No careful-model reader for lore: pass 1 uses the fast model")
                read_ai = ai
            # (The read keeps its own deadline and returns what it shaped, marked cut.)
            skip_pair = await connected_pairs(memory, campaign)

            async def show(text: str) -> None:
                await interaction.edit_original_response(content=text, allowed_mentions=NO_PINGS)

            # The same message is kept current (#2559): the step, part n of m, then how long.
            async with WaitingMessage(
                show,
                reading_text,
                stall_after_s=STALL_AFTER_S,
                stops_at_s=lore_read.LORE_TIME_LIMIT_S,
                gone=message_is_gone,
                started=clicked,
            ) as waiting:

                def on_step(stage: str, done: int, total: int) -> None:
                    if stage == "reading":
                        again = f", try {done}" if done > 1 else ""
                        waiting.step(f"Step 1 of 2: reading it through{again}", keeps=False)
                    else:
                        waiting.step("Step 2 of 2: picking out names and links", done, total)

                read = await lore_read.read_lore(
                    read_ai,
                    ai,
                    self.upload.text,
                    own,
                    AI_AT_ONCE,
                    skip_name=lambda name: name_key(name) in known,
                    skip_pair=skip_pair,
                    progress=on_step,
                    aliases=aliases,
                )
            found = bg.merge(
                [read.names],
                known,
                rules=(campaign.target_ruleset, campaign.fallback_ruleset),
            )
            stored = await store(
                memory, campaign, found, getattr(bot, "lore_context", None), self.upload.text
            )
            questions: list[str] = []
            keys: set[tuple[str, str, str]] = set()
            queued = await queue_links(
                bot,
                memory,
                campaign,
                found,
                [read.links],
                set(own),
                truth=truth,
                dropped=read.stats.dropped,
                questions=questions,
                keys=keys,
                upload=upload_label(self.upload.label, time.time()),
                speaker=speaker,
            )
            ready = await count_ready(bot, campaign, keys)
            cut = read.cut
            log.info(
                "Background read: %d names and %d connections suggested%s; %d connection lines "
                "found, dropped for: %s",
                sum(map(len, stored.values())),
                queued,
                ", an answer cut off" if cut else "",
                read.stats.links_listed,
                ", ".join(
                    f"{k}={n}"
                    for k, n in sorted(read.stats.dropped.items())
                    if k != "quote_widened"  # (a line kept, not dropped: counted for the log only)
                )
                or "nothing",
            )  # (counts only: the questions are the DM's own words, shown only to them)
            await summary(
                interaction,
                campaign,
                self.upload.label,
                stored,
                queued,
                cut=cut,
                # (lines for connections that were already there are not "found" again)
                links_found=read.stats.links_listed - read.stats.dropped["already_connected"],
                ready=ready,
                dropped=read.stats.dropped,
                questions=questions,
            )
        except (AIError, CampaignError) as exc:  # CampaignError: the campaign is gone
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(content=str(exc))
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
    memory: MemoryStore,
    campaign: Campaign,
    found: list[bg.Found],
    held: lore_context.Held | None = None,
    text: str = "",
) -> dict[str, list[str]]:
    """Each name as a suggestion with its quote (never confirmed), its other spellings with it and
    a disguise kept as a secret name. One change for the whole list: one reload of the live names,
    one Undo. Returns the new suggestions' IDs for each kind. What is stored stays the short quote
    (the text is never kept, #2167); `held` gets the sentence around each name, cut from `text`
    while it is still in memory, for the review card to show (#2561): memory only, never stored."""
    names: list[NewName] = []
    items: list[bg.Found] = []
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
        items.append(item)
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
    for name, item, entity_id in zip(names, items, written.value, strict=False):
        if entity_id is not None:
            stored.setdefault(name.type, []).append(entity_id)
            if held is not None and text:
                held.put(
                    campaign.guild_id,
                    entity_id,
                    lore_context.around(text, item.quote, item.name),
                )
    return stored


def _plural(kind: str, count: int) -> str:
    one, many = bg.LABELS.get(kind, (kind, kind + "s"))
    return f"{count} {one if count == 1 else many}"


def usable_alias(key: str) -> bool:
    """Whether a spelling is distinctive enough to stand for a name on its own (#2762): after the
    titles and articles are taken off ("the King" is nothing, "the Ashen King" is "ashen"), it
    needs two words, or one of five letters or more. A bare title or a short word is shared by
    too many people to be a name."""
    core = [w for w in key.split() if w not in FILLER]
    return bool(core) and (len(core) >= 2 or len(core[0]) >= 5)


async def confirmed_aliases(memory: MemoryStore, campaign: Campaign) -> dict[str, tuple[str, ...]]:
    """Each confirmed public name of the campaign (its key) to the other confirmed public spellings
    that mean only it (#2762), so a document that says "the Ashen King" can back a link the AI
    wrote with the name. Every owner of a spelling is counted first (secret ones, ones not yet
    confirmed, kinds the players don't know and the table's own people included) and a spelling
    with more than one owner, or a secret one, is refused: nothing here may ever match the wrong
    name or touch a secret. A spelling too plain to name anyone (a bare title) is left out too."""
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    owners: dict[str, set[str]] = {}
    for key, entries in lookup.by_key.items():
        owners.setdefault(key, set()).update(e.entity_id for e in entries)
    for key, people in lookup.people_by_key.items():  # (a person at the table owns it too)
        owners.setdefault(key, set()).update(f"person:{e.entity_id}" for e in people)
    out: dict[str, list[str]] = {}
    for key, entries in lookup.by_key.items():
        if len(owners[key]) != 1 or not usable_alias(key):
            continue
        if not all(e.confirmed and not e.secret and not e.person for e in entries):
            continue
        (entity,) = owners[key]
        canonical = name_key(lookup.entities[entity].name)
        if key != canonical:
            out.setdefault(canonical, []).append(key)
    return {k: tuple(v) for k, v in out.items()}


def _waiting_note(queued: int) -> str:
    if not queued:
        return ""
    return CONNECTIONS_NOTE.format(count=queued, s="" if queued == 1 else "s")


# What became of each connection line, for the summary (#2642). Every reason `keep_connections`,
# `read_connections` and the lore read count falls in one of these three, and what is left over
# (a slice cut at the time limit, a line the model did not shape) is "not read", so the parts
# always add up to what was found.
KNOWN_ALREADY = ("same_pair", "already_have", "already_waiting")
LEFT_OUT = (
    "name_not_found", "which_one", "unreadable", "word_not_allowed", "bad_names", "not_in_text",
    "spelling_clash",
)  # fmt: skip
NOT_READ = ("cut_before_check", "over_cap")


def _found_note(
    found: int,
    kept: int,
    *,
    ready: int | None = None,
    dropped: Mapping[str, int] | None = None,
    questions: Sequence[str] = (),
) -> str:
    """What became of the connection lines DMbot saw in the text (#2563, #2642), so a shorter
    list is never a silent one: "Found 34 connections: 30 ready to check, 3 wait for names you
    haven't confirmed, 2 already known, 1 left out (Which Mara?)". The parts add up to `found`.
    A connection is kept only when its two ends are names and the words are really in your text;
    DMbot never guesses one. Counts, and the DM's own words for a question, shown to them only."""
    lost = dropped or {}
    if found <= kept and not lost.get("which_waiting", 0):
        return ""
    known = sum(lost.get(k, 0) for k in KNOWN_ALREADY)
    left = sum(lost.get(k, 0) for k in LEFT_OUT)
    whose = lost.get("whose", 0)
    picking = lost.get("which_waiting", 0)
    unread = sum(lost.get(k, 0) for k in NOT_READ)
    # whatever else is missing (cut at the time limit, not shaped) is "not read"
    unread += max(0, found - kept - known - left - whose - picking - unread)
    ready_n = kept if ready is None else min(ready, kept)
    waits = kept - ready_n
    asked = ", ".join(f"Which {_md(q)}?" for q in questions[:3])
    parts = [
        f"{ready_n} ready to check" if ready_n else "",
        f"{waits} wait for names you haven't confirmed" if waits else "",
        f"{known} already known" if known else "",
        f"{left} left out" + (f" ({asked})" if asked else "") if left else "",
        f'{whose} about "I" left out: upload again and say whose backstory it is' if whose else "",
        f"{picking} need your pick: press 📜 Check the connections" if picking else "",
        f"{unread} not read in time" if unread else "",
    ]
    return (
        f"\n🔎 Found {found} connection{'' if found == 1 else 's'}: "
        + ", ".join(x for x in parts if x)
        + ". DMbot keeps one only when both ends are names and the words are really in your "
        "text, so a few may be missing. You can add any you know from a name's card."
    )


async def summary(
    interaction: discord.Interaction,
    campaign: Campaign,
    label: str,
    stored: dict[str, list[str]],
    queued: int = 0,
    *,
    cut: bool = False,
    links_found: int = 0,
    ready: int | None = None,
    dropped: Mapping[str, int] | None = None,
    questions: Sequence[str] = (),
) -> None:
    total = sum(map(len, stored.values()))
    note = _found_note(links_found, queued, ready=ready, dropped=dropped, questions=questions)
    if not total:
        # Never "no names" after a cut-off: the reading did not finish, so nothing is known (#2454)
        await interaction.edit_original_response(
            content=(
                CUT_NOTHING.format(label=_md(label))
                if cut
                else f"🤖 DMbot found no new names in {_md(label)}. Nothing was added." + note
            ),
            allowed_mentions=NO_PINGS,
        )
        if queued:  # the names were all known already: the connections may be ready
            await offer_ready(interaction, campaign.id)
        return
    parts = ", ".join(
        _plural(k, len(ids)) for k, ids in sorted(stored.items(), key=lambda x: -len(x[1]))
    )
    view = BulkConfirm(campaign.id, stored, queued + (dropped or {}).get("which_waiting", 0))
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
            + note
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
    taken = {*bg.CONNECTIONS.values(), bg.OTHER}
    own = {
        p.key: p.label
        for p in onto.predicates.values()
        if not p.core and p.status == ACTIVE and p.key not in taken
    }
    return dict(sorted(own.items())[: bg.MAX_OWN_KINDS])


def upload_label(label: str, now: float) -> str:
    """The upload as the DM will see it on a card and in the DM-screen notice: its name and the
    day, "Tavin's backstory, 24 Oct" (#2650). Cut to what the queue keeps."""
    day = time.gmtime(now)
    when = f", {day.tm_mday} {time.strftime('%b', day)}"
    return logic.shorten(label, UPLOAD_MAX - len(when)) + when  # (the date is never cut off)


async def queue_links(
    bot: Any,
    memory: MemoryStore,
    campaign: Campaign,
    found: list[bg.Found],
    answers: list[list[bg.Connection]],
    own: set[str],
    *,
    truth: bool = False,
    dropped: bg.Dropped | None = None,
    questions: list[str] | None = None,
    keys: set[tuple[str, str, str]] | None = None,
    upload: str = "",
    speaker: str | None = None,
) -> int:
    """Keep the reading's connections for later (`BackstoryLinks`), as entry IDs. An end is a
    name of this reading or a confirmed public name of the campaign; a pair the campaign has
    already is left out before the cap. Returns how many wait. `questions` collects the ends that
    fit two names ("Which Mara?", #2642); `keys` collects the (subject, how, object) of each one
    queued, so the summary can say how many are ready."""
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

    picks: list[bg.Pick] = []
    kept = bg.keep_connections(
        answers, found, ends, cap=bg.MAX_CONNECTIONS, skip=known, dropped=dropped,
        questions=questions, speaker=speaker, picks=picks,
    )  # fmt: skip
    await _queue_picks(store, campaign, picks, entry, truth, upload, dropped)
    rows = [
        NewLink(a, c.how, b, c.quote, c.secret, c.words, truth, upload)
        for c in kept
        if (a := entry(c.a)) is not None and (b := entry(c.b)) is not None
    ]
    if not rows:
        return 0
    # A connection already waiting from an earlier reading is not new: it is counted as such (and
    # not as ready or waiting again), so the summary's parts add up (#2642 review).
    waiting_now = {
        (w.subject_id, w.predicate, w.object_id)
        for w in await store.waiting(campaign.guild_id, campaign.id, symmetric=symmetric)
    }
    fresh = [r for r in rows if (r.subject_id, r.predicate, r.object_id) not in waiting_now]
    if dropped is not None:
        dropped["already_waiting"] += len(rows) - len(fresh)
    if keys is not None:
        keys.update((r.subject_id, r.predicate, r.object_id) for r in fresh)
    return int(await store.add(campaign.guild_id, campaign.id, fresh)) if fresh else 0


async def _queue_picks(
    store: Any,
    campaign: Campaign,
    picks: list[bg.Pick],
    entry: Callable[[str], str | None],
    truth: bool,
    upload: str,
    dropped: bg.Dropped | None,
) -> int:
    """Queue the "Which Mara?" questions (#2650). One whose names are not entries of the campaign
    after all is counted as not found, so the summary's parts still add up."""
    rows: list[NewWhich] = []
    for p in picks:
        fixed = entry(p.fixed)
        ids = tuple(dict.fromkeys(i for n in p.candidates if (i := entry(n)) is not None))
        if fixed is None or len(ids) < 2:
            if dropped is not None:
                dropped["which_waiting"] -= 1
                dropped["name_not_found"] += 1
            continue
        rows.append(
            NewWhich(
                fixed, p.fixed_is_a, p.how, p.word, ids, p.quote, p.secret, p.words, truth, upload
            )
        )
    added = int(await store.add_which(campaign.guild_id, campaign.id, rows)) if rows else 0
    if dropped is not None and added < len(rows):  # already waiting, or past the most
        dropped["which_waiting"] -= len(rows) - added
        dropped["over_cap"] += len(rows) - added  # (or already waiting: either way not new)
    return added


async def count_ready(bot: Any, campaign: Campaign, keys: set[tuple[str, str, str]]) -> int | None:
    """Of the connections this reading queued (`keys`), how many are ready to check now: both
    names are confirmed. None when the queue can't be read (the summary then says less). Those
    are marked told here (#2651): the summary of this very upload says they are ready, so the DM
    screen does not say it again a few seconds later."""
    store = getattr(bot, "backstory_links", None)
    if store is None or not keys:
        return None
    try:
        waiting = await store.waiting(campaign.guild_id, campaign.id)
        ready = [w for w in waiting if w.ready and (w.subject_id, w.predicate, w.object_id) in keys]
        await store.mark_told(campaign.guild_id, campaign.id, [w.id for w in ready if w.id])
    except Exception:
        log.exception("Couldn't count the ready connections")
        return None
    return len(ready)


async def connected_pairs(memory: MemoryStore, campaign: Campaign) -> Callable[[str, str], bool]:
    """Whether two names of the campaign are already connected (by any connection, either way
    round): a second run of the same background leaves those lines out before they are shaped, so
    it reaches the part the first run did not (#2453)."""
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    have = {
        frozenset((r.subject_id, r.object_id))
        for r in await memory.relations(campaign.guild_id, campaign.id, include_secret=True)
    }

    def entry(name: str) -> str | None:
        ids = {e.entity_id for e in lookup.by_key.get(name_key(name), ()) if not e.person}
        return next(iter(ids)) if len(ids) == 1 else None

    def connected(a: str, b: str) -> bool:
        x, y = entry(a), entry(b)
        return x is not None and y is not None and frozenset((x, y)) in have

    return connected


async def symmetric_kinds(memory: MemoryStore, campaign: Campaign) -> set[str]:
    onto = await memory.ontology(campaign.guild_id, campaign.id)
    return {p.key for p in onto.predicates.values() if p.symmetric}


async def ask_connections(interaction: discord.Interaction, campaign_id: str) -> None:
    """The 📜 button (on the names summary, the 📇 Names card and the DM-screen notice): the "Which
    one?" questions, then the connections that are ready, or why none is."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    asked = await offer_which(interaction, campaign_id)
    if not await offer_ready(interaction, campaign_id) and not asked:
        await _tell(
            interaction,
            "None to check yet. Each connection waits until both its names are answered. "
            "Answer more names (✅ Add all, or 📝 Check new names), then press 📜 Check the "
            "connections again.",
        )


async def offer_which(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The "Which Mara?" questions, one at a time in a private message (#2650). Returns whether
    there was anything to ask."""
    try:
        view = WhichOffer(campaign_id)
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the which-one questions from a background")
        return False


class WhichOffer(_Menu):
    """A backstory connection with one end that fits two or more names ("Brannoc's sister Mara":
    Mara Ashgrove or Mara Voss) waits for the DM to say which, never guessed (#2650). One card at
    a time with a button for each name, ❌ Neither, and ⏭️ Not now (the rest wait). A pick makes
    the connection an ordinary waiting one: ready to check when both its names are answered."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.current: WhichWaiting | None = None
        self.picked = 0
        self.dropped = 0
        self.left = 0
        self.total = 0
        self.busy = asyncio.Lock()
        self.words: dict[str, str] = {}

    async def _waiting(
        self, interaction: discord.Interaction, campaign: Campaign
    ) -> list[WhichWaiting]:
        memory = _memory(interaction)
        store = getattr(_bot(interaction), "backstory_links", None)
        if memory is None or store is None:
            return []
        onto = await memory.ontology(campaign.guild_id, campaign.id)
        self.words = {k: p.label for k, p in onto.predicates.items() if not p.core}
        return list(await store.which_waiting(campaign.guild_id, campaign.id))

    def _set(self, waiting: list[WhichWaiting]) -> None:
        self.left = len(waiting)
        self.total = max(self.total, self.picked + self.dropped + self.left)
        self.current = waiting[0] if waiting else None
        self.clear_items()
        if self.current is None:
            return
        grey = discord.ButtonStyle.secondary
        for index, (_, name) in enumerate(self.current.candidates[:3]):
            self.add_item(
                _Button(
                    partial(self._pick, index),
                    label=logic.shorten(name, 25),
                    style=discord.ButtonStyle.primary,
                )
            )
        self.add_item(_Button(self._neither, label="❌ Neither", style=grey, row=1))
        self.add_item(_Button(self._not_now, label="⏭️ Not now", style=grey, row=1))

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self._set(await self._waiting(interaction, campaign))
        return self.current is not None

    def text(self) -> str:
        c = self.current
        if c is None:
            if not self.picked:
                return "🔎 Done. Nothing to check."
            return (
                f"🔎 Done. You picked {self.picked}, left out {self.dropped}. Picks are ready to "
                "check once their names are confirmed (press 📜 Check the connections)."
            )
        how = link_words(c.predicate, c.detail, kin.directional(c.detail))
        if how == c.predicate.replace("_", " "):
            how = self.words.get(c.predicate, how)
        asked = _md(bare(c.word))  # (the end as written stays in the quote line)
        pair = (
            f"**{_md(c.other)}** {how} **{asked}**"
            if c.other_is_subject
            else f"**{asked}** {how} **{_md(c.other)}**"
        )
        names = ", ".join(_md(name) for _, name in c.candidates[:3])
        place = f" ({self.picked + self.dropped + 1} of {self.total})" if self.total > 1 else ""
        return (
            f"🔎 **Which {asked} is this?**{place}\n"
            f"{pair}\n"
            f"From your text: \u201c{_md(logic.shorten(c.quote, QUOTE_SHOWN))}\u201d\n"
            f"It could be {names}. Tap the right one. Neither leaves this connection out."
        )

    async def _show(self, interaction: discord.Interaction, waiting: list[WhichWaiting]) -> None:
        self._set(waiting)
        if self.current is None:
            self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=self.text(),
                view=self if self.current else None,
                allowed_mentions=NO_PINGS,
            )

    async def _answer(self, interaction: discord.Interaction, index: int | None) -> None:
        """Pick candidate `index`, or None for Neither. The card is read again first: a question
        that went away or changed meanwhile moves on to the next one."""
        await interaction.response.defer()
        async with self.busy:
            campaign = await _campaign_for(interaction, self.campaign_id)
            if campaign is None:
                return
            if interaction.user.id not in campaign.dm_user_ids:  # on every press
                await _tell(interaction, ONLY_DMS)
                return
            store = getattr(_bot(interaction), "backstory_links", None)
            shown = self.current
            if store is None or shown is None:
                return
            now = await self._waiting(interaction, campaign)
            same = next((w for w in now if w.id == shown.id), None)
            if same is None or (
                index is not None
                and (index >= len(same.candidates) or same.candidates != shown.candidates)
            ):
                await self._show(interaction, now)  # it changed: carry on with what is there
                return
            chosen = None if index is None else same.candidates[index][0]
            result = await store.pick_which(campaign.guild_id, campaign.id, same.id, chosen)
            if result == FULL:
                await _tell(
                    interaction,
                    "Too many connections are waiting. Keep or skip some with 📜 Check the "
                    "connections first, then answer this one.",
                )
                return
            if result == DONE:
                if chosen is None:
                    self.dropped += 1
                else:
                    self.picked += 1
            changed(interaction, campaign)
            await self._show(interaction, await self._waiting(interaction, campaign))
        if self.current is None and self.picked:  # their connections may be ready now
            await offer_ready(interaction, self.campaign_id)

    async def _pick(self, index: int, interaction: discord.Interaction) -> None:
        await self._answer(interaction, index)

    async def _neither(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, None)

    async def _not_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=(
                    f"⏭️ Saved for later. {self.left} still waiting: press 📜 Check the "
                    "connections when you are ready."
                ),
                view=None,
                allowed_mentions=NO_PINGS,
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
        self.all_button = _Button(self._keep_all, label="✅ Keep all", style=grey, row=1)
        self.add_item(self.all_button)
        self.all_done: keep.KeptAll | None = None  # what Keep all kept, for Undo
        self.undo_button = _Button(self._undo_all, label="↩️ Undo", style=grey, row=1)

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

    @staticmethod
    def _this_upload(ready: list[Waiting]) -> list[Waiting]:
        """What Keep all keeps (#2650): the ready connections from the same upload as the first
        card, an old one (queued before uploads had names, upload "") counting as an upload of
        its own, so a labelled backstory is never kept unseen and neither is an old one."""
        return [w for w in ready if w.upload == ready[0].upload] if ready else []

    def _set(self, ready: list[Waiting]) -> None:
        mine = self._this_upload(ready)
        self.all_button.label = f"✅ Keep all {len(mine)}" if len(mine) > 1 else "✅ Keep all"
        self.all_button.disabled = len(mine) < 2
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
        how = link_words(c.predicate, c.detail, kin.directional(c.detail))
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
            + self._scope(c)
        )

    def _scope(self, c: Waiting) -> str:
        """Which connections ✅ Keep all keeps, when the card is from a named upload (#2650)."""
        mine = [w for w in self.shown if w.upload == c.upload] if c.upload else []
        if len(mine) < 2:
            return ""
        keeps = f"{len(mine)} from {_md(c.upload)}"
        return f"\n✅ Keep all keeps the {keeps}, and none from other uploads."

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
                    detail=c.detail,
                    directional=kin.directional(c.detail),  # "a is the parent of b" keeps its order
                    unrevealed=True,  # a backstory connection waits to be said at the table (#2404)
                )  # fmt: skip
            except MemoryRuleError:
                log.info("A connection from a background was refused")
                await _tell(
                    interaction,
                    "DMbot couldn't keep that one. Press ❌ No to move on, or try again.",
                )
                return
            if c.truth:  # the DM called this upload 100% truth when they uploaded it (#2447)
                try:
                    await memory.pin(
                        campaign.guild_id, campaign.id, kept.value[0].id, True, source=DM
                    )
                except MemoryRuleError:  # kept all the same: pinning is one tap on its card
                    log.info("A kept connection could not be pinned")
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

    async def _keep_all(self, interaction: discord.Interaction) -> None:
        """Keep every connection that is ready, in one press (#2642): the text is the DM's own.
        Secret ones are kept as secret, never made public. Undo takes them all back."""
        await interaction.response.defer()
        async with self.busy:
            campaign = await self._dm(interaction)
            memory = _memory(interaction)
            store = getattr(_bot(interaction), "backstory_links", None)
            if campaign is None or memory is None or store is None:
                return
            ready = await self._ready(interaction, campaign)
            seen = {(w.subject_id, w.predicate, w.object_id) for w in self.shown}
            # only what this message has listed: one that turned ready meanwhile is still unseen
            todo = [w for w in ready if (w.subject_id, w.predicate, w.object_id) in seen]
            if self.current is None:
                return
            todo = [w for w in todo if w.upload == self.current.upload]  # (this backstory only)
            self.all_done = await keep.keep_all(memory, store, campaign.guild_id, campaign.id, todo)
            changed(interaction, campaign)
            self.kept += len(self.all_done.kept)
            self.left_out += self.all_done.refused
            self.current = None
            self.clear_items()
            self.add_item(self.undo_button)
            refused = (
                f" {self.all_done.refused} could not be kept: check them one by one."
                if self.all_done.refused
                else ""
            )
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=(
                        f"📜 Kept {len(self.all_done.kept)} connection"
                        f"{'' if len(self.all_done.kept) == 1 else 's'}.{refused} "
                        "Press ↩️ Undo to take them all back."
                    ),
                    view=self,
                    allowed_mentions=NO_PINGS,
                )

    async def _undo_all(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self.busy:
            campaign = await self._dm(interaction)
            memory = _memory(interaction)
            store = getattr(_bot(interaction), "backstory_links", None)
            done = self.all_done
            if campaign is None or memory is None or store is None or done is None:
                return
            back = await keep.undo_all(memory, store, campaign.guild_id, campaign.id, done)
            changed(interaction, campaign)
            self.all_done = None
            self.kept -= back
            self.stop()
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=(
                        f"↩️ Took back {back} connection{'' if back == 1 else 's'}. They wait "
                        "again: press 📜 Check the connections to go through them."
                    ),
                    view=None,
                    allowed_mentions=NO_PINGS,
                )

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
