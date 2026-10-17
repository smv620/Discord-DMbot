"""Guide me on the DM's screens (#1731; docs/STORY_MEMORY.md, section 6, "5d slice 6"): the
**🧭 What's next?** button on the Shared story card and in the pre-session note. It answers
privately, only to the DM who pressed: where the table seems to be in the DM's own adventure
(from the names heard lately that the DM linked to the book, #1715), what the chapter still holds
that the table hasn't met, the next chapter, and the clashes Watch the story (#1729) already told
for this chapter. The DM can say they are in another chapter, and that beats DMbot's guess until
they go back to the guess; either can be undone.

Nothing is invented or advised: every name and summary is what the reader stored. Only while Shared
story is on **Guide me**, a DM said here they have the right to the material and the owner's plan
allows it; asked again on every press. Never players, never a transcript, never `dmb-chat`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns.models import STORY_GUIDE
from dmbot.memory import guide as model
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.memory.story_watch import Clash
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets
from dmbot.ui.story_watch import WordsButton
from dmbot.ui.story_watch import line_text as clash_line

log = logging.getLogger(__name__)

LABEL = "🧭 What's next?"
ONLY_DM = "Only this campaign's DM can use this button."
NOT_GUIDE = "Not now. In Settings, set Shared story to **Guide me** and say you have the right."
NO_PLAN = "DMbot can't check this right now. Try again in a moment."
NOT_READ = "Nothing read yet. Press **Read it now** on the Shared story card first."
NO_CHAPTERS = "DMbot didn't find chapters in this file."
LAST = "This is the last chapter in the file."
UNDONE = "↩ Undone."
TEXT_MAX = 1990
NAME_SHOWN = 40  # characters of a name on a line, so a full message stays under 2000
LABEL_SHOWN = 25
MENU_MAX = 25  # options in a Discord menu


def _short(name: str) -> str:
    return _md(name if len(name) <= NAME_SHOWN else name[: NAME_SHOWN - 1] + "…")


def guide_text(
    guide: model.Guide, clashes: list[Clash], *, file_name: str = "", note: str = ""
) -> str:
    """The answer in at most ten lines: where, what is ahead, what is next, and the clashes."""
    lines = [note] if note else []
    if file_name:
        lines.append(f"📖 {_short(file_name)}")
    chapter = guide.chapter
    if chapter is None:
        lines.append("🧭 DMbot can't tell where you are yet. Pick your chapter below.")
        return "\n".join(lines)[:TEXT_MAX]
    if guide.picked:
        lines.append(f"🧭 You said you're in **{_short(chapter.name)}**.")
    else:
        names = ", ".join(_short(n) for n in guide.evidence)
        lines.append(f"🧭 You seem to be in **{_short(chapter.name)}** (from: {names}).")
    if guide.ahead:
        more = f", +{guide.ahead_more} more" if guide.ahead_more else ""
        lines.append(
            f"Still ahead in this chapter: {', '.join(_short(n) for n in guide.ahead)}{more}."
        )
    else:
        lines.append("Nothing else is listed in this chapter that your table hasn't met.")
    following = guide.following
    if following is None:
        lines.append(LAST)
    else:
        summary = model.summary_of(following)
        tail = f", {_md(summary)}" if summary else ""
        lines.append(f"Next chapter: **{_short(following.name)}**{tail}")
    for n, clash in enumerate(clashes, 1):
        prefix = f"{n}. " if len(clashes) > 1 else ""
        lines.append(f"{prefix}{clash_line(clash, {})}")
    return "\n".join(lines)[:TEXT_MAX]


async def _checked(interaction: discord.Interaction, campaign_id: str) -> tuple[Any, Any] | None:
    """The campaign and its memory, if this person is one of its DMs, the switch is on Guide me,
    a DM said here they have the right and the owner's plan allows it; else they are told."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    campaigns: Any = _bot(interaction).campaigns
    if campaign.story_mode != STORY_GUIDE or not await campaigns.story_confirmed(
        campaign.guild_id, campaign.id
    ):
        await _tell(interaction, NOT_GUIDE)
        return None
    if not await _bot(interaction).plan_allows(
        "adventure", campaign.guild_id, campaign, fail_open=False
    ):
        await _tell(interaction, NO_PLAN)
        return None
    return campaign, memory


async def _files(interaction: discord.Interaction, campaign: Any) -> list[Any]:
    """The files that have chapters read from them, in the card's order."""
    plan = getattr(_bot(interaction), "plan", None)
    store = getattr(_bot(interaction), "adventure", None)
    if plan is None or store is None:
        return []
    counts = await plan.counts(campaign.guild_id, campaign.id)
    docs = await store.documents(campaign.guild_id, campaign.id)
    return [d for d in docs if d.id in counts and counts[d.id].chapters > 0]


async def start(interaction: discord.Interaction, campaign_id: str) -> None:
    """The button: a private answer, after asking which file when there are several."""
    await _answer_first(interaction)
    found = await _checked(interaction, campaign_id)
    if found is None:
        return
    campaign, _ = found
    files = await _files(interaction, campaign)
    if not files:
        await _replace(
            interaction,
            NO_CHAPTERS if await _anything_read(interaction, campaign) else NOT_READ,
            None,
        )
        return
    if len(files) == 1:
        await show(interaction, campaign_id, files[0].id)
        return
    await _replace(interaction, "🧭 Which file?", FileMenu(campaign.id, files))


async def _anything_read(interaction: discord.Interaction, campaign: Any) -> bool:
    plan = getattr(_bot(interaction), "plan", None)
    counts = await plan.counts(campaign.guild_id, campaign.id) if plan is not None else {}
    return any(c.places + c.people + c.chapters for c in counts.values())


async def show(
    interaction: discord.Interaction,
    campaign_id: str,
    document_id: str,
    *,
    note: str = "",
    undo: int | None = None,
) -> None:
    await _answer_first(interaction, in_place=True)
    found = await _checked(interaction, campaign_id)
    if found is None:
        return
    campaign, memory = found
    nodes, pick = await model.read(memory, campaign.guild_id, campaign.id, document_id)
    guide = model.build(nodes, pick)
    if not guide.chapters:
        await _replace(interaction, NO_CHAPTERS, None)
        return
    watch = getattr(_bot(interaction), "watch", None)
    clashes = (
        await watch.open_for(campaign.guild_id, campaign.id, guide.in_chapter)
        if watch is not None
        else []
    )
    files = await _files(interaction, campaign)
    name = next((d.file_name for d in files if d.id == document_id), "")
    await _replace(
        interaction,
        guide_text(guide, clashes, file_name=name if len(files) > 1 else "", note=note),
        GuideMenu(campaign.id, document_id, guide, clashes, undo, many_files=len(files) > 1),
    )


class FileMenu(_Menu):
    def __init__(self, campaign_id: str, files: list[Any]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.select = _Select(
            self._picked,
            placeholder="Which file?",
            options=[
                discord.SelectOption(label=f.file_name[:LABEL_SHOWN] or "File", value=f.id)
                for f in files[:MENU_MAX]
            ],
        )
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show(interaction, self.campaign_id, self.select.values[0])


class GuideMenu(_Menu):
    """The chapter menu, a words button for each clash, and Use DMbot's guess / Undo."""

    def __init__(
        self,
        campaign_id: str,
        document_id: str,
        guide: model.Guide,
        clashes: list[Clash],
        undo: int | None,
        *,
        many_files: bool = False,
    ) -> None:
        super().__init__()
        self.campaign_id, self.document_id, self.undo = campaign_id, document_id, undo
        self.many_files = many_files
        chapters = list(guide.chapters)
        here = chapters.index(guide.chapter) if guide.chapter in chapters else 0
        start = max(0, min(here - MENU_MAX // 2, len(chapters) - MENU_MAX))
        self.select = _Select(
            self._picked,
            placeholder="I'm in another chapter…",
            options=[
                discord.SelectOption(label=c.name[:LABEL_SHOWN] or "Chapter", value=c.id)
                for c in chapters[start : start + MENU_MAX]
            ],
        )
        self.add_item(self.select)
        for n, clash in enumerate(clashes, 1):
            self.add_item(WordsButton(campaign_id, clash.id, n if len(clashes) > 1 else 0, row=1))
        if guide.picked:
            self.add_item(_Button(self._guess, label="Use DMbot's guess", row=2))
        if undo is not None:
            self.add_item(_Button(self._undo, label="↩ Undo that", row=2))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        try:
            batch = await model.pick(
                memory,
                campaign.guild_id,
                campaign.id,
                self.document_id,
                self.select.values[0],
                interaction.user.id,
            )
        except MemoryRuleError as exc:
            self.stop()
            await show(interaction, self.campaign_id, self.document_id, note=f"⚠️ {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        await show(interaction, self.campaign_id, self.document_id, undo=batch)

    async def _guess(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        batch = await model.clear(memory, campaign.guild_id, campaign.id, self.document_id)
        changed(interaction, campaign)
        self.stop()
        await show(interaction, self.campaign_id, self.document_id, undo=batch)

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None or self.undo is None:
            return
        campaign, memory = found
        note = UNDONE
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.undo)
        except TooLateToUndo:
            note = "Too late to undo that."
        except MemoryRuleError:
            note = "Not undone: it changed again after."
        changed(interaction, campaign)
        self.stop()
        await show(interaction, self.campaign_id, self.document_id, note=note)


class GuideButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:guide:(?P<campaign>[0-9a-f]{32})",
):
    """🧭 What's next?, on the Shared story card and the pre-session note; works after a restart."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:guide:{campaign_id}",
                row=row,
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> GuideButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start(interaction, self.campaign_id)


__all__ = ["GuideButton", "guide_text"]
