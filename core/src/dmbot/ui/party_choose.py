"""The join question's "Let me choose" (#4537, party knowledge slice 4c;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, 2026-11-19, G to J; the
ux-critic's words).

- **[Let me choose]** on the join question (and on the coming-back one) sends a private page to
  the DM: nothing changes until a pick. Two menus, "Catch up by session…" and "Catch up by story
  thread…", list only what has something to give, with how many things for each character; a
  line points at 👁️ Who knows this? for anything else, and says why lore isn't under a session
  (Supervisor's ruling G: a session holds what came up in it, and lore came up in none).
- **Each pick** is one change (`MemoryStore.join_choose`) and its own new message with ↩️ Undo, so
  an earlier pick's Undo stays where it is. The first pick answers the question; undoing that one
  puts the question's own words back ("…knows only what everyone knows again"); a later pick's
  Undo takes just what it gave.
- The design calls "Let me choose" a link to the DM Bot channel or the website (rule 10): in
  Discord it is this private page, as the slice 4 plan said; the website's is #4799 (WebDev).

The menus and buttons carry the campaign, the question's message and the change's number, never a
name; who may press is read again on every press, and only a campaign's DMs may. Characters are
named, never with a pronoun (ruling C). Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.memory.join_questions import BACK, AlreadyAnswered
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot
from dmbot.ui.party_join import ONLY_LAST, _before_words, _names, names_text, things
from dmbot.ui.party_tell import CANT_UNDO

log = logging.getLogger(__name__)

CHOOSE_BUTTON = "Let me choose"
HEAD = "**Choose what {who} {know}.** Nothing changes until you pick."
UNTIL_ONE = "Until then {who} knows only what everyone knows."
UNTIL_MANY = "Until then each knows only what everyone knows."
UNTIL_BACK_ONE = "Until then {who} knows only what was known before leaving."
UNTIL_BACK_MANY = "Until then each knows only what each knew before leaving."
EACH = (
    "Each pick gives {who} what the rest of the party knows from that session or story thread, "
    "with its own ↩️ Undo."
)
LORE = (
    "Uploaded lore and notes you typed in aren't part of any session. Find those by story thread, "
    "or use 👁️ Who knows this? on any card."
)
THREADS_CUT = (
    "Showing the 25 story threads with the most to give. For the rest, use 👁️ Who knows this? "
    "on any card."
)
NOTHING_AT_ALL = (
    "There's nothing left to choose: {who} {know} everything the rest of the party knows from the "
    "sessions and story threads. To give something else, use 👁️ Who knows this? on it."
)
BY_SESSION = "Catch up by session…"
BY_THREAD = "Catch up by story thread…"
DONE_ONE = (
    "✅ {name} now knows the {things} the rest of the party knew from {source}. Nothing was sent "
    "to {name}'s player."
)
DONE_MANY = "✅ From {source}, {each}. Nothing was sent to the players."
NOTHING_LEFT_ONE = (
    "{who} already knows everything the rest of the party knows from {source} (another DM may "
    "have given it). Nothing changed."
)
NOTHING_LEFT_MANY = (
    "{who} already know everything the rest of the party knows from {source} (another DM may "
    "have given it). Nothing changed."
)
UNDO = "↩️ Undo"
UNDONE_PICK = "↩️ Undone. {who} no longer {know} what that pick gave."
ANSWERED = "Already answered (by you or another DM). Nothing changed."
FAILED = "Couldn't do that just now. Nothing changed. Try again in a moment."
LABEL_MAX = 80
DESCRIPTION_MAX = 100

_PICK = r"dmbot:pchoose:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32}):(?P<kind>[st])"
# (at most 92 characters, under Discord's 100: two ids, a 12-digit change and the first-pick mark)
_UNDO = (
    r"dmbot:pchu:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32}):(?P<batch>[0-9]{1,12})"
    r":(?P<first>[01])"
)


def _cut(text: str, room: int) -> str:
    return text if len(text) <= room else text[: max(1, room - 1)].rstrip() + "…"


def _know(n: int) -> str:
    return "knows" if n == 1 else "know"


def count_words(each: Mapping[str, int], names: Mapping[str, str]) -> str:
    """A menu option's description: "14 things Mira doesn't know yet", or for several "Mira 14 ·
    Tavin 9" (cut to Discord's 100, never the first count). Pure."""
    if len(each) == 1:
        [(member, n)] = each.items()
        return _cut(f"{things(n)} {names.get(member, 'A character')} doesn't know yet", 100)
    parts = [f"{names.get(m, 'A character')} {n}" for m, n in each.items()]
    return _cut(" · ".join(parts), DESCRIPTION_MAX)


def page_text(names: Sequence[str], *, back: bool, sessions_cut: bool, threads_cut: bool) -> str:
    """The "Let me choose" page's words, for the question's characters. Pure."""
    who, one = names_text(names), len(names) == 1
    until = (
        (UNTIL_BACK_ONE if one else UNTIL_BACK_MANY) if back else (UNTIL_ONE if one else UNTIL_MANY)
    ).format(who=who)
    lines = [HEAD.format(who=who, know=_know(len(names))) + " " + until, EACH.format(who=who)]
    if sessions_cut:
        lines.append(ONLY_LAST)
    if threads_cut:
        lines.append(THREADS_CUT)
    lines.append(LORE)
    return "\n".join(lines)


def done_text(told: Mapping[str, int], names: Mapping[str, str], source: str) -> str:
    """After a pick: "✅ Mira now knows the 14 things the rest of the party knew from session 3.
    …", or for several "✅ From session 3, Mira now knows 14 more things and Tavin 9. …". Pure."""
    if len(told) == 1:
        [(member, n)] = told.items()
        name = names.get(member, "A character")
        return DONE_ONE.format(name=name, things=things(n), source=source)
    parts = [(names.get(m, "A character"), n) for m, n in told.items()]
    first = (
        f"{parts[0][0]} now knows {parts[0][1]} more {'thing' if parts[0][1] == 1 else 'things'}"
    )
    rest = [f"{name} {n}" for name, n in parts[1:]]
    return DONE_MANY.format(source=source, each=names_text([first, *rest]))


def nothing_left_text(names: Sequence[str], source: str) -> str:
    who = names_text(names)
    return (NOTHING_LEFT_ONE if len(names) == 1 else NOTHING_LEFT_MANY).format(
        who=who, source=source
    )


def session_source(number: int) -> str:
    return f"session {number}"


def thread_source(name: str) -> str:
    return f"the story thread **{name}**"


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any:
    from dmbot.ui.family_ties import _dm_campaign as dm_campaign

    return await dm_campaign(interaction, campaign_id)


def _md(text: str) -> str:
    from dmbot.ui.names import _md as md

    return md(text)


async def _send(interaction: discord.Interaction, text: str, view: Any = None) -> None:
    """A new private message under the pressed one (the page and each pick's answer stay)."""
    with contextlib.suppress(discord.HTTPException):
        kwargs: dict[str, Any] = {"allowed_mentions": discord.AllowedMentions.none()}
        if view is not None:
            kwargs["view"] = view
        await interaction.followup.send(text, **kwargs)


async def _who(bot: Any, campaign: Any, members: Sequence[str]) -> dict[str, str]:
    found = await _names(bot.memory, campaign.guild_id, campaign.id, members)
    return dict(zip(members, found, strict=True))


async def open_page(interaction: discord.Interaction, campaign_id: str, group_id: str) -> None:
    """[Let me choose]: the page, as a new private message (a DM only, read again)."""
    await _answer_first(interaction, in_place=True)
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.memory is None:
        return
    g, c = campaign.guild_id, campaign.id
    try:
        choices = await bot.memory.join_choices(g, c, group_id)
        questions = await bot.memory.join_question_group(g, c, group_id)
    except Exception as exc:
        log.warning("The Let me choose page failed (%s)", type(exc).__name__)
        await _send(interaction, FAILED)
        return
    if not choices.members:
        await _send(interaction, ANSWERED)
        return
    names = await _who(bot, campaign, choices.members)
    listed = list(names.values())
    if not choices.sessions and not choices.threads:
        who = names_text(listed)
        await _send(interaction, NOTHING_AT_ALL.format(who=who, know=_know(len(listed))))
        return
    back = bool(questions) and questions[0].kind == BACK
    text = page_text(
        listed,
        back=back,
        sessions_cut=choices.sessions_in_all > len(choices.sessions),
        threads_cut=choices.threads_in_all > len(choices.threads),
    )
    plain = {m: n.replace("\\", "") for m, n in names.items()}  # (menus show text, not markdown)
    view = discord.ui.View(timeout=None)
    if choices.sessions:
        view.add_item(
            PickSelect(
                campaign_id, group_id, "s",
                [
                    (f"s{at}", f"Session {number}", count_words(each, plain))
                    for at, number, each in choices.sessions
                ],
            )
        )  # fmt: skip
    if choices.threads:
        view.add_item(
            PickSelect(
                campaign_id, group_id, "t",
                [
                    (f"t{tid}", _cut(name, LABEL_MAX), count_words(each, plain))
                    for tid, name, each in choices.threads
                ],
            )
        )  # fmt: skip
    log.info(
        "Let me choose page shown (%d sessions, %d story threads)",
        len(choices.sessions),
        len(choices.threads),
    )
    await _send(interaction, text, view)


class PickSelect(discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]], template=_PICK):
    """One of the page's two menus (`kind` s: sessions, t: story threads)."""

    def __init__(
        self,
        campaign_id: str,
        group_id: str,
        kind: str,
        options: Sequence[tuple[str, str, str]] = (),
    ) -> None:
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder=BY_SESSION if kind == "s" else BY_THREAD,
            custom_id=f"dmbot:pchoose:{campaign_id}:{group_id}:{kind}",
            options=[
                discord.SelectOption(label=label, value=value, description=description)
                for value, label, description in options
            ]
            # (a menu needs one option even when rebuilt from its id after a restart)
            or [discord.SelectOption(label=BY_SESSION if kind == "s" else BY_THREAD, value="-")],
        )
        super().__init__(select)
        self.campaign_id, self.group_id, self.kind = campaign_id, group_id, kind

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PickSelect:
        return cls(match["campaign"], match["group"], match["kind"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        data: Any = interaction.data
        values = (data or {}).get("values") or []
        await pick(interaction, self.campaign_id, self.group_id, str(values[0]) if values else "")


async def pick(
    interaction: discord.Interaction, campaign_id: str, group_id: str, value: str
) -> None:
    """One pick ("s<start>" or "t<thread id>"): one change, answered in a new message with its
    own ↩️ Undo (a DM only, read again)."""
    await _answer_first(interaction, in_place=True)
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.memory is None:
        return
    g, c = campaign.guild_id, campaign.id
    session = re.fullmatch(r"s([0-9]{1,15})", value)
    thread = re.fullmatch(r"t([0-9a-f]{32})", value)
    if session is None and thread is None:
        await _send(interaction, FAILED)
        return
    try:
        choices = await bot.memory.join_choices(g, c, group_id)
        members = choices.members
        if session is not None:
            at = int(session[1])
            numbers = {s: n for s, n, _ in choices.sessions}
            source = session_source(numbers[at]) if at in numbers else "that session"
            done = await bot.memory.join_choose(g, c, group_id, session_at=at)
        else:
            assert thread is not None
            entity = await bot.memory.entity(g, c, thread[1])
            source = thread_source(_md(entity.name)) if entity is not None else "that story thread"
            done = await bot.memory.join_choose(g, c, group_id, thread_id=thread[1])
    except AlreadyAnswered:
        await _send(interaction, ANSWERED)
        return
    except Exception as exc:
        log.warning("A Let me choose pick failed (%s)", type(exc).__name__)
        await _send(interaction, FAILED)
        return
    names = await _who(bot, campaign, members)
    told, first = done.value
    if not told or done.batch is None:
        await _send(interaction, nothing_left_text(list(names.values()), source))
        return
    log.info("A DM picked for a join question (%d characters)", len(told))
    view = discord.ui.View(timeout=None)
    view.add_item(ChooseUndoButton(campaign_id, group_id, done.batch, first))
    await _send(interaction, done_text(told, names, source), view)


class ChooseUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩️ Undo of one pick. The first pick's Undo puts the question back as it was asked."""

    def __init__(self, campaign_id: str, group_id: str, batch: int, first: bool) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pchu:{campaign_id}:{group_id}:{batch}:{int(first)}",
            )
        )  # fmt: skip
        self.campaign_id, self.group_id, self.batch, self.first = (
            campaign_id,
            group_id,
            batch,
            first,
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ChooseUndoButton:
        return cls(match["campaign"], match["group"], int(match["batch"]), match["first"] == "1")

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.party_join import UNDONE

        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        questions = await bot.memory.join_question_group(g, c, self.group_id)
        listed = await _names(bot.memory, g, c, [q.entity_id for q in questions])
        who = names_text(listed) or "the character"
        try:
            await bot.memory.undo(g, c, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO.format(name=who))
            return
        except Exception as exc:
            log.warning("Undoing a pick failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a pick")
        if self.first:
            back = bool(questions) and questions[0].kind == BACK
            text = (
                _before_words(listed, undone=True)
                if back
                else UNDONE.format(names=who, know=_know(len(listed)))
            )
        else:
            text = UNDONE_PICK.format(who=who, know=_know(len(listed)))
        await _edit(interaction, text)


async def _edit(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(
            content=text, view=None, allowed_mentions=discord.AllowedMentions.none()
        )


DYNAMIC_ITEMS = (PickSelect, ChooseUndoButton)

__all__ = [
    "CHOOSE_BUTTON",
    "DYNAMIC_ITEMS",
    "count_words",
    "done_text",
    "nothing_left_text",
    "open_page",
    "page_text",
]
