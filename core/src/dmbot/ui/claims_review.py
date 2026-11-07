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
from dmbot.memory.claims import (
    GONE,
    ODD_NOW,
    PAGE,
    STOPPED_NOW,
    Claim,
    ClaimStore,
    Extracted,
    condition_of,
)
from dmbot.memory.models import DM, HOW_DM, MemoryRuleError, TooLateToUndo, name_key
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.name_card import FLAG_WHY
from dmbot.ui.names import _campaign_for, _md, _memory

NOTHING_WAITING = "✅ Nothing left to check. DMbot will ask again after the next session."
ODD_PAIR = "DMbot noticed something odd about it"
REFUSAL_MAX = (
    160  # a store's reason in the summary: the page and the summary share 2,000 characters
)
YES, NO, HIDE = "✅", "❌", "🤫"
ALL_TRUE, ALL_SECRET = (
    f"{YES} All true",
    f"{HIDE} All secret",
)  # one press for the page shown (#3624)
_FINAL = (GONE, STOPPED_NOW, ODD_NOW)  # answers that end a question without a yes or a no


def _is_condition(claim: Claim) -> bool:
    return (
        condition_of(
            Extracted(
                claim.subject, claim.relationship, claim.object, claim.details, claim.lines,
                claim.how,
            )
        )
        is not None
    )  # fmt: skip


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
    """The statement as a question, and who said it."""
    by = "you said" if claim.how == HOW_DM else "a player said"
    return f"{statement(claim)}? _({by})_"


def page_text(page: list[Claim], total: int, answered: Mapping[str, str] | None = None) -> str:
    """A few questions, numbered, with a heading when a name has several. An answered one
    keeps its number and says what came of it, so the numbers never move under a thumb."""
    answered = answered or {}
    lines = [
        f"**What was said at the table.** {YES} true · {HIDE} true, but secret from your players "
        f"· {NO} not true.\n{ALL_TRUE} and {ALL_SECRET} answer this whole page at once."
    ]
    last = ""
    for number, claim in enumerate(page, 1):
        if name_key(claim.subject) != last:  # the same key the questions are sorted by
            last = name_key(claim.subject)
            lines.append(f"\n__{_md(claim.subject)}__")
        done = answered.get(claim.id)
        lines.append(f"{number}. {question_text(claim)}" if done is None else f"{number}. {done}")
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


async def start_claims_review(interaction: discord.Interaction, campaign_id: str) -> None:
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
    view = ClaimsReview(campaign.id, campaign.guild_id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class ClaimsReview(_Menu):
    """A page of questions, each with ✅ and ❌ under its number. Answering one leaves the rest
    where they are; the next page comes when the whole page is answered."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        # From the campaign, not the press: a question in the DM's private chat has no server
        # around it (#3210).
        self.guild_id = guild_id
        self.page: list[Claim] = []
        self.total = 0
        self.answered: dict[str, str] = {}
        self.undoable: dict[str, int] = {}  # claim → the batch its fact was written in
        # What the last ✅ All true / 🤫 All secret press remembered, for its one Undo: (claim,
        # batch). Kept when the next page comes, so the Undo is still there to press (#3624).
        self.last_bulk: list[tuple[str, int]] = []
        self.busy = False  # a bulk press is writing: a second one waits (#3624)

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """A fresh page (the oldest waiting questions), or None when none are left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        agreed = await _bot(interaction).consent.consenting(self.guild_id)
        waiting = await ClaimStore(memory).waiting(self.guild_id, self.campaign_id, opted_in=agreed)
        self.page, self.total, self.answered = waiting[:PAGE], len(waiting), {}
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

    def _render(self) -> str:
        self.clear_items()
        self._bulk_buttons()
        for number, claim in enumerate(self.page, 1):
            gone = claim.id in self.answered
            self.add_item(
                _Button(
                    self._answer(claim.id, yes=True),
                    label=f"{YES} {number}",
                    style=discord.ButtonStyle.success,
                    row=1,
                    disabled=gone,
                )
            )
            if not _is_condition(claim):  # a state ("is dead") has no secret flag
                self.add_item(
                    _Button(
                        self._answer(claim.id, yes=True, secret=True),
                        label=f"{HIDE} {number}",
                        style=discord.ButtonStyle.primary,
                        row=2,
                        disabled=gone,
                    )
                )
            self.add_item(
                _Button(
                    self._answer(claim.id, yes=False),
                    label=f"{NO} {number}",
                    style=discord.ButtonStyle.secondary,
                    row=3,
                    disabled=gone,
                )
            )
        for number, claim in enumerate(self.page, 1):
            if claim.id in self.undoable:  # a fact the players may now be shown can be taken back
                self.add_item(
                    _Button(
                        self._undo(claim.id),
                        label=f"↩️ {number}",
                        style=discord.ButtonStyle.secondary,
                        row=4,
                    )
                )
        return page_text(self.page, self.total, self.answered)

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
        self, interaction: discord.Interaction, claim_id: str, yes: bool, *, secret: bool = False
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
                    self.guild_id, self.campaign_id, claim_id, opted_in=agreed, secret=secret
                )
                if result.batch is not None:
                    self.undoable[claim_id] = result.batch
                done = (
                    f"{HIDE} Remembered, and kept secret from your players: {saying}."
                    if secret
                    else f"{YES} Remembered: {saying}."
                )
                await _watch_story(interaction, campaign, result.claim.fact_id)
                if result.flags:
                    why = FLAG_WHY.get(result.flags[0].kind) or ODD_PAIR
                    note = f"\n⚠️ {why}. It's saved, and DMbot will ask you about it."
            else:
                await claims.reject(self.guild_id, self.campaign_id, claim_id)
                done = f"{NO} Not remembered: {saying}. DMbot won't ask again."
        except MemoryRuleError as exc:
            message = str(exc) or GONE
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
                await _replace(interaction, f"{done}{note}\n\n{NOTHING_WAITING}", None)
                return
            await _replace(interaction, f"{done}{note}\n\n{fresh}", self)
            return
        await _replace(interaction, f"{done}{note}\n\n{text}", self)


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
    kept = flagged = failed = 0
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
                await _replace(interaction, f"{summary}\n\n{NOTHING_WAITING}", view)
                return
            view.stop()
            await _replace(interaction, f"{summary}\n\n{NOTHING_WAITING}", None)
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
