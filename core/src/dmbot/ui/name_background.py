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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, cast

import discord

from dmbot.ai import AIError
from dmbot.campaigns import Campaign
from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.memory import background as bg
from dmbot.memory import backstory_keep as keep
from dmbot.memory import (
    kin,
    lore_context,
    lore_knowledge,
    lore_read,
    pronouns,
    who_ask,
    who_resolve,
)
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.backstory_links import (
    DONE,
    FULL,
    UPLOAD_MAX,
    NewLink,
    NewWhich,
    Waiting,
    WhichWaiting,
)
from dmbot.memory.end_match import bare, usable_alias
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
    MoreNames,
    NewName,
    Relation,
    name_key,
)
from dmbot.memory.ontology import ACTIVE, DISGUISED_AS, FALLBACK_PREDICATE, check_named
from dmbot.memory.pronouns import is_pronoun
from dmbot.memory.store import MemoryStore
from dmbot.memory.which_else import COULD_NOT_KEEP, none_of_these
from dmbot.mentions import NO_PINGS
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _send, _tell
from dmbot.ui.name_lists import (
    AI_AT_ONCE,
    BUSY,
    LIMIT,
    LINK_MAX,
    NO_AI_FOR_DOCUMENTS,
    PASTE_MAX,
    Upload,
    end_ai_read,
    read_attachment,
    read_link_once,
    start_ai_read,
)
from dmbot.ui.names import (
    REVIEW_LABEL_MAX,
    _campaign_for,
    _md,
    _memory,
    changed,
    start_review,
)
from dmbot.ui.who_knows import player_characters
from dmbot.waiting import WaitingMessage, message_is_gone

log = logging.getLogger(__name__)

# No change for this long is named as a stall in the waiting message (#2564): a pass-1 call may
# take minutes (the careful model), so it is longer than the 10 s between edits by a good margin.
STALL_AFTER_S = 240.0
# The who-is-it lookups stop this long before the read's own time limit, so the summary can still
# be shown inside Discord's 15 minutes (#2790).
WHO_SUMMARY_MARGIN_S = 60.0


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


KNOWS_LABELS = {
    lore_knowledge.SPOILERS: "Players: don't know",
    lore_knowledge.WORLD: "Players: all know it",
    lore_knowledge.SMART: "Players: DMbot sorts",
}
KNOWS_TEXT = {
    lore_knowledge.SPOILERS: "**Players don't know it yet.** They find it out in play.",
    lore_knowledge.WORLD: "**Everyone knows it.** Every character knows all of it. (Default)",
    lore_knowledge.SMART: "**DMbot sorts it.** It guesses what everyone would know. If unsure, "
    "players don't know it yet.",
}
KNOWS_UNSORTED = (
    "\nDMbot couldn't sort what the players know, so they don't know any of it yet. Change it "
    "from a card with Who knows this?."
)
KNOWS_NO_PLAYERS = (
    "\nNo player character is confirmed yet, so nobody knows any of it yet. Confirm them, then "
    "use Who knows this? on a card."
)
TRUTH_OFF = "Lore: may change"
TRUTH_ON = "Lore: 📌 100% truth"


def offer_text(
    upload: Upload,
    truth: bool,
    *,
    ask: bool = False,
    speaker: str | None = None,
    knows: str = lore_knowledge.PICKED_FIRST,
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
        + (
            ""
            if speaker  # a character's backstory: the players' knowledge is not asked
            else f"**Who knows it:** {KNOWS_TEXT[lore_knowledge.normalize(knows)]} "
            "Tap the Players button to switch.\n\n"
        )
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
        # The choice to go back to when the DM picks no backstory after one (#3103).
        self.knows_before_speaker = lore_knowledge.PICKED_FIRST
        # ...and, when that is a player character, their entry id: what the kept connections start
        # as known by (#2935). Empty for an NPC's backstory or none picked.
        self.known_by = ""
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
        self.knows = lore_knowledge.PICKED_FIRST  # what the players know of it (#2959, #3098)
        self.knows_button = _Button(
            self._cycle,
            label=KNOWS_LABELS[self.knows],
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.add_item(self.knows_button)

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
        was_speaker = self.speaker is not None
        self.speaker = self.choices.get(picked)
        memory = _memory(interaction)
        pcs = {c for c, _ in await player_characters(memory, campaign)} if memory else set()
        self.known_by = picked if picked in pcs else ""
        # The three choices are for campaign lore: a character's backstory stays as it was (#2959)
        # A character's backstory is that character's own knowledge, never campaign lore, so it
        # is always spoilers here, whatever the form would otherwise start on (#3098).
        if self.speaker:
            if not was_speaker:  # remember the DM's choice before the backstory took it
                self.knows_before_speaker = self.knows
            self.knows = lore_knowledge.SPOILERS
        elif was_speaker:  # "not one": the form is back on what it was before the backstory
            self.knows = self.knows_before_speaker
        self.knows_button.label = KNOWS_LABELS[self.knows]
        self.knows_button.disabled = bool(self.speaker)
        for option in self.whose.options:
            option.default = option.value == picked
        await interaction.response.edit_message(
            content=offer_text(
                self.upload, self.truth, ask=True, speaker=self.speaker, knows=self.knows
            ),
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
                self.upload,
                self.truth,
                ask=bool(self.choices),
                speaker=self.speaker,
                knows=self.knows,
            ),
            view=self,
            allowed_mentions=NO_PINGS,
        )

    async def _cycle(self, interaction: discord.Interaction) -> None:
        """Not yet, then everyone, then a smart guess, then round again (#2959). Shown again."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, ONLY_DMS)
            return
        order = (lore_knowledge.SPOILERS, lore_knowledge.WORLD, lore_knowledge.SMART)
        self.knows = order[(order.index(self.knows) + 1) % len(order)]
        self.knows_button.label = KNOWS_LABELS[self.knows]
        await interaction.response.edit_message(
            content=offer_text(
                self.upload,
                self.truth,
                ask=bool(self.choices),
                speaker=self.speaker,
                knows=self.knows,
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
        knows = self.knows  # likewise
        speaker = self.speaker
        known_by = self.known_by
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
        reading_text = (
            f"🤖 Reading {_md(self.upload.label)}… this can take a few minutes for a long "
            "background."
        )
        why_not = start_ai_read(guild, time.time())
        if why_not == BUSY:
            await _tell(interaction, "DMbot is already reading a document for this server. "
                        "Try again when it's done.")  # fmt: skip
            return
        if why_not == LIMIT:
            await _tell(interaction, "This server has used today's document reads. Try again "
                        "tomorrow.")  # fmt: skip
            return
        self.stop()
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
            who_ai = getattr(bot, "who_ai", None)  # only with DMBOT_WHO_IS_IT on (#2790)
            steps = 3 if who_ai is not None else 2

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
                        # Same words for every pass-1 call: each call is movement, not a new step.
                        waiting.step(f"Step 1 of {steps}: reading it through", keeps=False)
                        waiting.moved()
                    elif stage == "looking":
                        waiting.step(
                            f"Step 1 of {steps}: looking again at passages it may have skipped",
                            done,
                            total,
                        )
                    elif stage == "sorting":
                        waiting.step("Sorting what the players know", done, total)
                    elif stage == "who":
                        # (counts only; each lookup moves the count, so a long run never looks
                        # stalled)
                        waiting.step(f"Step 3 of {steps}: working out who they mean", done, total)
                    else:
                        waiting.step(f"Step 2 of {steps}: picking out names and links", done, total)

                done = await read_background(
                    bot,
                    campaign,
                    self.upload,
                    truth=truth,
                    speaker=speaker,
                    known_by=known_by,
                    started=clicked,
                    progress=on_step,
                    choice=knows,
                )
            stored, queued, cut = done.stored, done.queued, done.cut
            await summary(
                interaction,
                campaign,
                self.upload.label,
                stored,
                queued,
                cut=cut,
                links_found=done.links_found,
                ready=done.ready,
                dropped=done.dropped,
                questions=done.questions,
                changes=done.changes,
                suggested=done.suggested,
                knowledge_note=done.knowledge_note,
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
            end_ai_read(guild)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. Nothing was sent or added.", view=None
        )


@dataclass(slots=True)
class BackgroundRead:
    """What one read of a lore text made, as counts and the DM's own question words (never the
    text): the names stored for checking, the connections queued, and what became of the lines."""

    stored: dict[str, list[str]]
    queued: int
    cut: bool
    links_found: int
    ready: int | None
    changes: int
    dropped: Mapping[str, int]
    questions: list[str]
    suggested: int = 0  # other names suggested for names the DM has, waiting for a yes (#3034)
    # What the DM should know about "who knows it" (#2959), else "": it could not be sorted, or no
    # player character is confirmed yet. Words only, nothing from the text.
    knowledge_note: str = ""


async def read_background(
    bot: Any,
    campaign: Campaign,
    upload: Upload,
    *,
    truth: bool,
    speaker: str | None,
    known_by: str,
    started: float,
    progress: Callable[[str, int, int], None] | None = None,
    choice: str = lore_knowledge.DEFAULT,
) -> BackgroundRead:
    """The read itself, with no Discord interaction: both passes over the text, the names stored
    as proposals and the connections queued for the DM's answers. Discord's 🤖 Find names and the
    website's Upload lore (#3022) both come through here, so they cannot drift apart. The caller
    has checked who may ask, the plan and the allowance, and shows the result. `progress(stage,
    done, total)` is told where it is with counts only: "reading", "looking", "shaping" and "who"
    (working out who a pronoun means). `started` is the monotonic time the wait began. `choice`
    (#2959) is what the upload says the player characters know: world general knowledge, spoilers
    (today's behaviour, the default) or the AI's call, statement by statement."""
    memory = bot.memory
    ai = bot.background_ai
    guild = campaign.guild_id
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
    # (No line is left out for its pair being connected already: a different kind between
    # them may be a change the DM wants to see. Only the same kind the same way round is
    # "already known", once the line is shaped: #3021.)
    who_ai = getattr(bot, "who_ai", None)  # only with DMBOT_WHO_IS_IT on (#2790)

    def on_who(done: int, total: int) -> None:
        if progress is not None:
            progress("who", done, total)

    read = await lore_read.read_lore(
        read_ai,
        ai,
        upload.text,
        own,
        AI_AT_ONCE,
        skip_name=lambda name: name_key(name) in known,
        progress=progress,
        aliases=aliases,
        hold_open=who_ai is not None,
        known_keys=known,
    )
    found = bg.merge(
        [read.names],
        known,
        rules=(campaign.target_ruleset, campaign.fallback_ruleset),
    )
    choice = lore_knowledge.normalize(choice)
    if speaker:  # a character's backstory stays as it was: the choices are for campaign lore
        choice = lore_knowledge.SPOILERS
    world_names, world_links, sorted_ok = await _world(
        choice,
        found,
        read.links,
        ai,
        progress,
        until=started + lore_read.LORE_TIME_LIMIT_S - WHO_SUMMARY_MARGIN_S,
    )
    pcs = await keep.player_character_ids(memory, campaign.guild_id, campaign.id)
    # "Everyone in the story" (#3034, Supervisor's ruling on PLAN item 1): a disguise or an earlier
    # name is just another name, and so are the connections written through it. Under "Only the DM"
    # and a smart guess it stays secret (DM authority).
    everyone = choice == lore_knowledge.WORLD
    secret_keys = frozenset() if everyone else read.secret_spellings
    if everyone:
        found = [
            replace(f, also=tuple(dict.fromkeys((*f.also, *f.secret))), secret=()) for f in found
        ]
    stored = await store(
        memory,
        campaign,
        found,
        getattr(bot, "lore_context", None),
        upload.text,
        known_by,
        world=world_names,
        # a disguise becomes a plain other name only when the DM said everything is known;
        # under the smart guess it stays secret whatever the AI says (DM authority)
        fold_secret=choice == lore_knowledge.WORLD,
    )
    await save_earlier_names(
        memory,
        campaign,
        read.known_spellings,
        secret_keys,
        getattr(bot, "lore_context", None),
        upload.text,
        known_by,
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
        upload=upload_label(upload.label, time.time()),
        speaker=speaker,
        known_by=known_by,
        unsettled=read.stats.unsettled or (),
        unnamed=read.unnamed,
        who_ai=who_ai,
        on_who=on_who,
        until=started + lore_read.LORE_TIME_LIMIT_S - WHO_SUMMARY_MARGIN_S,
        known_spellings=read.known_spellings,
        secret_spellings=secret_keys,
        world_links=world_links,
        world_all=choice == lore_knowledge.WORLD,
        world_level=lore_knowledge.level_of(choice),
    )
    ready = await count_ready(bot, campaign, keys)
    changes = await count_changes(bot, memory, campaign, keys)
    log.info(
        "Background read: %d names and %d connections suggested%s; %d connection lines "
        "found, dropped for: %s",
        sum(map(len, stored.values())),
        queued,
        ", an answer cut off" if read.cut else "",
        read.stats.links_listed,
        ", ".join(
            f"{k}={n}"
            for k, n in sorted(read.stats.dropped.items())
            if k != "quote_widened"  # (a line kept, not dropped: counted for the log only)
        )
        or "nothing",
    )  # (counts only: the questions are the DM's own words, shown only to them)
    try:
        suggested = len(await memory.suggested_names(campaign.guild_id, campaign.id))
    except Exception:  # only the count for the summary: the read itself is done
        log.exception("The suggested names could not be counted")
        suggested = 0
    note = ""
    if choice != lore_knowledge.SPOILERS:
        if not sorted_ok:
            note = KNOWS_UNSORTED
        elif not pcs:
            note = KNOWS_NO_PLAYERS
    return BackgroundRead(
        stored, queued, read.cut, read.stats.links_listed, ready, changes,
        read.stats.dropped, questions, suggested, note,
    )  # fmt: skip


def _link_key(a: str, how: str, b: str) -> tuple[str, str, str]:
    return (name_key(a), how, name_key(b))


async def _world(
    choice: str,
    found: Sequence[bg.Found],
    links: Sequence[bg.Connection],
    ai: Any,
    progress: Callable[[str, int, int], None] | None = None,
    until: float | None = None,
) -> tuple[set[str], set[tuple[str, str, str]], bool]:
    """What the upload's choice says every player character knows (#2959): the names (by key) and
    the connections (by their two names' keys and the kind), and whether it could all be sorted.
    Spoilers: nothing. World: all of it. Smart guess: the AI's call on one short line each (the
    kind, the name with its other names and disguises, and the quote the read kept), a spoiler
    wherever it does not say otherwise."""
    if choice == lore_knowledge.SPOILERS:
        return set(), set(), True

    def named(f: bg.Found) -> str:
        parts = [f"also called {', '.join(f.also)}" if f.also else ""]
        parts.append(f"hidden identity {', '.join(f.secret)}" if f.secret else "")
        extra = "; ".join(p for p in parts if p)
        return f.name + (f" ({extra})" if extra else "")

    lines = [lore_knowledge.line("name", named(f), f.quote) for f in found] + [
        lore_knowledge.line("connection", f"{c.a} — {c.how} — {c.b}", c.quote) for c in links
    ]

    def sorting(done: int, total: int) -> None:
        if progress is not None:
            progress("sorting", done, total)

    known, ok = await lore_knowledge.decide(choice, lines, ai, sorting, until)
    names = {name_key(f.name) for i, f in enumerate(found) if i in known}
    keys = {_link_key(c.a, c.how, c.b) for i, c in enumerate(links, len(found)) if i in known}
    return names, keys, ok


# The few helpers the suggested-names view shares with this module (public, so a sibling module
# can use them without reaching into private names).
campaign_for = _campaign_for
memory_of = _memory
markdown_safe = _md


async def save_earlier_names(
    memory: MemoryStore,
    campaign: Campaign,
    spellings: Mapping[str, Sequence[str]],
    secret: Collection[str],
    held: lore_context.Held | None = None,
    text: str = "",
    known_by: str = "",
) -> None:
    """The other names a lore text gives a name the campaign already has (#3034): a pseudonym, an
    earlier or birth name, a nickname. They are saved as suggestions on that entry (never a new
    character, never confirmed for the DM), secret when the text called the name a disguise.
    A spelling the entry has, or another entry uses, is left out by the store, so reading the
    same text twice adds nothing. A refusal is logged: the read goes on without them."""
    if not spellings:
        return
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    more: list[MoreNames] = []
    claims: dict[str, set[str]] = {}
    for main, found in spellings.items():
        for sp in found:
            claims.setdefault(name_key(sp), set()).add(name_key(main))
    for main, found in spellings.items():
        found = tuple(sp for sp in found if len(claims[name_key(sp)]) == 1)  # (not two names')
        ids = {e.entity_id for e in lookup.by_key.get(name_key(main), ()) if not e.person}
        if len(ids) != 1:
            continue  # (no such entry, or two: DMbot will not guess which one is meant)
        hidden = tuple(sp for sp in found if name_key(sp) in secret)
        shown = tuple(sp for sp in found if name_key(sp) not in secret)
        more.append(MoreNames(next(iter(ids)), shown, hidden, proposed=True))
    if not more:
        return
    try:
        waiting = {a.id for a, _ in await memory.suggested_names(campaign.guild_id, campaign.id)}
        await memory.add_names(campaign.guild_id, campaign.id, [], source=DM, more=more)
    except MemoryRuleError as exc:
        log.info("Skipped the earlier names of a background: %s", exc)
        return
    except Exception:  # the names of the read are stored already: this must not undo the read
        log.exception("The earlier names of a background could not be saved")
        return
    try:
        # only the names this call added: one an earlier upload suggested keeps its own knowers
        added = [
            a for a, _ in await memory.suggested_names(campaign.guild_id, campaign.id)
            if a.id not in waiting
        ]  # fmt: skip
    except Exception:
        log.exception("The suggested names could not be read")
        return
    if known_by:  # the uploader's character knows the secret names it gave (#2935), as store() does
        for alias in added:
            if alias.secret:
                await _secret_names_known_by(
                    memory, campaign, alias.entity_id, [alias.text], known_by
                )
    if held is None or not text:
        return
    for alias in added:  # the sentence around each, for its question (memory only)
        held.put(campaign.guild_id, alias.id, lore_context.around(text, "", alias.text))


async def store(
    memory: MemoryStore,
    campaign: Campaign,
    found: list[bg.Found],
    held: lore_context.Held | None = None,
    text: str = "",
    known_by: str = "",
    world: Collection[str] = (),
    fold_secret: bool = False,
) -> dict[str, list[str]]:
    """Each name as a suggestion with its quote (never confirmed), its other spellings with it and
    a disguise kept as a secret name. One change for the whole list: one reload of the live names,
    one Undo. Returns the new suggestions' IDs for each kind. What is stored stays the short quote
    (the text is never kept, #2167); `held` gets the sentence around each name, cut from `text`
    while it is still in memory, for the review card to show (#2561): memory only, never stored.
    `world` (#2959): the keys of the names the players know (the upload's choice); each is stored
    not held back from them, with its other names, so the old flags show it once the DM confirms
    it and the who-knows list follows the flags (nothing is written to that list here).
    `fold_secret`: only when the DM chose "everyone knows it", a disguise the text gives is just
    another public name.
    `known_by` (the uploader's player character, #2935): a secret name the upload gives starts as
    known by that character, as a kept connection does; with none, it is the DM's alone. That is
    set after the names, in changes of its own: Undo of the names removes the name and, with it,
    who knew it."""
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
        known = name_key(item.name) in world
        if known and fold_secret:  # the DM said all of it is known: a disguise is just a name
            item = replace(item, also=tuple(dict.fromkeys((*item.also, *item.secret))), secret=())
        names.append(
            NewName(
                item.name,
                item.kind,
                PROPOSED,
                others=item.also,
                secrets=item.secret,
                note=note,
                # (a name the choice calls known is stored not held back; the list follows when the
                # DM confirms it, at the hook's 1.0: only a connection's certainty is lowered for
                # a smart guess, #2959)
                shown=known,
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
            if known_by and item.secret:
                await _secret_names_known_by(memory, campaign, entity_id, item.secret, known_by)
    return stored


async def _secret_names_known_by(
    memory: MemoryStore, campaign: Campaign, entity_id: str, secrets: Sequence[str], known_by: str
) -> None:
    """The secret spellings this upload gave one entry start as known by the uploader's character
    (#2935). A refusal (a character since demoted) leaves the name with the DM alone, as before."""
    wanted = {name_key(s) for s in secrets}
    found = await memory.aliases(
        campaign.guild_id, campaign.id, entity_id=entity_id, include_secret=True
    )
    for alias in found:
        if not alias.secret or name_key(alias.text) not in wanted:
            continue
        try:
            await memory.set_alias_knowers(
                campaign.guild_id, campaign.id, alias.id, [known_by], source=DM
            )
        except MemoryRuleError:
            log.info("A secret name's first knowers were refused")
        except Exception:  # the names are stored: this only leaves the name with the DM alone
            log.exception("A secret name's first knowers could not be set")


def _plural(kind: str, count: int) -> str:
    one, many = bg.LABELS.get(kind, (kind, kind + "s"))
    return f"{count} {one if count == 1 else many}"


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
    "spelling_clash", "which_way", "bad_kind",
)  # fmt: skip
NOT_READ = ("cut_before_check", "over_cap")


def _found_note(
    found: int,
    kept: int,
    *,
    ready: int | None = None,
    dropped: Mapping[str, int] | None = None,
    questions: Sequence[str] = (),
    changes: int = 0,
    always: bool = False,
) -> str:
    """What became of the connection lines DMbot saw in the text (#2563, #2642), so a shorter
    list is never a silent one: "Found 34 connections: 30 ready to check, 3 wait for names you
    haven't confirmed, 2 already known, 1 left out (Which Mara?)". The parts add up to `found`.
    A connection is kept only when its two ends are names and the words are really in your text;
    DMbot never guesses one. Counts, and the DM's own words for a question, shown to them only."""
    lost = dropped or {}
    if found <= kept and not lost.get("which_waiting", 0) and not changes and not always:
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
    changing = min(changes, ready_n)  # (a change ends a live connection, so its ends are real)
    ready_text = (
        f"{ready_n} ready to check ({ready_n - changing} new, {changing} "
        f"change{'' if changing == 1 else 's'})"
        if changing and ready_n > changing
        else f"{changing} change{'' if changing == 1 else 's'} ready to check"
        if changing
        else f"{ready_n} ready to check"
    )
    asked = ", ".join(f"Which {_md(q)}?" for q in questions[:3])
    parts = [
        ready_text if ready_n else "",
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
    changes: int = 0,
    suggested: int = 0,
    knowledge_note: str = "",
) -> None:
    total = sum(map(len, stored.values()))
    links_waiting = queued + (dropped or {}).get("which_waiting", 0)
    waiting = links_waiting + suggested  # (the 📜 button shows for names too)
    names_note = (
        f"\n🔎 {suggested} other name{'' if suggested == 1 else 's'} to check: press 📜 Check "
        "the connections. Nothing uses a name until you say yes."
        if suggested
        else ""
    )
    note = (knowledge_note if total or waiting else "") + _found_note(
        links_found,
        queued,
        ready=ready,
        dropped=dropped,
        questions=questions,
        changes=changes,
        always=not total and bool(links_waiting),  # (names alone are no 'Found 0 connections')
    )
    if not total:
        # Never "no names" after a cut-off: the reading did not finish, so nothing is known (#2454)
        # and never "Nothing was added" while connections wait for a yes (#3021)
        await interaction.edit_original_response(
            content=(
                CUT_NOTHING.format(label=_md(label))
                if cut
                else f"🤖 DMbot found no new names in {_md(label)}."
                + (
                    note
                    + names_note
                    + (
                        "\nPress 📜 Check the connections to look at them one by one."
                        if ready is None
                        or ready
                        or suggested
                        or (dropped and dropped.get("which_waiting"))
                        else ""
                    )
                    if waiting
                    else " Nothing was added." + note
                )
            ),
            view=BulkConfirm(campaign.id, stored, waiting) if waiting and not cut else None,
            allowed_mentions=NO_PINGS,
        )
        if queued:  # the names were all known already: the connections may be ready
            await offer_ready(interaction, campaign.id)
        return
    parts = ", ".join(
        _plural(k, len(ids)) for k, ids in sorted(stored.items(), key=lambda x: -len(x[1]))
    )
    view = BulkConfirm(campaign.id, stored, waiting)
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
            + names_note
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
    known_by: str = "",
    unsettled: Sequence[bg.Unsettled] = (),
    unnamed: Sequence[who_resolve.UnnamedLink] = (),
    who_ai: who_ask.Client | None = None,
    on_who: Callable[[int, int], None] | None = None,
    until: float | None = None,
    known_spellings: Mapping[str, Sequence[str]] | None = None,
    secret_spellings: Collection[str] = (),
    world_links: Collection[tuple[str, str, str]] = (),
    world_all: bool = False,
    world_level: int = lore_knowledge.SURE,
) -> int:
    """Keep the reading's connections for later (`BackstoryLinks`), as entry IDs. An end is a
    name of this reading or a confirmed public name of the campaign; a pair the campaign has
    already is left out before the cap. Returns how many wait. `questions` collects the ends that
    fit two names ("Which Mara?", #2642); `keys` collects the (subject, how, object) of each one
    queued, so the summary can say how many are ready.

    `unsettled` (#2790): the open pronouns of a lore read, settled here with `who_ai` once the
    campaign's names are known: a settled one joins the connections, a doubt becomes a "Which
    one?" question, and one nobody can name becomes a relationship with an unnamed person. Every
    one is counted in `dropped` or kept; none is lost without a count.

    `known_spellings` (#2841): new public spellings the text gives names the campaign already has
    (name -> spellings); an end written with one finds that name. A spelling two names both claim
    is no end. `secret_spellings`: every disguise this read listed (new names' and known names'):
    a connection found through one is kept as secret like any other that touches a disguise.

    `world_links` (#2959): the connections (their two names' keys and the kind) the players know
    once kept, by the upload's choice; `world_all`: all of them (the DM said everything is known),
    which also covers the ones made from a settled pronoun, a "Which one?" question and an unnamed
    person. `world_level`: how they are known (`lore_knowledge.SURE`, or `GUESS` for the AI's)."""
    store = getattr(bot, "backstory_links", None)
    if store is None or not (any(answers) or unsettled or unnamed):
        return 0
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    ends: dict[str, str] = {}
    for key, entries in lookup.by_key.items():
        for e in entries:
            if e.confirmed and not e.secret and not e.person:
                ends.setdefault(key, lookup.entities[e.entity_id].name)
    owners: dict[str, set[str]] = {}
    for main, spellings in (known_spellings or {}).items():
        for sp in spellings:
            owners.setdefault(name_key(sp), set()).add(name_key(main))
    for main, spellings in (known_spellings or {}).items():
        have_name = ends.get(name_key(main))
        for sp in spellings:
            if (
                have_name is not None
                and len(owners[name_key(sp)]) == 1
                and usable_alias(name_key(sp))
            ):
                ends.setdefault(name_key(sp), have_name)

    def entry(name: str) -> str | None:
        ids = {e.entity_id for e in lookup.by_key.get(name_key(name), ()) if not e.person}
        return next(iter(ids)) if len(ids) == 1 else None

    have = {
        (r.subject_id, r.predicate, r.object_id)
        for r in await memory.relations(campaign.guild_id, campaign.id, include_secret=True)
        if r.to_session_at is None  # (an ended one can be offered again: the DM may change back)
    }
    symmetric = await symmetric_kinds(memory, campaign)

    def known(c: bg.Connection) -> bool:
        a, b = entry(c.a), entry(c.b)
        if a is None or b is None:
            return True  # an end that is no entry of the campaign (a name that was refused)
        return (a, c.how, b) in have or (c.how in symmetric and (b, c.how, a) in have)

    settled = who_resolve.Settled()
    if unsettled:
        if who_ai is not None:
            names = [*dict.fromkeys([*ends.values(), *(f.name for f in found)])]
            settled = await who_resolve.settle(
                unsettled, who_ai, names, until=until, progress=on_who
            )
        else:  # (the check was switched off: left out and counted, as before it existed)
            settled.unresolved = len(unsettled)
        if dropped is not None:
            dropped["not_in_text"] += settled.unresolved
        answers = [*answers, settled.connections]
    # People the text describes but never names (#2959) go the way the who-is-it check's do: the
    # same store call, the same secrecy (below) and the same cap.
    settled.unnamed = [*settled.unnamed, *unnamed]
    picks: list[bg.Pick] = []
    # (the campaign's own secret names stay secret whatever the upload's choice: the choice only
    # frees the earlier names this read found)
    campaign_secrets = {
        key for key, entries in lookup.by_key.items() if any(e.secret for e in entries)
    }
    kept = bg.keep_connections(
        answers, found, ends, cap=bg.MAX_CONNECTIONS, skip=known, dropped=dropped,
        questions=questions, speaker=speaker, picks=picks,
        secret_keys={*secret_spellings, *campaign_secrets},
    )  # fmt: skip
    # A pronoun-resolved question or unnamed person is secret as a connection would be: when its
    # named end is a secret spelling, of this reading or of the campaign (`keep_connections` does
    # the same for the lines it keeps), so a disguise is never tied to its real name in public.
    hidden = {name_key(x) for f in found for x in f.secret} | {
        key for key, entries in lookup.by_key.items() if any(e.secret for e in entries)
    }
    asked_before = {(name_key(p.fixed), p.how, p.word.casefold(), p.fixed_is_a) for p in picks}
    for p in settled.picks:
        again = (name_key(p.fixed), p.how, p.word.casefold(), p.fixed_is_a)
        if again in asked_before or len(picks) >= bg.MAX_PICKS:
            if dropped is not None:
                dropped["same_pair" if again in asked_before else "over_cap"] += 1
            continue
        asked_before.add(again)
        picks.append(replace(p, secret=name_key(p.fixed) in hidden))
        if dropped is not None:
            dropped["which_waiting"] += 1  # (`_queue_picks` takes back any lost)
    settled.unnamed = [replace(u, secret=name_key(u.fixed) in hidden) for u in settled.unnamed]
    await _queue_picks(
        store,
        campaign,
        picks,
        entry,
        truth,
        upload,
        dropped,
        known_by,
        world_links,
        world_level if world_all else 0,
    )
    onto = await memory.ontology(campaign.guild_id, campaign.id)

    def fitted(c: bg.Connection, a_id: str, b_id: str) -> str | None:
        """The kind this connection may have between these two entries (#3035): its own when the
        ends fit it, "is in" for a place in a place that the AI called a membership, else None.
        A campaign's own kind and the catch-all take any ends."""
        term = onto.predicates.get(c.how)
        if term is None or not term.core or c.how == FALLBACK_PREDICATE:
            return c.how
        a_type, b_type = lookup.entities[a_id].type, lookup.entities[b_id].type
        if onto.is_any(a_type, term.subject_types) and onto.is_any(b_type, term.object_types):
            return c.how
        if c.how == "member_of" and a_type == "place" and b_type == "place":
            return "located_in"
        return None

    # (a kept connection may name an end by another spelling than the one the read listed: both
    # are compared by the entry they point to, either way round)
    world_ids = {
        (x, how, y)
        for ka, how, kb in world_links
        if (x := entry(ka)) is not None and (y := entry(kb)) is not None
    }
    rows = []
    for c in kept:
        if (a := entry(c.a)) is None or (b := entry(c.b)) is None:
            continue
        how = fitted(c, a, b)
        if how is None:  # the ends do not fit the kind: counted, never stored
            if dropped is not None:
                dropped["bad_kind"] += 1
            continue
        world = (
            world_level
            if world_all
            or (a, c.how, b) in world_ids
            or (c.how in symmetric and (b, c.how, a) in world_ids)
            else 0
        )
        if how == DISGUISED_AS:  # a disguise is a plot secret by nature, under every choice (#3091)
            world = 0
        rows.append(
            NewLink(
                a,
                how,
                b,
                c.quote,
                c.secret or how == DISGUISED_AS,
                c.words,
                truth,
                upload,
                c.decided,
                known_by,
                world,
            )
        )
    if settled.unnamed:  # (nothing here runs when the check is off: the rows are as they were)
        room = max(0, bg.MAX_CONNECTIONS - len(rows))
        if len(settled.unnamed) > room and dropped is not None:
            dropped["over_cap"] += len(settled.unnamed) - room  # (counted; no entry is made)
        made_rows = await _unnamed_rows(
            memory,
            campaign,
            settled.unnamed[:room],
            entry,
            truth,
            upload,
            dropped,
            known_by,
            world_level if world_all else 0,
        )
        held = {(r.subject_id, r.predicate, r.object_id) for r in rows}
        for r in made_rows:  # (two lines about one unnamed person are one row)
            row_key = (r.subject_id, r.predicate, r.object_id)
            if row_key in held:
                if dropped is not None:
                    dropped["same_pair"] += 1
                continue
            held.add(row_key)
            rows.append(r)
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


async def _unnamed_rows(
    memory: MemoryStore,
    campaign: Campaign,
    links: Sequence[who_resolve.UnnamedLink],
    entry: Callable[[str], str | None],
    truth: bool,
    upload: str,
    dropped: bg.Dropped | None,
    known_by: str = "",
    world: int = 0,
) -> list[NewLink]:
    """The relationships kept with a person the read could not name (#2790): each makes (or finds)
    the unnamed entry, hung off the named end when the story gave a relationship word. A named end
    that is not an entry of the campaign after all is counted as not found."""
    rows: list[NewLink] = []
    read: dict[str, str] = {}  # this read's own record: two "a hooded figure" are one person
    for u in links:
        fixed = entry(u.fixed)
        if fixed is None:
            if dropped is not None:
                dropped["name_not_found"] += 1
            continue
        try:
            added = await memory.add_unnamed(
                campaign.guild_id,
                campaign.id,
                what=u.what,
                anchor_id=fixed if u.anchored else None,
                role=u.role[: unnamed_mod.MAX_ROLE],
                # ("someone" is never joined to another "someone": two pronouns for nobody
                # are two people unless the story gives a real shared descriptor)
                read=None if u.what == who_resolve.SOMEONE else read,
            )
        except MemoryRuleError:  # an anchor that went away meanwhile, or no word: counted
            if dropped is not None:
                dropped["name_not_found"] += 1
            continue
        other = added.entity.id
        a, b = (fixed, other) if u.fixed_is_a else (other, fixed)
        rows.append(
            NewLink(
                a, u.how, b, u.quote, u.secret, u.words, truth, upload, u.decided, known_by, world
            )
        )
    return rows


async def _queue_picks(
    store: Any,
    campaign: Campaign,
    picks: list[bg.Pick],
    entry: Callable[[str], str | None],
    truth: bool,
    upload: str,
    dropped: bg.Dropped | None,
    known_by: str = "",
    world_links: Collection[tuple[str, str, str]] = (),
    world_all: int = 0,
) -> int:
    """Queue the "Which Mara?" questions (#2650). `world_all`: how every one is known when the
    upload says all of it is (0: by `world_links`, one by one). One whose names are not entries
    of the campaign after all is counted as not found, so the summary's parts still add up."""
    rows: list[NewWhich] = []
    for p in picks:
        fixed = entry(p.fixed)
        ids = tuple(dict.fromkeys(i for n in p.candidates if (i := entry(n)) is not None))
        if fixed is None or len(ids) < (1 if is_pronoun(p.word) else 2):
            if dropped is not None:
                dropped["which_waiting"] -= 1
                dropped["name_not_found"] += 1
            continue
        rows.append(
            NewWhich(
                fixed,
                p.fixed_is_a,
                p.how,
                p.word,
                ids,
                p.quote,
                p.secret,
                p.words,
                truth,
                upload,
                p.decided,
                known_by,
                world_all
                or (
                    lore_knowledge.GUESS
                    if (
                        _link_key(p.fixed, p.how, p.word)
                        if p.fixed_is_a
                        else _link_key(p.word, p.how, p.fixed)
                    )
                    in world_links
                    else 0
                ),
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


async def count_changes(
    bot: Any, memory: MemoryStore, campaign: Campaign, keys: set[tuple[str, str, str]]
) -> int:
    """Of the connections this reading queued (`keys`), how many are changes: they would end a
    connection the campaign has (#3021). 0 when the queue can't be read."""
    store = getattr(bot, "backstory_links", None)
    if store is None or not keys:
        return 0
    try:
        waiting = await store.waiting(campaign.guild_id, campaign.id)
        mine = [w for w in waiting if (w.subject_id, w.predicate, w.object_id) in keys]
        return len(await keep.changes_among(memory, campaign.guild_id, campaign.id, mine))
    except Exception:
        log.exception("Couldn't count the changed connections")
        return 0


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
        # (A pronoun's question never loses the connection: "none of these" keeps it with a person
        # who is not named yet, #2790.)
        label = "🙋 Someone else" if self._is_pronoun(self.current) else "❌ Neither"
        self.add_item(_Button(self._neither, label=label, style=grey, row=1))
        self.add_item(_Button(self._not_now, label="⏭️ Not now", style=grey, row=1))

    @staticmethod
    def _is_pronoun(w: WhichWaiting) -> bool:
        return is_pronoun(bare(w.word))

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
            + who_resolve.checked_line(c.decided)
            + (
                f"It could be {names}. Tap the right one. "
                if names
                else "No name it could be is left. "
            )
            + (
                "Someone else keeps the connection with a person who isn't named yet."
                if self._is_pronoun(c)
                else "Neither leaves this connection out."
            )
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
            memory = _memory(interaction)
            if index is None:
                if memory is None:
                    return
                result = await none_of_these(memory, store, campaign.guild_id, campaign.id, same)
                chosen = None
                if result == COULD_NOT_KEEP:  # the question stays, nothing is lost
                    await _tell(interaction, "DMbot couldn't keep that one. It stays waiting.")
                    return
                kept_unnamed = is_pronoun(bare(same.word)) and result == DONE
            else:
                chosen = same.candidates[index][0]
                kept_unnamed = False
                result = await store.pick_which(campaign.guild_id, campaign.id, same.id, chosen)
            if result == FULL:
                await _tell(
                    interaction,
                    "Too many connections are waiting. Keep or skip some with 📜 Check the "
                    "connections first, then answer this one.",
                )
                return
            if result == DONE:
                if chosen is None and not kept_unnamed:
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
        from dmbot.ui.name_questions import offer_names  # (it imports this module)
        from dmbot.ui.same_person_questions import offer_same_person
        from dmbot.ui.worked_out_questions import offer_worked_out

        asked_names = await offer_names(interaction, campaign_id)  # suggested other names first
        asked_names = await offer_worked_out(interaction, campaign_id) or asked_names
        asked_names = await offer_same_person(interaction, campaign_id) or asked_names
        view = ConnectionOffer(campaign_id)
        if not await view.load(interaction):
            return asked_names
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
        self.characters: dict[str, str] = {}  # player characters (id -> name), for the default
        # the waiting connections that change one the campaign has (key -> what they would end)
        self.changes: dict[tuple[str, str, str], list[Relation]] = {}
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
        self.characters = dict(await player_characters(memory, campaign))
        symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
        waiting = await store.waiting(campaign.guild_id, campaign.id, symmetric=symmetric)
        ready = [w for w in waiting if w.ready]
        self.changes = await keep.changes_among(memory, campaign.guild_id, campaign.id, ready)
        return ready

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
        self.keep.label = "✅ Change it" if self.current and self._old(self.current) else "✅ Keep"

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self._set(await self._ready(interaction, campaign))
        return self.current is not None

    def _old(self, c: Waiting) -> list[Relation]:
        return self.changes.get((c.subject_id, c.predicate, c.object_id), [])

    def _change_line(self, c: Waiting, how: str) -> str:
        """What is there now, then what the text says. Nothing changes until the DM says yes, and
        the old connection is ended, not deleted (#3021)."""
        old = ", ".join(
            self.words.get(r.predicate, r.predicate.replace("_", " ")) for r in self._old(c)
        )
        return (
            f"🔁 Now: **{_md(c.subject)}** {_md(old)} **{_md(c.object)}**\n"
            f"Your text: **{_md(c.subject)}** {how} **{_md(c.object)}**\n"
            "✅ Change it swaps the old one for this. Nothing changes until you press it.\n"
        )

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
        who = self.characters.get(c.known_by)
        if who is not None:
            mine = _md(who[:40])
            note += (
                f"\n👥 ✅ Keep: you and **{mine}**'s character know it."
                if not c.secret
                else f"\n👥 **{mine}**'s character won't know it: it uses a secret name."
            )
        if self._old(c):
            return (
                f"📜 **Connection {self.kept + self.left_out + 1} of {self.total}.** "
                "Change this?\n"
                + self._change_line(c, how)
                + f"From your text: \u201c{_md(logic.shorten(c.quote, QUOTE_SHOWN))}\u201d\n"
                + who_resolve.checked_line(c.decided)
                + f"{note}"
                + self._scope(c)
            )
        return (
            f"📜 **Connection {self.kept + self.left_out + 1} of {self.total}.** Keep this?\n"
            f"**{_md(c.subject)}** {how} **{_md(c.object)}**\n"
            f"From your text: \u201c{_md(logic.shorten(c.quote, QUOTE_SHOWN))}\u201d\n"
            + who_resolve.checked_line(c.decided)
            + f"{note}"
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
            old = self._old(c)
            now = int(time.time())
            start = keep.change_start(old, now)
            ended: list[int | None] = []
            try:
                shown = await keep.shown_entry_ids(memory, campaign.guild_id, campaign.id)
                await keep.end_replaced(memory, campaign.guild_id, campaign.id, old, start, ended)
                kept = await memory.add_relation(
                    campaign.guild_id, campaign.id, c.subject_id, c.predicate, c.object_id,
                    source=DM, confidence=1.0, status=CONFIRMED, secret=secret or c.secret,
                    detail=c.detail,
                    directional=kin.directional(c.detail),  # "a is the parent of b" keeps its order
                    # a backstory connection waits to be said at the table (#2404), unless the
                    # upload said the players know it and both its ends are shown (#2959)
                    unrevealed=keep.kept_unrevealed(c, secret or c.secret, shown),
                    # ✅ Keep: the uploader's character knows it from the start; 🤫 Keep as secret
                    # is the DM's alone (#2935)
                    known_by=[c.known_by] if c.known_by and not (secret or c.secret) else (),
                    from_session_at=start if old else None,  # (starts as the old one ends)
                    # a secret SPELLING in the text never hides what the players were shown (#3025);
                    # the DM's own 🤫 button is their word and does
                    keep_flags=not secret,
                )  # fmt: skip
            except Exception as exc:
                # a change is whole or not at all, whatever went wrong
                await keep.undo_batches(memory, campaign.guild_id, campaign.id, ended)
                if not isinstance(exc, MemoryRuleError):
                    raise
                log.info("A connection from a background was refused")
                await _tell(
                    interaction,
                    "DMbot couldn't keep that one. Press ❌ No to move on, or try again.",
                )
                return
            made = kept.value[0]
            # (only a connection made just now: an older twin's rows are the DM's, not a guess's)
            if c.world == lore_knowledge.GUESS and not made.unrevealed and made.created_at >= now:
                await keep.mark_guess(
                    memory, campaign.guild_id, campaign.id, "connection", [made.id]
                )
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
        if self.ids:
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
        await start_review(interaction, self.campaign_id, bulk=False)

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
