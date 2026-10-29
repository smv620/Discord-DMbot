"""The DM's after-session heads-ups (#1320, docs/STORY_MEMORY.md section 4): "⚠️ **Probable:
Ulfgar** died in Session 4 … In this session: Ulfgar is sworn to Cerric." What a session's
unanswered claims clash with in the story the DM already confirmed, each with the earlier thing
quoted and three answers. DMbot only points; the DM decides what is true in their story.

Private to the campaign's DM: the DM screen only says how many (never what), and the list is an
answer only the DM sees, because a heads-up spoils the story if a player peeks. Never in a
transcript. Every press asks again whether the person still runs the campaign.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.memory.checks import ACTS_AFTER_END
from dmbot.memory.claims import GONE, ODD_NOW, STOPPED_NOW
from dmbot.memory.continuity import (
    BACK_FROM_THE_END,
    CHANGED,
    IGNORE,
    KNOWS_A_SECRET,
    NOT_TRUE,
    Conflict,
    ContinuityStore,
    Levels,
)
from dmbot.memory.models import DESTROYED, MemoryRuleError
from dmbot.ui.claims_review import statement
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _replace,
    _send,
    _tell,
)
from dmbot.ui.names import _campaign_for, _md, _memory

log = logging.getLogger(__name__)

PAGE = 5  # heads-ups per page: a row of three buttons for each, and Discord allows five rows
NOTHING_LEFT = "✅ Nothing left to check. DMbot looks again after your next session."
_FINAL = (GONE, STOPPED_NOW, ODD_NOW)  # answers that end a heads-up without a choice


def waiting_text(count: int, pinned: int = 0) -> str:
    """For the DM screen after a session: how many, never what. `pinned`: how many of them go
    against something the DM pinned as 100% truth (#2447), said as a number only."""
    what = "heads-up" if count == 1 else "heads-ups"
    off = f" 📌 {pinned} {'goes' if pinned == 1 else 'go'} against a pin." if pinned else ""
    return (
        f"⚠️ **{count} {what} from this session.**{off} Something said at the table may clash "
        "with your story. Nothing changes until you decide. Only you can see the list."
    )


def _when(conflict: Conflict, numbers: Mapping[int, int]) -> str:
    started = conflict.earlier.session_started_at if conflict.earlier else None
    number = numbers.get(started) if started is not None else None
    return f" in Session {number}" if number is not None else ""


def line_text(conflict: Conflict, numbers: Mapping[int, int]) -> str:
    """One heads-up in plain words, with the earlier thing and where it came from."""
    level = conflict.level.capitalize()
    name = f"**{_md(conflict.entity_name)}**"
    when = _when(conflict, numbers)
    quote = (
        f" (“{_md(conflict.earlier.quote)}”)"
        if conflict.earlier is not None and conflict.earlier.quote
        else ""
    )
    now = f"At the table: {statement(conflict.claim)}."
    if conflict.kind == KNOWS_A_SECRET:
        # The clue is the entry; the list is the people the DM said know it. Nobody is told
        # they are wrong: a missing name may only mean DMbot was never told.
        who = _md(conflict.learner_name or "Someone")
        listed = _md(conflict.earlier_words) or "the list is empty"
        return (
            f"⚠️ **Check: {who}** spoke of **{_md(conflict.entity_name)}** and isn't on its list "
            f"({listed}). {now} ({conflict.level})"
        )
    if conflict.kind in (ACTS_AFTER_END, BACK_FROM_THE_END):
        ended = "was destroyed" if conflict.ended_condition == DESTROYED else "died"
        return f"⚠️ **{level}:** {name} {ended}{when}{quote}. {now}"
    before = _md(conflict.earlier_words) or "earlier in your story"
    if conflict.pinned:  # the DM's own 100% truth: said plainly, as a question and not a ruling
        return f"📌 **Check: {name}.** You pinned: {before}{quote}. {now} ({conflict.level})"
    return f"⚠️ **{level}: {name}.** Before{when}: {before}{quote}. {now}"


def changed_label(conflict: Conflict) -> str:
    """Short, because a number goes in front and Discord cuts a button at 25 characters on a
    phone: what the DM is saying is true now."""
    if conflict.kind == KNOWS_A_SECRET:
        return "They learned it"
    if conflict.ended_condition == DESTROYED:
        return "Still here"
    if conflict.ended_condition is not None:
        return "Alive after all"
    return "Story changed"


def page_text(
    page: list[Conflict], total: int, numbers: Mapping[int, int], answered: Mapping[str, str]
) -> str:
    lines = [
        "**Heads-ups from this session.** Pick what is true in your story.\n"
        "**Story changed:** the new thing is true now. **They learned it:** add them to the list. "
        "**Wasn't real:** a joke or a mistake; DMbot forgets it. "
        "**Ignore:** leave it for now."
    ]
    if any(c.pinned for c in page):
        lines[0] += " **Story changed** on a 📌 line takes the pin off."
    for number, conflict in enumerate(page, 1):
        done = answered.get(conflict.claim.id)
        lines.append(f"{number}. {line_text(conflict, numbers) if done is None else done}")
    if total > len(page):
        lines.append(f"\n_{total - len(page)} more waiting. They show when these are done._")
    return "\n".join(lines)


class ContinuityButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:continuity:review:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart (the campaign is in the
    button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="⚠️ Check the heads-ups",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:continuity:review:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ContinuityButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_continuity_review(interaction, self.campaign_id)


def continuity_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ContinuityButton(campaign_id))
    return view


def _levels(interaction: discord.Interaction) -> Levels:
    return _bot(interaction).settings.continuity


async def _session_numbers(interaction: discord.Interaction, campaign_id: str) -> dict[int, int]:
    """Session 1, 2, … by when each started, for "in Session 4". Empty when transcripts are off."""
    guild = interaction.guild
    transcripts = _bot(interaction).transcripts
    if guild is None or transcripts is None:
        return {}
    sessions = await transcripts.sessions(guild.id, campaign_id, limit=1000)
    return {s.started_at: s.number for s in sessions}


async def start_continuity_review(interaction: discord.Interaction, campaign_id: str) -> None:
    if _memory(interaction) is None:
        await _tell(
            interaction,
            "Story memory isn't switched on for this DMbot yet. "
            "Ask whoever runs DMbot to turn it on.",
        )
        return
    campaign = await _campaign_for(interaction, campaign_id)  # only whoever runs the campaign
    if campaign is None:
        return
    view = ContinuityReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_LEFT)
        return
    await _send(interaction, text, view)


class ContinuityReview(_Menu):
    """A page of heads-ups, each with its own row of three answers. Answering one leaves the
    rest where they are; the next page comes when the whole page is done."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.page: list[Conflict] = []
        self.total = 0
        self.answered: dict[str, str] = {}
        self.numbers: dict[int, int] = {}
        self.ignored: set[str] = set()  # quiet for the rest of this list

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        """A fresh page, or None when no heads-up is left."""
        memory = _memory(interaction)
        guild = interaction.guild
        if memory is None or guild is None:
            return None
        agreed = await _bot(interaction).consent.consenting(guild.id)
        report = await ContinuityStore(memory).check(
            guild.id, self.campaign_id, opted_in=agreed, levels=_levels(interaction)
        )
        open_ = [c for c in report.conflicts if c.claim.id not in self.ignored]
        self.numbers = await _session_numbers(interaction, self.campaign_id)
        self.page, self.total, self.answered = open_[:PAGE], len(open_), {}
        if not self.page:
            self.clear_items()
            return None
        return self._render()

    def _render(self) -> str:
        self.clear_items()
        for row, (number, conflict) in enumerate(enumerate(self.page, 1)):
            gone = conflict.claim.id in self.answered
            for action, label, style in (
                (CHANGED, changed_label(conflict), discord.ButtonStyle.primary),
                (NOT_TRUE, "Wasn't real", discord.ButtonStyle.secondary),
                (IGNORE, "Ignore", discord.ButtonStyle.secondary),
            ):
                self.add_item(
                    _Button(
                        self._answer(conflict.claim.id, action),
                        label=f"{number} · {label}",
                        style=style,
                        row=row,
                        disabled=gone,
                    )
                )
        return page_text(self.page, self.total, self.numbers, self.answered)

    def _answer(self, claim_id: str, action: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._decide(interaction, claim_id, action)

        return handler

    async def _decide(self, interaction: discord.Interaction, claim_id: str, action: str) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        guild = interaction.guild
        conflict = next((c for c in self.page if c.claim.id == claim_id), None)
        if campaign is None or memory is None or guild is None or conflict is None:
            return
        await _answer_first(interaction, in_place=True)  # the steps below can take a moment
        try:
            # Who may be recorded is asked again now: someone may have stopped meanwhile.
            agreed = await _bot(interaction).consent.consenting(guild.id)
            said = await ContinuityStore(memory).answer(
                guild.id,
                self.campaign_id,
                claim_id,
                action,
                opted_in=agreed,
                levels=_levels(interaction),
            )
        except MemoryRuleError as exc:
            message = str(exc) or GONE
            if message not in _FINAL:
                await _tell(interaction, message)  # the heads-up stays; nothing changed
                return
            said = message
            action = ""
        if action == IGNORE:
            self.ignored.add(claim_id)
        elif action == CHANGED:  # the answer may set a condition: Watch the story looks (#1729)
            from dmbot.ui.claims_review import _watch_story

            await _watch_story(interaction, campaign, None)
        log.info("Continuity heads-up answered: %s", action or "gone")  # a count, never words
        # The whole list is read again after every answer, with consent asked again: a line that
        # someone who has stopped being recorded said must leave the screen now, not when the
        # page is done.
        fresh = await self.current_text(interaction)
        if fresh is None:
            self.stop()
            await _replace(interaction, f"{said}\n\n{NOTHING_LEFT}", None)
            return
        await _replace(interaction, f"{said}\n\n{fresh}", self)


__all__ = [
    "ContinuityButton",
    "continuity_view",
    "line_text",
    "page_text",
    "start_continuity_review",
    "waiting_text",
]
