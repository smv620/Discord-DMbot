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
from functools import partial
from typing import Any

import discord

from dmbot.campaigns.models import STORY_GUIDE
from dmbot.discord_limits import MESSAGE_MAX, fit_message
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
NOT_GUIDE = (
    "What's next? is off. To turn it on: open Settings, set Shared story to **Guide me**, then "
    "confirm you have the right to use the book."
)
NO_PLAN = (
    "Your plan doesn't include this, or DMbot can't check it right now. Try again in a moment."
)
NOT_READ = (
    "DMbot hasn't read your book yet. Press **Read it now** on the Shared story card, "
    "then try again."
)
NO_CHAPTERS = "DMbot didn't find chapters in this file. Try another file."
LAST = "This is the book's last chapter."
UNDONE = "↩ Undone."
CANT_SAVE = "⚠️ Couldn't save that. Try again."
TEXT_MAX = MESSAGE_MAX - 10  # a little under a whole message
NAME_SHOWN = 40  # characters of a name on a line, so a full message stays under 2000
LABEL_SHOWN = 25
MENU_MAX = 25  # options in a Discord menu


def _short(name: str) -> str:
    return _md(name if len(name) <= NAME_SHOWN else name[: NAME_SHOWN - 1] + "…")


def _fit(lines: list[str]) -> str:
    """The lines as one message: whole lines are dropped from the end, never cut in the middle
    (an open ** would turn the rest of the message bold)."""
    while len("\n".join(lines)) > TEXT_MAX and len(lines) > 1:
        lines.pop()
    return fit_message("\n".join(lines), limit=TEXT_MAX)


def guide_text(
    guide: model.Guide, clashes: list[Clash], *, file_name: str = "", note: str = ""
) -> str:
    """The answer in at most ten lines: where, what is ahead, what is next, and the clashes."""
    lines = [note] if note else []
    if file_name:
        lines.append(f"📖 {_short(file_name)}")
    chapter = guide.chapter
    if chapter is None:
        lines.append("🧭 No names heard yet, so no guess. Pick your chapter below.")
        return _fit(lines)
    if guide.picked:
        lines.append(f"🧭 **{_short(chapter.name)}** (you picked it).")
    else:
        names = ", ".join(_short(n) for n in guide.evidence)
        lines.append(f"🧭 Probably **{_short(chapter.name)}** (heard: {names}).")
    if guide.ahead:
        more = f", +{guide.ahead_more} more" if guide.ahead_more else ""
        names = ", ".join(_short(n) for n in guide.ahead)
        lines.append(f"In the book, this chapter also has: {names}{more}.")
    else:
        lines.append("Nothing else listed in this chapter that your table hasn't met.")
    following = guide.following
    if following is None:
        lines.append(LAST)
    else:
        summary = model.summary_of(following)
        tail = f": {_md(summary)}" if summary else ""
        lines.append(f"The book's next chapter: **{_short(following.name)}**{tail}")
    for n, clash in enumerate(clashes, 1):
        prefix = f"{n}. " if len(clashes) > 1 else ""
        lines.append(f"{prefix}{clash_line(clash, {})}")
    return _fit(lines)


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


async def _files(client: Any, campaign: Any) -> list[Any]:
    """The files that have chapters read from them, in the card's order."""
    plan = getattr(client, "plan", None)
    store = getattr(client, "adventure", None)
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
    files = await _files(_bot(interaction), campaign)
    if not files:
        await _replace(
            interaction,
            NO_CHAPTERS if await _anything_read(_bot(interaction), campaign) else NOT_READ,
            None,
        )
        return
    if len(files) == 1:
        await _show(interaction, found, files, files[0].id)
        return
    await _replace(interaction, "🧭 Which book file?", FileMenu(campaign.id, files))


async def _anything_read(client: Any, campaign: Any) -> bool:
    plan = getattr(client, "plan", None)
    counts = await plan.counts(campaign.guild_id, campaign.id) if plan is not None else {}
    return any(c.places + c.people + c.chapters for c in counts.values())


async def show(
    interaction: discord.Interaction,
    campaign_id: str,
    document_id: str,
    *,
    note: str = "",
    undo: int | None = None,
    start: int | None = None,
) -> None:
    """The answer for one file, after checking (once) that this person may have it."""
    await _answer_first(interaction, in_place=True)
    found = await _checked(interaction, campaign_id)
    if found is None:
        return
    await _show(interaction, found, None, document_id, note=note, undo=undo, start=start)


async def _show(
    interaction: discord.Interaction,
    found: tuple[Any, Any],
    files: list[Any] | None,
    document_id: str,
    *,
    note: str = "",
    undo: int | None = None,
    start: int | None = None,
) -> None:
    """The answer, for a person `_checked` already passed in this same interaction: no second
    check. About nine calls in all: the checks (campaign, confirmation, plan), the files (2), and
    the guide's own three reads (nodes with the pick, linked entries, clashes)."""
    campaign, memory = found
    client = _bot(interaction)
    if files is None:
        files = await _files(client, campaign)
    composed = await compose(client, campaign, memory, files, document_id, note=note, verified=True)
    if composed is None:
        await _replace(interaction, NO_CHAPTERS, None)
        return
    text, guide, clashes = composed
    await _replace(
        interaction,
        text,
        GuideMenu(campaign.id, document_id, guide, clashes, undo, start=start),
    )


async def compose(
    client: Any,
    campaign: Any,
    memory: Any,
    files: list[Any],
    document_id: str,
    *,
    note: str = "",
    verified: bool = False,
) -> tuple[str, model.Guide, list[Clash]] | None:
    """The answer for one file: its text, the guide it was made from and the clashes in it, or
    None when the file has no chapters. The one place the text is made: the 🧭 button and the
    DM sidebar's "what's next?" (#1739) both come here, so they can never differ. `verified`: the
    caller has just checked the three conditions."""
    nodes, pick = await model.read(memory, campaign.guild_id, campaign.id, document_id)
    guide = model.build(nodes, pick)
    if not guide.chapters:
        return None
    watch = getattr(client, "watch", None)
    clashes = (
        await watch.open_for(campaign.guild_id, campaign.id, guide.in_chapter, verified=verified)
        if watch is not None
        else []
    )
    name = next((d.file_name for d in files if d.id == document_id), "")
    return (
        guide_text(guide, clashes, file_name=name if len(files) > 1 else "", note=note),
        guide,
        clashes,
    )


SPOKEN_FILES = 3  # the sidebar answers for at most this many files in one message


async def answer_text(client: Any, campaign: Any) -> str | None:
    """The sidebar's answer (#1739): the same text as the button, for a DM whose three
    conditions the caller has checked. With several files, one block for each (the first few).
    None if DMbot has no memory to read."""
    memory = getattr(client, "memory", None)
    if memory is None:
        return None
    files = await _files(client, campaign)
    if not files:
        return NO_CHAPTERS if await _anything_read(client, campaign) else NOT_READ
    blocks = []
    for doc in files[:SPOKEN_FILES]:
        composed = await compose(client, campaign, memory, files, doc.id, verified=True)
        blocks.append(NO_CHAPTERS if composed is None else composed[0])
    return _fit_blocks(blocks)


def _fit_blocks(blocks: list[str]) -> str:
    """Whole blocks joined by a blank line, the last ones dropped until it fits one message."""
    while len("\n\n".join(blocks)) > TEXT_MAX and len(blocks) > 1:
        blocks.pop()
    return fit_message("\n\n".join(blocks), limit=TEXT_MAX)


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
        start: int | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id, self.document_id, self.undo = campaign_id, document_id, undo
        chapters = list(guide.chapters)
        here = chapters.index(guide.chapter) if guide.chapter in chapters else 0
        # A menu holds 25: the ones around the chapter shown, and Earlier / Later move the window.
        last = max(0, len(chapters) - MENU_MAX)
        self.start = max(0, min(here - MENU_MAX // 2 if start is None else start, last))
        start = self.start
        cut = len(chapters) > MENU_MAX
        self.select = _Select(
            self._picked,
            placeholder=(
                f"Pick my chapter… ({start + 1} to {min(start + MENU_MAX, len(chapters))}"
                f" of {len(chapters)})"
                if cut
                else "Pick my chapter…"
            ),
            options=[
                discord.SelectOption(label=c.name[:LABEL_SHOWN] or "Chapter", value=c.id)
                for c in chapters[start : start + MENU_MAX]
            ],
        )
        self.add_item(self.select)
        for n, clash in enumerate(clashes, 1):
            self.add_item(WordsButton(campaign_id, clash.id, n if len(clashes) > 1 else 0, row=1))
        if start > 0:
            self.add_item(
                _Button(partial(self._move, -MENU_MAX), label="◀ Earlier chapters", row=3)
            )
        if start < last:
            self.add_item(_Button(partial(self._move, MENU_MAX), label="Later chapters ▶", row=3))
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
            log.info("Guide me pick refused: %s", exc)
            self.stop()
            await _show(interaction, found, None, self.document_id, note=CANT_SAVE)
            return
        changed(interaction, campaign)
        self.stop()
        await _show(interaction, found, None, self.document_id, undo=batch)

    async def _move(self, step: int, interaction: discord.Interaction) -> None:
        self.stop()
        await show(
            interaction, self.campaign_id, self.document_id, undo=self.undo, start=self.start + step
        )

    async def _guess(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _checked(interaction, self.campaign_id)
        if found is None:
            return
        campaign, memory = found
        batch = await model.clear(memory, campaign.guild_id, campaign.id, self.document_id)
        changed(interaction, campaign)
        self.stop()
        await _show(interaction, found, None, self.document_id, undo=batch)

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
            note = "Can't undo that any more."
        except MemoryRuleError:
            note = "Not undone. Something changed since. Pick your chapter again if you need to."
        changed(interaction, campaign)
        self.stop()
        await _show(interaction, found, None, self.document_id, note=note)


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
