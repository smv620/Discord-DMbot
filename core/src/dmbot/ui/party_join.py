"""A character joining the party, in Discord (#4537, party knowledge slice 2b;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, 2026-11-18; behind
`DMBOT_PARTY_JOIN`).

- **"Since when has Mira been in the party?"** right after the DM makes a character a player
  character: "From now" (the default, already recorded), "From the first session" (ruling B: Mira
  was there all along, so it also gives Mira what the party knows, one change with its ↩️ Undo) or
  "Or pick a session…" (a later session: the stretch is dated from it and the join question stands).
- **The join question**, one private message to each DM, never during play (`send_join_questions`,
  from the after-session steps and the questions round): "Only what everyone knows" (writes
  nothing) or "What the party knows" (one change with its ↩️ Undo). Characters who joined before it
  went share one message. An unanswered one stays in ❓ Questions for you.

The buttons carry the campaign and the message (or the character), never a name; who may press is
read again on every press, and only a campaign's DMs may. The character is named, never with a
pronoun (ruling C). Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Sequence
from typing import Any, ClassVar

import discord

from dmbot.memory.join_questions import BACK, AlreadyAnswered
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot

log = logging.getLogger(__name__)

EVERYONE_BUTTON = "Only what everyone knows"
BEFORE_BUTTON = "Only what was known before"  # (coming back, #4537 4a: the message names who)
PARTY_BUTTON = "What the party knows"
UNDO = "↩️ Undo"
SINCE = "**Since when has {name} been in the party?**"
SINCE_NOTE = (
    "Not choosing keeps **From now**. **From the first session** also gives {name} what the party "
    "knows. Otherwise DMbot will ask what {name} knows, never during play."
)
FROM_NOW = "From now"
FROM_FIRST = "From the first session"
PICK = "Or pick a session…"
PICK_PLACEHOLDER = "Which session did {name} join?"
ONLY_LAST = "Showing the last 25 sessions."
NOW_DONE = (
    "✅ {name} joins the party from now on. DMbot will ask what {name} knows, never during play."
)
PICKED = (
    "✅ {name} has been in the party since session {n}. DMbot will ask what {name} knows, never "
    "during play."
)
PICKED_NO_NUMBER = (
    "✅ {name} has been in the party since that session. DMbot will ask what {name} knows, never "
    "during play."
)
FIRST_DONE = (
    "✅ {name} has been in the party since the first session, so {name} knows the {things} the "
    "party knew."
)
FIRST_DONE_NONE = (
    "✅ {name} has been in the party since the first session. The party doesn't know anything "
    "yet, so there was nothing to give {name}."
)
FIRST_UNDONE = (
    "↩️ Undone. {name} is in the party from now on. DMbot will ask what {name} knows, never "
    "during play."
)
NO_SESSIONS = "This campaign has no sessions yet, so {name} is in the party from now on."
OVERLAP = (
    "{name} already has party time recorded around then, so nothing changed. Pick a later "
    "session, or open {name}'s card to fix the dates."
)
NOT_IN_PARTY = "{name} isn't in the party now, so nothing changed."
EVERYONE_DONE = (
    "✅ {names} {know} only what everyone knows, plus what's said at the table from now on."
)
PARTY_DONE = "✅ {names} now {know} the {things} the party knew."
PARTY_DONE_NONE = "✅ The party doesn't know anything yet, so there was nothing to give {names}."
UNDONE = (
    "↩️ Undone. {names} {know} only what everyone knows again, plus what was said at the table "
    "since."
)
BEFORE_DONE_ONE = (
    "✅ {names} knows only what was known before leaving, plus what's said at the table from "
    "now on."
)
BEFORE_DONE_MANY = (
    "✅ {names} know only what each knew before leaving, plus what's said at the table from now on."
)
BEFORE_UNDONE_ONE = (
    "↩️ Undone. {names} knows only what {names} knew before leaving, plus what was said at the "
    "table since."
)
BEFORE_UNDONE_MANY = (
    "↩️ Undone. {names} know only what each knew before leaving, plus what was said at the table "
    "since."
)
CANT_UNDO = "Can't undo this one: some of it changed since. Open {name}'s card to fix it."
ANSWERED = "Already answered (by you or another DM). Nothing changed."
FAILED = "Couldn't do that just now. Nothing changed. Try again in a moment."

_E = r"dmbot:pjoine:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32})"
_C = r"dmbot:pjoinc:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32})"
_P = r"dmbot:pjoinp:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32})"
_U = r"dmbot:pjoinu:(?P<campaign>[0-9a-f]{32}):(?P<group>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"
_SINCE = (
    r"dmbot:psince:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<when>now|first|pick)"
)
_FU = r"dmbot:psinceu:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"


def names_text(names: Sequence[str]) -> str:
    """ "Mira", "Mira and Tavin", "Mira, Tavin and Oskar". Pure."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def things(n: int) -> str:
    return "1 thing" if n == 1 else f"{n} things"


def question_text(names: Sequence[str], count: int, *, back: bool = False) -> str:
    """The join question for these characters (the ux-critic's words in Supervisor's ruling;
    the count line is dropped at 0), or with `back` the question about the time away of
    characters who came back (#4537 4a: they keep everything from before). Pure."""
    who = names_text(names)
    one = len(names) == 1
    know = "knows" if one else "know"
    if back:
        head = f"**{who} {'is' if one else 'are'} back in the party.**"
        ask = f"What {'does' if one else 'do'} {who} know about what happened while away?"
        until = (
            f"Until you choose, {who} {know} only "
            + ("what was known before leaving" if one else "what each knew before leaving")
            + ", plus what's said at the table from now on."
        )
    else:
        head = f"**{who} {'has' if one else 'have'} joined the party.**"
        ask = f"What {'does' if one else 'do'} {who} know about what came before?"
        until = (
            f"Until you choose, {who} {know} only what anyone in the world would know, "
            + (f"{who}'s own backstory" if one else "each character's own backstory")
            + ", and what's said at the table from now on."
        )
    lines = [
        f"{head} {ask}",
        until
        + (
            f" Answers to the whole table and recaps leave out the {things(count)} {who} "
            f"{"doesn't" if one else "don't"} know yet."
            if count
            else ""
        ),
        f"You can tell {who} more later from any card.",
    ]
    return "\n".join(lines)


def _refused(exc: Exception, name: str) -> str:
    """The DM's words for a refusal of the party record (the store's own text is for logs)."""
    text = str(exc)
    if "no sessions" in text:
        return NO_SESSIONS.format(name=name)
    if "overlap" in text:
        return OVERLAP.format(name=name)
    return NOT_IN_PARTY.format(name=name)


def _know(n: int) -> str:
    return "knows" if n == 1 else "know"


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any:
    """The campaign, for one of its DMs only ("Only a DM of this campaign can do that.")."""
    from dmbot.ui.family_ties import _dm_campaign as dm_campaign

    return await dm_campaign(interaction, campaign_id)


def _md(text: str) -> str:
    from dmbot.ui.names import _md as md

    return md(text)


async def _edit(interaction: discord.Interaction, text: str, view: Any = None) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(
            content=text, view=view, allowed_mentions=discord.AllowedMentions.none()
        )


async def _names(memory: Any, guild_id: int, campaign_id: str, ids: Sequence[str]) -> list[str]:
    out = []
    for entity_id in ids:
        entity = await memory.entity(guild_id, campaign_id, entity_id)
        out.append(_md(entity.name) if entity is not None else "A character")
    return out


def _question_view(
    campaign_id: str, group_id: str, *, everyone: bool = True, back: bool = False
) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if everyone:
        view.add_item(EveryoneButton(campaign_id, group_id, back=back))
    view.add_item(PartyButton(campaign_id, group_id))
    view.add_item(ChooseButton(campaign_id, group_id))  # ("Let me choose", #4537 4c)
    return view


async def question_for(bot: Any, campaign: Any, group_id: str) -> tuple[str, discord.ui.View]:
    """The text and buttons of one sent join question (also what ❓ Questions for you shows)."""
    questions = await bot.memory.join_question_group(campaign.guild_id, campaign.id, group_id)
    ids = [q.entity_id for q in questions if q.status == "waiting"] or [
        q.entity_id for q in questions
    ]
    names = await _names(bot.memory, campaign.guild_id, campaign.id, ids)
    count = await bot.memory.party_known_count(campaign.guild_id, campaign.id, ids)
    back = _is_back(questions)
    return question_text(names, count, back=back), _question_view(campaign.id, group_id, back=back)


def _is_back(questions: Sequence[Any]) -> bool:
    """A message about characters who came back (one message holds one kind)."""
    return bool(questions) and questions[0].kind == BACK


def _before_words(names: Sequence[str], *, undone: bool = False) -> str:
    """ "Only what was known before" said back, after the answer or an Undo of the copy."""
    one = len(names) == 1
    if undone:
        text = BEFORE_UNDONE_ONE if one else BEFORE_UNDONE_MANY
    else:
        text = BEFORE_DONE_ONE if one else BEFORE_DONE_MANY
    return text.format(names=names_text(names))


async def send_join_questions(bot: Any, guild_id: int, campaign_id: str) -> int:
    """Send the campaign's join questions not sent yet, as one message to each DM privately: never
    while a session is running (the claim takes none then). Returns how many characters it asked
    about. A message no DM could get is given back, to go next time. Never raises."""
    memory = getattr(bot, "memory", None)
    if memory is None or not getattr(bot.settings, "party_join_on", False):
        return 0
    asked = 0
    for _ in range(2):  # (one message per kind: joins, then those who came back, or the reverse)
        sent = await _send_one(bot, memory, guild_id, campaign_id)
        if not sent:
            break
        asked += sent
    return asked


async def _send_one(bot: Any, memory: Any, guild_id: int, campaign_id: str) -> int:
    """One message of `send_join_questions`: 0 when nothing went."""
    group_id = None
    try:
        claimed = await memory.join_questions_to_send(guild_id, campaign_id)
        if not claimed:
            return 0
        group_id = claimed[0].group_id
        campaign = await bot.campaigns.get(guild_id, campaign_id)
        if campaign is None or group_id is None:
            return 0
        text, view = await question_for(bot, campaign, group_id)
        if not await bot.ask_privately(guild_id, campaign_id, text, view):
            await memory.unclaim_join_questions(guild_id, campaign_id, group_id)
            return 0
        log.info("Party join question sent (%d characters)", len(claimed))
        return len(claimed)
    except Exception as exc:
        log.warning("Party join question not sent (%s)", type(exc).__name__)
        if group_id is not None:
            with contextlib.suppress(Exception):
                await memory.unclaim_join_questions(guild_id, campaign_id, group_id)
        return 0


class ChooseButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_C):
    """ "Let me choose": the page of picks, as a new private message (`party_choose`)."""

    def __init__(self, campaign_id: str, group_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Let me choose", style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pjoinc:{campaign_id}:{group_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.group_id = campaign_id, group_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ChooseButton:
        return cls(match["campaign"], match["group"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.party_choose import open_page

        await _answer_first(interaction, in_place=True)
        if await _dm_campaign(interaction, self.campaign_id) is None:  # (a DM only, every press)
            return
        await open_page(interaction, self.campaign_id, self.group_id)


class EveryoneButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_E):
    """ "Only what everyone knows" (or, for those who came back, "Only what was known before"):
    nothing is written; "What the party knows" stays offered."""

    def __init__(self, campaign_id: str, group_id: str, *, back: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=BEFORE_BUTTON if back else EVERYONE_BUTTON,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pjoine:{campaign_id}:{group_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.group_id = campaign_id, group_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> EveryoneButton:
        return cls(match["campaign"], match["group"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # (on every press)
        if campaign is None or bot.memory is None:
            return
        try:
            done = await bot.memory.join_answer_everyone(
                campaign.guild_id, campaign.id, self.group_id
            )
        except AlreadyAnswered:
            await _edit(interaction, ANSWERED)
            return
        except Exception as exc:
            log.warning("Join answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM answered a join question: only what everyone knows")
        names = await _names(
            bot.memory, campaign.guild_id, campaign.id, [q.entity_id for q in done]
        )
        back = _is_back(done)
        text = (
            _before_words(names)
            if back
            else EVERYONE_DONE.format(names=names_text(names), know=_know(len(names)))
        )
        await _edit(
            interaction,
            text,
            _question_view(self.campaign_id, self.group_id, everyone=False, back=back),
        )


class PartyButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_P):
    """ "What the party knows": one change with its ↩️ Undo."""

    def __init__(self, campaign_id: str, group_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=PARTY_BUTTON, style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:pjoinp:{campaign_id}:{group_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.group_id = campaign_id, group_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PartyButton:
        return cls(match["campaign"], match["group"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        try:
            questions = await bot.memory.join_question_group(g, c, self.group_id)
            done = await bot.memory.join_answer_party(g, c, self.group_id)
        except AlreadyAnswered:
            await _edit(interaction, ANSWERED)
            return
        except Exception as exc:
            log.warning("Join answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM answered a join question: what the party knows (%d)", done.value)
        ids = [q.entity_id for q in questions if q.status in ("waiting", "everyone")]
        names = await _names(bot.memory, g, c, ids)
        text = (
            PARTY_DONE.format(
                names=names_text(names), know=_know(len(names)), things=things(done.value)
            )
            if done.value
            else PARTY_DONE_NONE.format(names=names_text(names))
        )
        view = discord.ui.View(timeout=None)
        if done.batch is not None:
            view.add_item(JoinUndoButton(self.campaign_id, self.group_id, done.batch))
        await _edit(interaction, text, view)


class JoinUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_U):
    """↩️ Undo of "What the party knows": the copy goes; the answer reads "Only what everyone
    knows" again, with "What the party knows" offered again."""

    def __init__(self, campaign_id: str, group_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pjoinu:{campaign_id}:{group_id}:{batch}",
            )
        )  # fmt: skip
        self.campaign_id, self.group_id, self.batch = campaign_id, group_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> JoinUndoButton:
        return cls(match["campaign"], match["group"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        questions = await bot.memory.join_question_group(g, c, self.group_id)
        names = await _names(bot.memory, g, c, [q.entity_id for q in questions])
        try:
            await bot.memory.undo(g, c, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO.format(name=names_text(names) or "the character"))
            return
        except Exception as exc:
            log.warning("Undoing a join answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a join answer")
        back = _is_back(questions)
        text = (
            _before_words(names, undone=True)
            if back
            else UNDONE.format(names=names_text(names), know=_know(len(names)))
        )
        await _edit(
            interaction,
            text,
            _question_view(self.campaign_id, self.group_id, everyone=False, back=back),
        )


# ---- "Since when has Mira been in the party?" ----


async def ask_since_when(interaction: discord.Interaction, campaign: Any, entity_id: str) -> None:
    """Asked privately right after a DM made a player character, with the switch on, only when
    that made a join question (a character joining a party that has played): never for a
    campaign's first characters, a party of one, or a new player for a character already in the
    party. A server manager who is not one of the campaign's DMs is not asked."""
    bot = _bot(interaction)
    if bot.memory is None or not getattr(bot.settings, "party_join_on", False):
        return
    if not campaign.is_dm(interaction.user.id):
        return
    try:
        if not await bot.memory.join_question_waiting_for(
            campaign.guild_id, campaign.id, entity_id
        ):
            return
        [name] = await _names(bot.memory, campaign.guild_id, campaign.id, [entity_id])
    except Exception as exc:
        log.warning("Since-when question not shown (%s)", type(exc).__name__)
        return
    view = discord.ui.View(timeout=None)
    for when in ("now", "first", "pick"):
        view.add_item(SinceButton(campaign.id, entity_id, when))
    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(
            SINCE.format(name=name) + "\n" + SINCE_NOTE.format(name=name),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class SinceButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_SINCE):
    """From now · From the first session · Or pick a session…"""

    LABELS: ClassVar[dict[str, str]] = {"now": FROM_NOW, "first": FROM_FIRST, "pick": PICK}

    def __init__(self, campaign_id: str, entity_id: str, when: str) -> None:
        style = discord.ButtonStyle.primary if when == "now" else discord.ButtonStyle.secondary
        super().__init__(
            discord.ui.Button(
                label=self.LABELS.get(when, when), style=style,
                custom_id=f"dmbot:psince:{campaign_id}:{entity_id}:{when}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.when = campaign_id, entity_id, when

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SinceButton:
        return cls(match["campaign"], match["entity"], match["when"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        [name] = await _names(bot.memory, g, c, [self.entity_id])
        if self.when == "now":
            await _edit(interaction, NOW_DONE.format(name=name))
            await send_join_questions(bot, g, c)  # (at once when no session is running)
            return
        if self.when == "first":
            try:
                done = await bot.memory.join_from_first_session(g, c, self.entity_id)
            except MemoryRuleError as exc:
                await _edit(interaction, _refused(exc, name))
                return
            except Exception as exc:
                log.warning("From the first session failed (%s)", type(exc).__name__)
                await _edit(interaction, FAILED)
                return
            log.info("A DM dated a character from the first session (%d)", done.value)
            view = discord.ui.View(timeout=None)
            if done.batch is not None:
                view.add_item(FirstUndoButton(self.campaign_id, self.entity_id, done.batch))
            text = (
                FIRST_DONE.format(name=name, things=things(done.value))
                if done.value
                else FIRST_DONE_NONE.format(name=name)
            )
            await _edit(interaction, text, view)
            return
        await _show_sessions(interaction, bot, campaign, self.entity_id, name)


async def _show_sessions(
    interaction: discord.Interaction, bot: Any, campaign: Any, entity_id: str, name: str
) -> None:
    """ "Or pick a session…": the campaign's last 25 sessions (Discord's limit for one list)."""
    try:
        sessions = await bot.transcripts.sessions(campaign.guild_id, campaign.id, limit=1000)
    except Exception as exc:
        log.warning("Sessions not read for the since-when list (%s)", type(exc).__name__)
        await _edit(interaction, FAILED)
        return
    if not sessions:
        await _edit(interaction, NO_SESSIONS.format(name=name))
        return
    ordered = sorted(sessions, key=lambda s: s.started_at)
    first_at = ordered[0].started_at
    view = discord.ui.View(timeout=900)
    select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
        placeholder=PICK_PLACEHOLDER.format(name=name)[:150],
        options=[
            discord.SelectOption(label=f"Session {s.number}", value=str(s.started_at))
            for s in ordered[-25:]
        ],
    )

    async def picked(pressed: discord.Interaction) -> None:
        await _answer_first(pressed, in_place=True)
        if await _dm_campaign(pressed, campaign.id) is None:
            return
        at = int(select.values[0])
        number = next((s.number for s in ordered if s.started_at == at), None)
        if at == first_at:  # (the first session is "From the first session", ruling B)
            await SinceButton(campaign.id, entity_id, "first").callback(pressed)
            return
        try:
            await bot.memory.join_from_session(campaign.guild_id, campaign.id, entity_id, at)
        except MemoryRuleError as exc:
            await _edit(pressed, _refused(exc, name))
            return
        except Exception as exc:
            log.warning("Picking a session failed (%s)", type(exc).__name__)
            await _edit(pressed, FAILED)
            return
        log.info("A DM dated a character from a picked session")
        done_text = PICKED_NO_NUMBER if number is None else PICKED
        await _edit(pressed, done_text.format(name=name, n=number))
        await send_join_questions(bot, campaign.guild_id, campaign.id)

    select.callback = picked  # type: ignore[method-assign,assignment]  # (as claims_review)
    view.add_item(select)
    heading = SINCE.format(name=name) + ("\n" + ONLY_LAST if len(ordered) > 25 else "")
    await _edit(interaction, heading, view)


class FirstUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_FU):
    """↩️ Undo of "From the first session": the stretch is from now again, nothing copied, and
    the join question waits again."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:psinceu:{campaign_id}:{entity_id}:{batch}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.batch = campaign_id, entity_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FirstUndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        [name] = await _names(bot.memory, campaign.guild_id, campaign.id, [self.entity_id])
        try:
            await bot.memory.undo(campaign.guild_id, campaign.id, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO.format(name=name))
            return
        except Exception as exc:
            log.warning("Undoing from the first session failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a from-the-first-session")
        await _edit(interaction, FIRST_UNDONE.format(name=name))
        await send_join_questions(bot, campaign.guild_id, campaign.id)


# ---- ❓ Questions for you ----

OPEN_LABEL = "🧭 New in the party"
WAIT_FOR_PLAY = "A session is running. DMbot will ask about the new party members when it ends."
NOTHING_LEFT = "No new party members to ask about."
_OPEN = r"dmbot:pjoino:(?P<campaign>[0-9a-f]{32})"


class JoinsOpenButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_OPEN):
    """🧭 New in the party, from ❓ Questions for you: each waiting join question again, privately,
    for a DM only. One not sent yet goes now, unless a session is running."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=OPEN_LABEL, style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:pjoino:{campaign_id}",
            )
        )  # fmt: skip
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> JoinsOpenButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        try:
            # (one not sent yet is claimed here and shown in this reply only, never sent twice)
            sent = len(await bot.memory.join_questions_to_send(g, c))
            groups = await bot.memory.join_question_groups_waiting(g, c)
            waiting, _ = await bot.memory.join_questions_waiting(g, c)
            shown = [await question_for(bot, campaign, group) for group in groups[:5]]
        except Exception as exc:
            log.warning("Join questions not shown (%s)", type(exc).__name__)
            await _followup(interaction, FAILED)
            return
        if not shown:
            await _followup(interaction, WAIT_FOR_PLAY if waiting and not sent else NOTHING_LEFT)
            return
        for text, view in shown:
            await _followup(interaction, text, view)


async def _followup(interaction: discord.Interaction, text: str, view: Any = None) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(
            text,
            view=view or discord.utils.MISSING,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


DYNAMIC_ITEMS = (
    ChooseButton,
    EveryoneButton,
    PartyButton,
    JoinUndoButton,
    SinceButton,
    FirstUndoButton,
    JoinsOpenButton,
)

__all__ = [
    "DYNAMIC_ITEMS",
    "JoinsOpenButton",
    "ask_since_when",
    "names_text",
    "question_for",
    "question_text",
    "send_join_questions",
    "things",
]
