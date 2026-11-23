"""The DM's after-session questions about what was said at the table (#1297): "**Bram** carries
**Heartseeker**? ✅ ❌". What the story extractor read out of a session is only a proposal; here
the DM turns each into a fact (✅) or turns it down for good (❌). Private to the campaign's DM,
never in a transcript and never shown to players. Grouped by who they are about, a few at a time.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.discord_limits import shorten
from dmbot.memory import party_icons
from dmbot.memory.claims import (
    GONE,
    ODD_NOW,
    PAGE,
    STOPPED_NOW,
    Claim,
    ClaimStore,
    is_condition,
    says_travels_with,
)
from dmbot.memory.link_words import TRAVELS_WITH_MEANS
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    HOW_DM,
    Entity,
    MemoryRuleError,
    TooLateToUndo,
    name_key,
)
from dmbot.memory.store import companion_refusal
from dmbot.ui.check_everything import add_stop, next_part, progress
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.name_card import FLAG_WHY
from dmbot.ui.names import _campaign_for, _md, _memory
from dmbot.walk import Walk

NOTHING_WAITING = "✅ Nothing left to check. DMbot will ask again after the next session."
FINISHED = "✅ What was said: checked."  # (the line a "Check everything" walk leaves, #4192)
ODD_PAIR = "DMbot noticed something odd about it"
REFUSAL_MAX = (
    160  # a store's reason in the summary: the page and the summary share 2,000 characters
)
YES, NO, HIDE, SOME = "✅", "❌", "🤫", "👥"
# A held-back connection the table stated, now shown to the players (#3963 c): said every time.
SHOWN_NOW = "👁️ Your players can see this now: it was said at the table."
# Kept hidden beside a connection the story already hides between the same two (#3975, #3979).
HELD_NOW = (
    "🙈 Kept hidden from your players, so it can't give away another hidden connection between "
    "these two names. To show it: open either name, ✏️ Edit a connection…, 👁️ Who knows this?, "
    "then 👁️ Everyone at the table."
)

# Said while the game clock had the party split (#2996 rule 1, slice 2a): the group that was there
# knows it; when DMbot can't tell which group, nobody does yet (never a guess).
SPLIT_GROUP = "👁️ Only the characters who were there know this: the party was split."
SPLIT_UNKNOWN = (
    "🙈 Kept hidden from your players: the party was split, and DMbot can't tell which group "
    "heard it. To choose who knows: open either name, ✏️ Edit a connection…, 👁️ Who knows this?, "
    "pick who was there, then 👥 Only some characters."
)


def split_line(group: int, unknown: int) -> str:
    """The "All true" summary's line for what was said while the party was split (#2996)."""
    parts = []
    if group:
        parts.append(
            f"👁️ {group} of them {'was' if group == 1 else 'were'} said while the party was split: "
            "only the characters who were there know "
            f"{'it' if group == 1 else 'them'}."
        )
    if unknown:
        parts.append(
            f"🙈 {unknown} of them {'is' if unknown == 1 else 'are'} kept hidden from your "
            "players: the party was split, and DMbot can't tell which group heard "
            f"{'it' if unknown == 1 else 'them'}. To choose who knows: open either name, "
            "✏️ Edit a connection…, 👁️ Who knows this?, pick who was there, then 👥 Only some "
            "characters."
        )
    return "\n".join(parts)


def shown_line(n: int) -> str:
    """The "All true" summary's line for the held-back connections it showed (#3963 c)."""
    return (
        f"👁️ {n} of them {'was' if n == 1 else 'were'} said at the table, so your players can see "
        f"{'it' if n == 1 else 'them'} now."
    )


def held_line(n: int) -> str:
    """The "All true" summary's line for the connections kept hidden beside a hidden one (#3979)."""
    one = n == 1
    return (
        f"🙈 {n} of them {'is' if one else 'are'} kept hidden from your players, so "
        f"{'it' if one else 'they'} can't give away another hidden connection between the same "
        f"two names. To show {'it' if one else 'them'}: open either name, ✏️ Edit a connection…, "
        f"👁️ Who knows this?, then 👁️ Everyone at the table."
    )


ALL_TRUE, ALL_SECRET = (
    f"{YES} All true",
    f"{HIDE} All secret",
)  # one press for the page shown (#3624)
_FINAL = (GONE, STOPPED_NOW, ODD_NOW)  # answers that end a question without a yes or a no


_is_condition = is_condition  # (moved to the store module, #4229 slice 4b)


def waiting_text(count: int, *, private: bool = False) -> str:
    """For the DM after a session: how many, never what. `private` is the DM's own chat (#3210),
    where nobody else reads it, so it says what to do next and drops the line about who will see
    the button."""
    if private:
        what = "thing said at the table needs" if count == 1 else "things said at the table need"
        return (
            f"🗣️ **{count} {what} your yes or no.** Nothing is remembered until you say so. "
            "Press the button."
        )
    what = "thing" if count == 1 else "things"
    return (
        f"🗣️ **{count} {what} said at the table to check.** Nothing is remembered until you "
        "say so. Press the button; only you will see it."
    )


def statement(claim: Claim) -> str:
    """What was said, in the words it was said in: "**Bram** carries **Heartseeker**"."""
    extra = f" ({_md(claim.details)})" if claim.details else ""
    return (
        f"**{_md(claim.subject)}** {_md(claim.relationship)} **{_md(claim.object)}**{extra}"
    ).replace(" ****", "")


def question_text(claim: Claim) -> str:
    """The statement as a question, and who said it; a companion link also says what it means
    (#5164), since "travels with" alone leaves the DM guessing."""
    by = "you said" if claim.how == HOW_DM else "a player said"
    means = (
        f"\n   _{TRAVELS_WITH_MEANS[0].upper()}{TRAVELS_WITH_MEANS[1:]}._"
        if says_travels_with(claim.relationship)
        else ""
    )
    return f"{statement(claim)}? _({by})_{means}"


def knows(count: int) -> str:
    """ "knows" for one, "know" for more."""
    return "knows" if count == 1 else "know"


def names_joined(names: list[str]) -> str:
    """ "Cerric", "Cerric and Belleros", "Cerric, Belleros and Okthandom", escaped for Discord;
    "nobody" for none (a stale list). Names only."""
    shown = [_md(n) for n in names]
    if not shown:
        return "nobody"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def party_names(party: list[Entity], ids: list[str]) -> str:
    """The characters' names, in the party list's order (`names_joined`). Names only."""
    wanted = set(ids)
    return names_joined([e.name for e in party if e.id in wanted])


def knower_icon(count: int) -> str:
    """The owner's icon rule for what a line says about who knows: 👤 for one, 👥 for two or more.
    (The button stays 👥: it picks any number.)"""
    return "👤" if count == 1 else SOME


def guess_for(claim: Claim, party: list[Entity]) -> list[str]:
    """Who DMbot thinks knows it (#4534): the player characters whose players spoke the lines it
    was said in, when that is some of the party and not all or none (nothing then to suggest).
    Only the picker's pre-ticks and a line on the card: it writes nothing, and never takes a
    character off the DM's own list."""
    spoke = {e.id for e in party if e.played_by is not None and e.played_by in claim.speaker_ids}
    return [e.id for e in party if e.id in spoke] if 0 < len(spoke) < len(party) else []


def page_text(
    page: list[Claim],
    total: int,
    answered: Mapping[str, str] | None = None,
    hints: Mapping[str, str] | None = None,
) -> str:
    """A few questions, numbered, with a heading when a name has several. An answered one
    keeps its number and says what came of it, so the numbers never move under a thumb."""
    answered = answered or {}
    lines = [
        f"**What was said at the table.** {YES} true · {SOME} true, only some characters know "
        f"· {HIDE} true, but secret from your players · {NO} not true.\n"
        f"{ALL_TRUE} and {ALL_SECRET} answer this whole page at once."
    ]
    last = ""
    for number, claim in enumerate(page, 1):
        if name_key(claim.subject) != last:  # the same key the questions are sorted by
            last = name_key(claim.subject)
            lines.append(f"\n__{_md(claim.subject)}__")
        done = answered.get(claim.id)
        if done is not None:
            lines.append(f"{number}. {done}")
        else:  # (why it is "something that happened to them" goes on its own line, not the details)
            why = f"\n   _{_md(claim.note)}_" if claim.note else ""
            hint = f"\n   {hints[claim.id]}" if hints and claim.id in hints else ""
            lines.append(f"{number}. {question_text(claim)}{why}{hint}")
    if total > len(page):
        lines.append(f"\n_{total - len(page)} more. Answer these and the next ones appear._")
    return "\n".join(lines)


log = logging.getLogger(__name__)
_WATCHES: set[asyncio.Task[None]] = set()  # kept so a task isn't dropped while it runs


async def _watch_story(
    interaction: discord.Interaction, campaign: Any, fact_id: str | None
) -> None:
    """A condition the DM just confirmed may have left their adventure behind (#1729): the check
    for that one condition. It never makes the answer fail."""
    bot = _bot(interaction)

    async def run() -> None:
        try:
            await bot.watch_story(
                campaign.guild_id, campaign.id, campaign.dm_screen_channel_id, state_id=fact_id
            )
        except Exception:
            log.exception("Story watch after a confirmed fact failed")

    # In the background: the DM's answer must not wait on it (Discord gives three seconds).
    task = asyncio.create_task(run())
    _WATCHES.add(task)
    task.add_done_callback(_WATCHES.discard)


class ClaimsReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:claims:review:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart (the campaign is in the
    button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🗣️ Check what was said",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:claims:review:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClaimsReviewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_claims_review(interaction, self.campaign_id)


def claims_review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ClaimsReviewButton(campaign_id))
    return view


async def start_claims_review(
    interaction: discord.Interaction, campaign_id: str, *, walk: Walk | None = None
) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(
            interaction,
            "Remembering names isn't switched on for this DMbot yet. "
            "Ask whoever runs DMbot to turn it on.",
        )
        return
    campaign = await _campaign_for(interaction, campaign_id)  # only whoever runs the campaign
    if campaign is None:
        return
    view = ClaimsReview(campaign.id, campaign.guild_id, walk=walk)
    text = await view.current_text(interaction)
    if text is None:
        if walk is not None:  # (answered meanwhile: on to the next part)
            await next_part(interaction, walk)
            return
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class ClaimsReview(_Menu):
    """A page of questions, each with ✅ and ❌ under its number. Answering one leaves the rest
    where they are; the next page comes when the whole page is answered."""

    def __init__(self, campaign_id: str, guild_id: int, *, walk: Walk | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.walk = walk  # inside "Check everything" (#4192): a progress line and Stop for now
        self.first_total = 0  # how many waited when this review began, for "4 of 11"
        # From the campaign, not the press: a question in the DM's private chat has no server
        # around it (#3210).
        self.guild_id = guild_id
        self.page: list[Claim] = []
        self.party: list[Entity] = []  # the current party, for 👥, its guess and the line
        # characters who left the party (and why), offered after the party in 👥 (#4537 §10)
        self.left: list[tuple[Entity, str]] = []
        self.total = 0
        self.answered: dict[str, str] = {}
        self.undoable: dict[str, int] = {}  # claim → the batch its fact was written in
        # What the last ✅ All true / 🤫 All secret press remembered, for its one Undo: (claim,
        # batch). Kept when the next page comes, so the Undo is still there to press (#3624).
        self.last_bulk: list[tuple[str, int]] = []
        self.busy = False  # a bulk press is writing: a second one waits (#3624)
        # A first meeting's "Starting feeling?" line, worked out with each page (#4229 slice 4b)
        self.feelings: dict[str, str] = {}

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """A fresh page (the oldest waiting questions), or None when none are left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        agreed = await _bot(interaction).consent.consenting(self.guild_id)
        waiting = await ClaimStore(memory).waiting(self.guild_id, self.campaign_id, opted_in=agreed)
        self.page, self.total, self.answered = waiting[:PAGE], len(waiting), {}
        self.feelings = {}
        try:  # (only a reminder: a failed read leaves the questions as they were)
            if self.page:
                self.feelings = await ClaimStore(memory).starting_feelings(
                    self.guild_id, self.campaign_id, self.page
                )
        except Exception:
            log.exception("starting feelings: read failed")
        # (the current party, in the party list's order: #4537 rule 13.1)
        group = await memory.party_and_others(self.guild_id, self.campaign_id)
        members = [i for i, _ in group.party]
        found = {
            e.id: e
            for e in await memory.entities(self.guild_id, self.campaign_id, statuses=[CONFIRMED])
        }
        self.party = [found[i] for i in members if i in found]
        self.left = [(found[i], why) for i, (_, why) in group.left.items() if i in found]
        self.first_total = self.first_total or self.total
        self.undoable = {}
        if not self.page:
            self.clear_items()
            return None
        return self._render()

    def _bulk_buttons(self) -> None:
        """Row 0: the whole page at once, then the Undo of the last such press. They answer only
        the questions still waiting on the page shown, never ones the DM has not seen."""
        waiting = [c for c in self.page if c.id not in self.answered]
        if waiting:
            self.add_item(
                _Button(
                    self._answer_all(secret=False),
                    label=ALL_TRUE,
                    style=discord.ButtonStyle.success,
                    row=0,
                )
            )
            if any(not _is_condition(c) for c in waiting):  # a state has no secret flag
                self.add_item(
                    _Button(
                        self._answer_all(secret=True),
                        label=ALL_SECRET,
                        style=discord.ButtonStyle.primary,  # blurple: Discord has no purple
                        row=0,
                    )
                )
        if self.last_bulk:
            self.add_item(
                _Button(
                    self._take_back_all,
                    label=f"↩️ Undo all {len(self.last_bulk)}",
                    style=discord.ButtonStyle.secondary,
                    row=0,
                )
            )

    def _hints(self) -> dict[str, str]:
        """For each question still waiting: who DMbot thinks knows it, when it has a guess."""
        out: dict[str, str] = {}
        for claim in self.page:
            if claim.id in self.answered or _is_condition(claim):
                continue
            said: list[str] = []
            guessed = guess_for(claim, self.party)
            if guessed:
                who = party_names(self.party, guessed)
                icon = knower_icon(len(guessed))
                said.append(f"{icon} DMbot thinks only {who} {knows(len(guessed))} this.")
            if claim.id in self.feelings:
                said.append(self.feelings[claim.id])
            if said:
                out[claim.id] = "\n   ".join(said)
        return out

    def _render(self) -> str:
        """Row 1: ✅ (an answered one's ↩️ takes its place), row 2: 👥, row 3: 🤫, row 4: ❌, so
        every question has its four answers and the taking back needs no row of its own."""
        self.clear_items()
        self._bulk_buttons()
        for number, claim in enumerate(self.page, 1):
            gone = claim.id in self.answered
            if claim.id in self.undoable:  # a fact the players may now be shown can be taken back
                self.add_item(
                    _Button(
                        self._undo(claim.id),
                        label=f"↩️ {number}",
                        style=discord.ButtonStyle.secondary,
                        row=1,
                    )
                )
            else:
                self.add_item(
                    _Button(
                        self._answer(claim.id, yes=True),
                        label=f"{YES} {number}",
                        style=discord.ButtonStyle.success,
                        row=1,
                        disabled=gone,
                    )
                )
            if not _is_condition(claim):  # a state ("is dead") has no audience and no secret flag
                if len(self.party) >= 2:  # (with one character there is no "some")
                    self.add_item(
                        _Button(
                            self._some(claim.id),
                            label=f"{SOME} {number}",
                            style=discord.ButtonStyle.primary,
                            row=2,
                            disabled=gone,
                        )
                    )
                self.add_item(
                    _Button(
                        self._answer(claim.id, yes=True, secret=True),
                        label=f"{HIDE} {number}",
                        style=discord.ButtonStyle.primary,
                        row=3,
                        disabled=gone,
                    )
                )
            self.add_item(
                _Button(
                    self._answer(claim.id, yes=False),
                    label=f"{NO} {number}",
                    style=discord.ButtonStyle.secondary,
                    row=4,
                    disabled=gone,
                )
            )
        add_stop(self, self.walk, row=0)  # (row 0 has at most three buttons; row 4 can be full)
        done = min(self.first_total - self.total + len(self.answered) + 1, self.first_total)
        return progress(self.walk, done, self.first_total) + page_text(
            self.page, self.total, self.answered, self._hints()
        )

    def _some(self, claim_id: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._pick(interaction, claim_id)

        return handler

    async def _pick(self, interaction: discord.Interaction, claim_id: str) -> None:
        """👥: the party's characters to tick, DMbot's guess ticked already. The DM only, asked
        again now; nothing is written until Save."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        claim = next((c for c in self.page if c.id == claim_id), None)
        if campaign is None:
            return
        if (
            claim is None or claim_id in self.answered
        ):  # (a stale or double press still gets an answer)
            await _tell(interaction, "That question was already answered.")
            return
        picker = KnowersPicker(self, claim)
        await _replace(interaction, picker.text(), picker)

    def _undo(self, claim_id: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._take_back(interaction, claim_id)

        return handler

    async def _take_back(self, interaction: discord.Interaction, claim_id: str) -> None:
        """↩️ on a remembered fact: the change log's Undo for exactly that answer. The question
        is asked again with the next ones."""
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        batch = self.undoable.get(claim_id)
        claim = next((c for c in self.page if c.id == claim_id), None)
        if campaign is None or memory is None or batch is None or claim is None:
            return
        try:
            await memory.undo(
                campaign.guild_id,
                campaign.id,
                batch,
                source=DM,
                after=lambda w: ClaimStore.reopen_in(w, claim_id),
            )
        except (MemoryRuleError, TooLateToUndo) as exc:
            await _tell(interaction, str(exc) or GONE)
            return
        del self.undoable[claim_id]
        self.answered[claim_id] = f"↩️ Taken back: {statement(claim)}. DMbot will ask again."
        lookup = getattr(_bot(interaction), "lookup", None)
        if lookup is not None:
            lookup.drop([campaign.id])
        await _replace(interaction, self._render(), self)

    def _answer_all(self, *, secret: bool) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await _decide_all_on(self, interaction, secret)

        return handler

    async def _take_back_all(self, interaction: discord.Interaction) -> None:
        await _take_back_all_on(self, interaction)

    def _answer(self, claim_id: str, *, yes: bool, secret: bool = False) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._decide(interaction, claim_id, yes, secret=secret)

        return handler

    async def _decide(
        self,
        interaction: discord.Interaction,
        claim_id: str,
        yes: bool,
        *,
        secret: bool = False,
        knowers: list[str] | None = None,
    ) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        claim = next((c for c in self.page if c.id == claim_id), None)
        if campaign is None or memory is None or claim is None:
            return
        claims = ClaimStore(memory)
        saying = statement(claim)
        note = ""
        self.last_bulk = []  # (a new answer: the last bulk press can no longer be taken back whole)
        try:
            if yes:
                # Who may be recorded is asked again now: someone may have stopped meanwhile.
                agreed = await _bot(interaction).consent.consenting(self.guild_id)
                result = await claims.confirm(
                    self.guild_id,
                    self.campaign_id,
                    claim_id,
                    opted_in=agreed,
                    secret=secret,
                    knowers=knowers,
                )
                if result.batch is not None:
                    self.undoable[claim_id] = result.batch
                if knowers is not None:
                    # (who knows it, by §7's rule against the current party, #4537 slice 3a)
                    who = party_icons.line(
                        knowers, [(e.id, e.name) for e in self.party], escape=_md
                    )
                    done = f"Remembered: {saying}.\n{who}"
                elif secret:
                    done = f"{HIDE} Remembered, and kept secret from your players: {saying}."
                else:
                    done = f"{YES} Remembered: {saying}."
                if result.shown:
                    done += f"\n{SHOWN_NOW}"
                if result.held:
                    done += f"\n{HELD_NOW}"
                if result.split and knowers is None:  # (a pick is the DM's own word: no guess line)
                    done += f"\n{SPLIT_GROUP if result.scene else SPLIT_UNKNOWN}"
                await _watch_story(interaction, campaign, result.claim.fact_id)
                if result.flags:
                    why = FLAG_WHY.get(result.flags[0].kind) or ODD_PAIR
                    note = f"\n⚠️ {why}. It's saved, and DMbot will ask you about it."
            else:
                await claims.reject(self.guild_id, self.campaign_id, claim_id)
                done = f"{NO} Not remembered: {saying}. DMbot won't ask again."
        except MemoryRuleError as exc:
            why = companion_refusal(str(exc))  # (and what to press, #5164)
            message = (
                f"{why} Press {NO} with its number to leave it out." if why else str(exc) or GONE
            )
            if message not in _FINAL:
                await _tell(interaction, message)  # the question stays; nothing changed
                return
            done = f"{message}"
        self.answered[claim_id] = done
        text = self._render()
        if len(self.answered) >= len(self.page):  # the page is done: the next one
            fresh = await self.current_text(interaction)
            if fresh is None:
                self.stop()
                final = FINISHED if self.walk is not None else NOTHING_WAITING
                await _replace(interaction, f"{done}{note}\n\n{final}", None)
                if self.walk is not None:
                    await next_part(interaction, self.walk)
                return
            await _replace(interaction, f"{done}{note}\n\n{fresh}", self)
            return
        await _replace(interaction, f"{done}{note}\n\n{text}", self)


class KnowersPicker(_Menu):
    """👥 Some know: who knows what was said. One card with the party's characters to tick (DMbot's
    guess ticked already), Save and Back. Nothing is written until Save, which answers the question
    as true with exactly these characters knowing it: one change, one ↩️ on the page. The store
    refuses what can't be honoured (a shown connection, a hidden name) in plain words and the
    question stays."""

    MAX_OPTIONS = 25  # what a menu holds; a party is a handful

    def __init__(self, review: ClaimsReview, claim: Claim) -> None:
        super().__init__()
        self.review, self.claim = review, claim
        self.picks = [*review.party, *(e for e, _ in review.left)][: self.MAX_OPTIONS]
        shown = {e.id for e in self.picks}  # (a menu holds 25: the party first, #4537 §10)
        guessed = [i for i in guess_for(claim, review.party) if i in shown]
        self.chosen: list[str] = list(guessed)
        self.guess = guessed  # DMbot's own guess, kept apart from what the DM ticks
        self.saving = False
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Who knows it?",
            min_values=1,
            max_values=len(self.picks),
            options=self._options(),
            row=0,
        )
        self.select.callback = self._ticked  # type: ignore[method-assign]
        self.add_item(self.select)
        self.add_item(_Button(self._save, label="Save", style=discord.ButtonStyle.success, row=1))
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary, row=1))

    def _options(self) -> list[discord.SelectOption]:
        why = {e.id: w for e, w in self.review.left}
        return [
            discord.SelectOption(
                label=e.name[:100], value=e.id, default=e.id in self.chosen,
                description=party_icons.left_note(why[e.id]) if e.id in why else None,
                emoji="🚪" if e.id in why else None,
            )
            for e in self.picks
        ]  # fmt: skip

    def text(self) -> str:
        # (DMbot's name only goes on DMbot's guess, never on what the DM ticked)
        who = (
            f"{knower_icon(len(self.guess))} DMbot's guess is ticked: only "
            f"{party_names(self.review.party, self.guess)} {knows(len(self.guess))} this.\n"
            if self.guess and sorted(self.chosen) == sorted(self.guess)
            else ""
        )
        return (
            f"{SOME} **Who knows this?** {statement(self.claim)}\n{who}"
            "Tick who knows it, then press Save. Saving also marks it true, and only they will "
            "know. "
            "Back changes nothing."
        )

    async def _ticked(self, interaction: discord.Interaction) -> None:
        self.chosen = list(self.select.values)
        self.select.options = self._options()
        await _replace(interaction, self.text(), self)

    async def _save(self, interaction: discord.Interaction) -> None:
        if not self.chosen:  # (the menu needs one, but a card with nothing ticked says so)
            await _tell(interaction, "Tick at least one character who knows it, or press Back.")
            return
        if self.saving or self.claim.id in self.review.answered:  # (a double press writes once)
            await _tell(interaction, "That question was already answered.")
            return
        self.saving = True
        try:
            await _answer_first(interaction, in_place=True)  # (the write below can take a moment)
            await self.review._decide(interaction, self.claim.id, True, knowers=self.chosen)
        finally:
            self.saving = False
        if self.claim.id in self.review.answered:
            self.stop()  # (a refusal leaves this card in place to change the ticks)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _replace(interaction, self.review._render(), self.review)


def _reopen(claim_id: str) -> Any:
    """The question asked again in the same write as its fact's Undo."""

    async def after(w: Any) -> None:
        await ClaimStore.reopen_in(w, claim_id)

    return after


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


async def _decide_all_on(
    view: ClaimsReview, interaction: discord.Interaction, secret: bool
) -> None:
    """✅ All true / 🤫 All secret: say yes to every question still waiting on the page shown,
    each exactly as its own button would (one fact each, the speakers' consent asked again now,
    the store's rules deciding). A refused one is counted and the rest still go in. One Undo
    takes back everything this press remembered."""
    await _answer_first(interaction, in_place=True)  # several writes follow (3-second limit)
    campaign = await _campaign_for(interaction, view.campaign_id)  # asked again each press
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    waiting = [c for c in view.page if c.id not in view.answered]
    targets = [c for c in waiting if not (secret and _is_condition(c))]
    skipped = len(waiting) - len(targets)  # states ("is dead") cannot be secret
    if not targets:
        await _tell(interaction, "Nothing on this page can be answered that way.")
        return
    if view.busy:  # a second press while the first is still writing would answer the same ones
        await _tell(interaction, "Still saving the last press. Give it a moment.")
        return
    view.busy = True
    try:
        await _answer_page(view, interaction, campaign, memory, targets, secret, skipped)
    finally:
        view.busy = False


async def _answer_page(
    view: ClaimsReview,
    interaction: discord.Interaction,
    campaign: Any,
    memory: Any,
    targets: list[Claim],
    secret: bool,
    skipped: int,
) -> None:
    """The writing and the page that follows, for `_decide_all_on` (which holds the view's lock)."""
    claims = ClaimStore(memory)
    kept = flagged = failed = shown = held = split_group = split_unknown = 0
    made: list[tuple[str, int]] = []  # (claim, batch) of what this press remembered
    refused: dict[str, int] = {}
    for claim in targets:
        saying = statement(claim)
        try:
            # (who may be recorded is asked again for each, as one by one: a speaker may stop
            # while the others are being written)
            agreed = await _bot(interaction).consent.consenting(view.guild_id)
            result = await claims.confirm(
                view.guild_id, view.campaign_id, claim.id, opted_in=agreed, secret=secret
            )
        except MemoryRuleError as exc:
            message = shorten(str(exc) or GONE, REFUSAL_MAX)
            refused[message] = refused.get(message, 0) + 1
            if message in _FINAL:  # the question ended (answered elsewhere, or its speaker stopped)
                view.answered[claim.id] = message
            continue
        except Exception as exc:  # a database error: counted, and the rest of the page goes on
            log.warning("An All true/secret answer failed: %s", type(exc).__name__)
            failed += 1
            continue
        if result.batch is not None:
            made.append((claim.id, result.batch))
        view.answered[claim.id] = (
            f"{HIDE} Remembered, and kept secret from your players: {saying}."
            if secret
            else f"{YES} Remembered: {saying}."
        )
        kept += 1
        shown += result.shown
        held += result.held
        split_group += result.split and result.scene > 0
        split_unknown += result.split and result.scene == 0
        await _watch_story(interaction, campaign, result.claim.fact_id)
        flagged += bool(result.flags)
    if made:  # a press that kept nothing leaves the earlier Undo alone
        view.last_bulk = made
    saved = (
        f"{HIDE} Saved {kept} as true, and secret from your players."
        if secret
        else f"{YES} Saved {kept} as true."
    )
    lines = [saved]
    if shown:
        lines.append(shown_line(shown))
    if held:
        lines.append(held_line(held))
    if split_group or split_unknown:
        lines.append(split_line(split_group, split_unknown))
    for message, count in refused.items():
        lines.append(f"{count} not saved: {message}")
    if failed:
        lines.append(f"{failed} could not be saved. Try those again.")
    if skipped:
        lines.append(
            f"{skipped} left for you: a state like “is dead” can't be secret. Use ✅ or ❌."
        )
    if flagged:
        lines.append(f"⚠️ {flagged} saved, and DMbot noticed something odd. It will ask you.")
    summary = "\n".join(lines)
    if len(view.answered) >= len(view.page):  # the page is done: the next one, Undo still there
        fresh = await view.current_text(interaction)
        if fresh is None:
            if view.last_bulk:  # nothing left to check, but the press can still be taken back
                view.clear_items()
                view._bulk_buttons()
                final = FINISHED if view.walk is not None else NOTHING_WAITING
                await _replace(interaction, f"{summary}\n\n{final}", view)
                if view.walk is not None:  # (this card keeps its Undo; the walk goes on below it)
                    await next_part(interaction, view.walk)
                return
            view.stop()
            final = FINISHED if view.walk is not None else NOTHING_WAITING
            await _replace(interaction, f"{summary}\n\n{final}", None)
            if view.walk is not None:
                await next_part(interaction, view.walk)
            return
        await _replace(interaction, f"{summary}\n\n{fresh}", view)
        return
    await _replace(interaction, f"{summary}\n\n{view._render()}", view)


async def _take_back_all_on(view: ClaimsReview, interaction: discord.Interaction) -> None:
    """↩️ Undo all: the change log's Undo for each fact the last bulk press remembered, newest
    first; the questions are asked again with the waiting ones. One that something changed since
    stays remembered and is counted."""
    await _answer_first(interaction, in_place=True)  # several undos follow (3-second limit)
    campaign = await _campaign_for(interaction, view.campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None or not view.last_bulk:
        return
    undone, left = 0, []
    for claim_id, batch in reversed(view.last_bulk):
        try:
            await memory.undo(
                campaign.guild_id,
                campaign.id,
                batch,
                source=DM,
                after=_reopen(claim_id),
            )
            undone += 1
        except (MemoryRuleError, TooLateToUndo):
            left.append((claim_id, batch))
    view.last_bulk = list(reversed(left))
    lookup = getattr(_bot(interaction), "lookup", None)
    if lookup is not None:
        lookup.drop([campaign.id])
    note = f"↩️ Undid {_plural(undone, 'answer', 'answers')}. DMbot will ask about them again."
    if left:
        note += f" {len(left)} could not be undone because something changed since."
    fresh = await view.current_text(interaction)
    if fresh is None:
        view.clear_items()
        view._bulk_buttons()
        await _replace(interaction, f"{note}\n\n{NOTHING_WAITING}", view if left else None)
        return
    await _replace(interaction, f"{note}\n\n{fresh}", view)


__all__ = [
    "ClaimsReviewButton",
    "claims_review_view",
    "page_text",
    "question_text",
    "start_claims_review",
    "statement",
    "waiting_text",
]
