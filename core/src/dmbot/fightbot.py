"""FightBot (#1129; docs/PLAN.md, "FightBot"): while the table is in combat, a short private
reminder to each player of their own options that could help now ("You still have Hunter's Mark
and Cure Wounds"). It advises; the player decides. It never rules on anything and never posts
publicly: every message is a private message to that one player.

What it may read is limited by `memory.fight_prompt`: only that player's own sheet, the round,
and what people who agreed said at the table lately, minus any line with a secret name. Never
the DM screen, monster stats, DM notes, or another player's private messages.

Limits: only in combat, only players who agreed to be recorded, at most one AI call (and so one
message) per player every 2 minutes, and only when something changed since the last (a new
round, hit points, a condition). Each message has **Snooze 10 min**, **Quiet until next
session** and **Never**; "Never" is kept per player per campaign and can be undone from
📜 My character sheet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import discord

from dmbot import fight_style
from dmbot.ai import AIError
from dmbot.memory import fight_prompt
from dmbot.memory.sheet_store import CharacterSheet, FightPlayer
from dmbot.sidebar.service import SCENE_SECONDS

log = logging.getLogger(__name__)

REMIND_GAP_S = 120.0  # per player, between one call (and so one message) and the next
SNOOZE_S = 600
MAX_CALLS_PER_TICK = 2  # a table never gets a burst of AI calls
TICK_S = 30
MAX_TOKENS = 120
_ID = r"(?P<campaign>[0-9a-f]{32})"
SNOOZED = "😴 Okay, quiet for 10 minutes."
QUIET = "🤫 Okay, quiet until the next session."
NO_SESSION = "There is no game running just now, so there is nothing to quiet."
NEVER = "🚫 FightBot is off for you. To turn it on, tap 📜 My character sheet."
NOT_SAVED = "That didn't save. Tap the button again."
NOT_PLAYING = "FightBot is for players with a character in this campaign. Ask your DM to add yours."
FOOTER = "FightBot reminder. Only you see this. It's your call."
INTRO = (
    "FightBot privately reminds you of your own spells and features in a fight. "
    "Tap **Never** to turn it off.\n"
)


@dataclass(frozen=True, slots=True)
class Due:
    """A moment FightBot should remind a player about (#1771): their turn is next, their side
    acts, initiative is rolled again, or the DM asked what everyone will do. Kept in memory;
    dropped once the moment has passed (the turn moved on, or the round ended)."""

    kind: str  # "turn", "next", "side", "reroll" or "declare"
    round: int
    turn_no: int
    seq: int
    at: float  # when it was heard (monotonic): a moment does not wait long


MOMENT_TTL_S = 180.0  # a moment not acted on by then has passed
ORDER_MAX = 64  # creatures remembered in a fight's order

MOMENT_LINES = {
    "turn": "The DM just called your name",
    "next": "The DM seems to be calling your turn next",
    "side": "The DM seems to be calling your side now",
    "reroll": "The DM seems to be starting a new round of initiative",
    "declare": "The DM seems to be asking everyone what they will do",
}


@dataclass(slots=True)
class FightState:
    """What one session remembers in memory only: the last state each player's character was
    reminded about (so nothing is said twice for the same thing) and who has been told what
    FightBot is. Everything that must survive a restart is in the database
    (`fightbot_players`)."""

    last_fp: dict[tuple[int, str], tuple[Any, ...]] = field(default_factory=dict)
    introduced: set[int] = field(default_factory=set)
    # How the table runs initiative, as heard this session (#1771): kept from one fight to the
    # next, and gone with the session. The rest is per fight.
    style: fight_style.StyleState = field(default_factory=fight_style.StyleState)
    round: int | None = None  # the fight's round as of the last pass; None out of combat
    present: set[int] = field(default_factory=set)  # who is at the table, as of the last pass
    names: dict[str, int] = field(default_factory=dict)  # lower-case name -> person
    secrets: list[str] | None = None  # secret names, as of the last pass; None: not known
    turn_no: int = 0  # turns called this fight
    order_seen: list[tuple[str, int | str]] = field(default_factory=list)  # who went, in order
    turns_this_round: bool = False
    declare_heard: bool = False
    due: dict[int, Due] = field(default_factory=dict)
    seq: int = 0
    saved: str | None = None  # the style the campaign keeps, as of now (#1784)
    last_called: tuple[str, int | str] | None = None
    last_due_seq: dict[int, int] = field(default_factory=dict)  # per player, the last one acted on

    @classmethod
    def starting_from(cls, saved: str | None) -> FightState:
        """A new session's state, starting from the style the campaign kept last time."""
        return cls(style=fight_style.StyleState.from_saved(saved), saved=saved)

    def new_due(self, kind: str) -> Due:
        self.seq += 1
        return Due(kind, self.round or 0, self.turn_no, self.seq, time.monotonic())

    def moved_to(self, round_number: int | None) -> bool:
        """The fight is now at this round (None: it is over). A new round ends the old one's
        moments; a round that played out with turns called and no call for declarations means
        the table doesn't declare. True if the heard style changed. Called as soon as the clock
        moves (`clock_screen.show`) and again at each pass, so a line heard just after the DM
        moved the round is counted in the right one."""
        if round_number is None:
            return self.end_fight()
        changed = False
        if self.round is not None and round_number > self.round:
            if self.turns_this_round and not self.declare_heard:
                changed = self.style.infer_no_declarations()
            self.turns_this_round = self.declare_heard = False
        self.round = round_number
        return changed

    def end_fight(self) -> bool:
        """The fight is over: its order, turns and pending moments go. What the table's style
        was heard to be stays for the next fight. True if the heard style changed."""
        self.round = None
        self.turn_no = 0
        self.order_seen.clear()
        self.turns_this_round = self.declare_heard = False
        self.due.clear()
        self.last_called = None
        self.last_due_seq.clear()
        return self.style.forget_inferred()

    def due_now(self) -> dict[int, Due]:
        """The pending moments that have not passed: a turn moment ends when the next turn is
        called, any moment when the round ends."""
        now = time.monotonic()
        self.due = {user: d for user, d in self.due.items() if self.still_due(d, now)}
        return self.due

    def still_due(self, d: Due, now: float) -> bool:
        # A "roll initiative" often comes just as the round changes: it keeps one round.
        same_round = d.round == self.round or (
            d.kind == "reroll" and self.round is not None and d.round == self.round - 1
        )
        return (
            same_round
            and now - d.at <= MOMENT_TTL_S
            and (d.kind not in ("turn", "next") or d.turn_no == self.turn_no)
        )


def note_line(table: Any, user_id: int, line: str, *, is_dm: bool, consenting: Any) -> bool:
    """A line of table talk, heard for the way the table runs initiative (#1771). Called for
    cleaned voice lines of people who agreed and a DM's typed lines. Nothing here touches the
    combat state, the round or what a player sees; it only lets FightBot time its reminders.
    Only a DM's line moves a turn or calls a moment; players' lines can only vote for a style,
    and only during a fight. Returns True if what the DM's combat line says changed."""
    fight: FightState | None = getattr(table, "fight", None)
    if fight is None:
        return False
    secrets = fight.secrets
    if secrets is None:  # not known yet: nothing is built without being able to leave them out
        return False
    lowered = line.lower()
    # (the list is as of the last pass, up to 30 seconds old: all that is kept from a line is a
    # person's id and a creature key, which are never sent or logged)
    if any(secret in lowered for secret in secrets):
        return False
    heard = fight_style.hear(line, fight.names, round_number=fight.round)
    if heard is None:
        return False
    if not is_dm and fight.round is None:
        return False  # a casual remark out of a fight sets nothing
    if heard.order == "each" and not (heard.turn or heard.on_deck or heard.creature) and not is_dm:
        return False  # a name nobody has: only a DM's word makes that a style
    before = fight.style.dm_phrase()
    style = fight.style
    if heard.order is not None:
        style.vote(
            "order", heard.order, user_id, from_dm=is_dm, consenting=consenting, guessed=heard.guess
        )
    if heard.declarations is not None:
        style.vote(
            "declarations", heard.declarations, user_id, from_dm=is_dm, consenting=consenting
        )
    if is_dm and fight.round is not None:
        _call_moments(table, fight, heard)
    return style.dm_phrase() != before


def _call_moments(table: Any, fight: FightState, heard: fight_style.Heard) -> None:
    """A DM's line calls moments (their turn, their side, declarations, a new roll)."""
    dms = getattr(table, "dm_user_ids", ())
    everyone = sorted(u for u in fight.present if u not in dms)  # (players, not the DM)
    if heard.declare_call:
        fight.declare_heard = True
        # Everyone says their move first: no "your turn is next" this round.
        fight.due = {u: d for u, d in fight.due.items() if d.kind not in ("turn", "next")}
        for user in everyone:
            fight.due[user] = fight.new_due("declare")
    if heard.reroll_call:
        fight.order_seen.clear()  # a new roll, a new order
        for user in everyone:
            fight.due[user] = fight.new_due("reroll")
    if heard.players_side:
        for user in everyone:
            fight.due[user] = fight.new_due("side")
    for user in heard.on_deck:
        if user in fight.present and user not in dms and not fight.declare_heard:
            fight.due[user] = fight.new_due("next")
    for user in heard.turn:
        _turn_called(fight, ("p", user), dms)
    if heard.creature is not None:
        _turn_called(fight, ("m", heard.creature), dms)


def _turn_called(fight: FightState, key: tuple[str, int | str], dms: Any = ()) -> None:
    """A creature's turn was called. The order is learned from the turns called; a player not
    yet placed is reminded now, and once the order is known the player after the one called is
    reminded that their turn is next (the first one again after the last)."""
    if key == fight.last_called:
        return  # the same words twice (a re-transcribed line) are one turn
    fight.last_called = key
    fight.turn_no += 1
    fight.turns_this_round = True
    if key not in fight.order_seen:
        if len(fight.order_seen) < ORDER_MAX:
            fight.order_seen.append(key)
        if (
            key[0] == "p"
            and key[1] in fight.present
            and key[1] not in dms
            and not fight.declare_heard
        ):
            fight.due[int(key[1])] = fight.new_due("turn")
        return
    at = fight.order_seen.index(key)
    if fight.style.order == "each" and len(fight.order_seen) > 1 and (fight.round or 0) >= 2:
        following = fight.order_seen[(at + 1) % len(fight.order_seen)]
        reminded = following[0] == "p" and following[1] in fight.present
        if reminded and following[1] not in dms and not fight.declare_heard:
            fight.due[int(following[1])] = fight.new_due("next")


def observe(
    fight: FightState, round_number: int | None, present: set[int], consenting: Any
) -> bool:
    """Once a pass: note the round, who is here, and whose lines still count. True if what
    the DM's combat line says changed."""
    before = fight.style.dm_phrase()
    fight.style.prune(consenting)
    fight.moved_to(round_number)
    if round_number is not None:
        fight.present = present
    return fight.style.dm_phrase() != before


def may_remind(state: FightPlayer, session_started: int, now: float) -> bool:
    """Not Never, not quiet for this session, not snoozed, and at least two minutes since the
    last call for this player: all kept in the database, so a restart forgets none of it."""
    if state.never or state.quiet_since == session_started:
        return False
    if state.snoozed_until is not None and now < state.snoozed_until:
        return False
    return state.last_call_at is None or now - state.last_call_at >= REMIND_GAP_S


async def run(bot: Any, table: Any) -> None:
    """The loop for one session: look every half minute; ends with the session."""
    while bot.tables.get(table.guild_id) is table:
        await asyncio.sleep(TICK_S)
        if not table.listening or table.campaign_id is None:
            continue
        try:
            await tick(bot, table)
        except Exception as exc:  # never the text: it can quote a row
            log.error("FightBot: a pass failed (%s)", type(exc).__name__)


async def _secret_texts(bot: Any, table: Any) -> list[str] | None:
    """The campaign's secret names as text, or None if they can't be known (then nothing is
    sent: a reminder must never be built without being able to leave them out)."""
    if bot.lookup is None:
        return None
    try:
        lookup = await bot.lookup.get(table.guild_id, table.campaign_id)
    except Exception:
        return None
    return [n.text for n in lookup.names if n.secret]


def _consenting(bot: Any, table: Any) -> Any:
    return lambda user: bool(bot.consent.has_consent(table.guild_id, user))


def _names(bot: Any, table: Any, found: list[CharacterSheet]) -> dict[str, int]:
    """Names a turn can be called by -> the person: their characters' names and the names they
    go by at the table (matched whole and without case), and a first name when it is unique."""
    names: dict[str, int] = {}
    guild = bot.get_guild(table.guild_id)
    channel = guild.get_channel(table.voice_channel_id) if guild is not None else None
    dms = getattr(table, "dm_user_ids", ())
    for member in getattr(channel, "members", []):
        shown = getattr(member, "display_name", None)
        if not member.bot and shown and member.id not in dms:
            names[str(shown).lower()] = member.id
    for sheet in found:
        if sheet.played_by is not None and sheet.name:
            names[sheet.name.lower()] = sheet.played_by
    firsts: dict[str, set[int]] = {}
    for full, user in names.items():
        first = full.split()[0]
        firsts.setdefault(first, set()).add(user)
    for first, users in firsts.items():
        if len(users) == 1:
            names.setdefault(first, next(iter(users)))
    return names


async def _style_changed(bot: Any, table: Any) -> None:
    """The heard style changed: the DM's combat line shows it. Never raises."""
    hook = getattr(bot, "fight_style_changed", None)
    if hook is None:
        return
    try:
        await hook(table)
    except Exception as exc:
        log.info("FightBot: couldn't refresh the combat line (%s)", type(exc).__name__)


def _present(bot: Any, table: Any) -> set[int]:
    """The people in the table's voice channel right now, not counting bots: only they are at
    the table tonight (consent and a sheet carry over, but an absent player is not reminded)."""
    guild = bot.get_guild(table.guild_id)
    channel = guild.get_channel(table.voice_channel_id) if guild is not None else None
    return {m.id for m in getattr(channel, "members", []) if not m.bot}


async def tick(bot: Any, table: Any) -> None:
    if bot.fight_ai is None or bot.sheets is None or bot.clocks is None:
        return
    stored = await bot.clocks.get(table.guild_id, table.campaign_id)
    fight: FightState = table.fight
    if stored is None or stored.clock.combat_round is None:
        fight.last_fp.clear()  # out of combat: the next fight starts fresh
        if fight.end_fight():
            await _style_changed(bot, table)
        return
    round_number: int = stored.clock.combat_round
    present = _present(bot, table)
    consenting = _consenting(bot, table)
    if observe(fight, round_number, present, consenting):
        await _style_changed(bot, table)
    if not present:
        return
    found: list[CharacterSheet] = await bot.sheets.sheets(table.guild_id, table.campaign_id)
    players = await bot.sheets.fight_players(table.guild_id, table.campaign_id)
    fight.names = _names(bot, table, found)
    heard_secrets = await _secret_texts(bot, table)  # (also what `note_line` leaves out)
    fight.secrets = None if heard_secrets is None else [s.lower() for s in heard_secrets if s]
    timed = fight.style.known  # a style is heard: remind at the moment, not on a timer
    due = fight.due_now() if timed else {}
    wall = time.time()
    secrets = fight.secrets
    allowed: bool | None = None
    calls = 0
    done: set[int] = set()  # players called this pass: one call each, however many characters
    for sheet in found:
        user = sheet.played_by
        if user is None or sheet.sheet is None or user not in present or user in done:
            continue
        if timed and user not in due:
            continue  # nothing is about to happen for this player
        if not may_remind(players.get(user, FightPlayer()), table.started_at, wall):
            continue
        if not bot.consent.has_consent(table.guild_id, user):
            continue
        fp = fight_prompt.fingerprint(sheet.sheet, round_number)
        if timed:  # a new turn or a call for declarations is what counts as a change
            if fight.last_due_seq.get(user) == due[user].seq:
                continue
        elif fight.last_fp.get((user, sheet.entity_id)) == fp:
            continue  # nothing new since the last reminder
        if calls >= MAX_CALLS_PER_TICK:
            break  # the rest next time
        if secrets is None:  # the secret names can't be known: nothing is built without them
            return
        if allowed is None:  # once a tick, when the first player is due
            campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
            allowed = campaign is not None and await bot.plan_allows("ai", table.guild_id, campaign)
        if not allowed:
            return
        # Checked again after the awaits: the session, the pause and their consent.
        if (
            bot.tables.get(table.guild_id) is not table
            or not table.listening
            or not bot.consent.has_consent(table.guild_id, user)
        ):
            return
        calls += 1
        done.add(user)
        moment_due = due.get(user) if timed else None
        moment = MOMENT_LINES[moment_due.kind] if moment_due is not None else None
        if moment_due is not None:
            fight.last_due_seq[user] = moment_due.seq
        fight.due.pop(user, None)
        fight.last_fp[(user, sheet.entity_id)] = fp
        await bot.sheets.fight_called(table.guild_id, table.campaign_id, user, int(wall))
        said = fight_prompt.safe_lines(_table_lines(bot, table, time.monotonic()), secrets)
        try:
            reply = await bot.fight_ai.complete(
                fight_prompt.PROMPT,
                fight_prompt.build(sheet.sheet, round_number, said, moment=moment),
                max_tokens=MAX_TOKENS,
            )
        except AIError as exc:
            log.info("FightBot: no reminder this time (%s)", type(exc).__name__)
            continue
        text = fight_prompt.clean_reply(reply.text, fight_prompt.sheet_names(sheet.sheet))
        # (A failed or empty answer still uses up this change: no retry until something new.)
        if text is not None and await _may_send(bot, table, user, moment_due):
            first = user not in fight.introduced
            fight.introduced.add(user)
            await _send(bot, table, user, text, intro=first)


async def _may_send(bot: Any, table: Any, user_id: int, moment: Due | None = None) -> bool:
    """Checked again right before a message goes out: the AI call takes seconds, and the
    session may have ended, the table paused, the player withdrawn their consent, left the
    voice channel or silenced FightBot meanwhile."""
    if not (
        bot.tables.get(table.guild_id) is table
        and table.listening
        and bot.consent.has_consent(table.guild_id, user_id)
        and user_id in _present(bot, table)
        and (moment is None or table.fight.still_due(moment, time.monotonic()))
    ):
        return False  # (a moment that passed while the answer was written is not sent late)
    players = await bot.sheets.fight_players(table.guild_id, table.campaign_id)
    state = players.get(user_id, FightPlayer())
    now = time.time()
    # (The gap is not checked here: this very call has just recorded itself.)
    return not (
        state.never
        or state.quiet_since == table.started_at
        or (state.snoozed_until is not None and now < state.snoozed_until)
    )


def _table_lines(bot: Any, table: Any, now: float) -> list[tuple[str, str]]:
    """The last few things *said aloud* at the table, by people who still agree to be recorded
    and no longer ago than the sidebar's scene: the cleaned lines the shared transcript shows.
    Typed chat is raw text anyone can write anything in, so it is not used."""
    lines: list[tuple[str, str]] = []
    for at, speaker, text in list(table.recent.spoken)[-40:]:
        if now - at <= SCENE_SECONDS and bot.consent.has_consent(table.guild_id, speaker):
            lines.append((bot.name_of(table.guild_id, speaker), text))
    return lines


def buttons(guild_id: int, campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("snooze", "quiet", "never"):
        view.add_item(FightButton(guild_id, campaign_id, action))
    return view


async def _send(bot: Any, table: Any, user_id: int, text: str, *, intro: bool = False) -> None:
    # (the reminder's length is a target in the prompt; MAX_TOKENS keeps it far under Discord's limit)
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(
            f"⚔️ {discord.utils.escape_markdown(text)}\n{INTRO if intro else ''}{FOOTER}",
            view=buttons(table.guild_id, table.campaign_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as exc:  # their private messages may be closed
        log.info("FightBot: couldn't message user %s (%s)", user_id, type(exc).__name__)


_LABELS = {
    "snooze": ("Snooze 10 min", "😴", discord.ButtonStyle.secondary),
    "quiet": ("Quiet until next session", "🤫", discord.ButtonStyle.secondary),
    "never": ("Never", "🚫", discord.ButtonStyle.secondary),
}


class FightButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:fight:(?P<guild>\d{{1,20}}):{_ID}:(?P<do>snooze|quiet|never)",
):
    """One of a reminder's three silences. It only ever changes the presser's own settings, and
    only when they play a character in the campaign the button names (a button id is just text
    that anyone's client can send, so a row is never written for a stranger)."""

    def __init__(self, guild_id: int, campaign_id: str, action: str) -> None:
        label, emoji, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"dmbot:fight:{guild_id}:{campaign_id}:{action}",
            )
        )
        self.guild_id, self.campaign_id, self.action = guild_id, campaign_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FightButton:
        return cls(int(match["guild"]), match["campaign"], match["do"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot: Any = interaction.client
        user = interaction.user.id
        # Answer Discord first (#1798): the checks and the save are two or three database trips,
        # and a busy table must not make the button look broken. The message is changed after.
        await interaction.response.defer()
        # Only ever the presser's own settings, saved whether or not a session is running.
        try:
            found = await bot.sheets.sheets(self.guild_id, self.campaign_id)
            if not any(s.played_by == user for s in found):
                await interaction.followup.send(NOT_PLAYING, ephemeral=True)
                return
            if self.action == "never":
                await bot.sheets.set_fightbot(self.guild_id, self.campaign_id, user, False)
                said = NEVER
            elif self.action == "snooze":
                until = int(time.time() + SNOOZE_S)
                await bot.sheets.fight_snooze(self.guild_id, self.campaign_id, user, until)
                said = SNOOZED
            else:
                table = bot.tables.get(self.guild_id)
                if table is None or table.campaign_id != self.campaign_id:
                    said = NO_SESSION  # the next session starts fresh anyway
                else:
                    await bot.sheets.fight_quiet(
                        self.guild_id, self.campaign_id, user, table.started_at
                    )
                    said = QUIET
        except Exception:
            log.exception("FightBot: a silence button failed")
            await interaction.followup.send(NOT_SAVED, ephemeral=True)
            return
        text = interaction.message.content if interaction.message is not None else ""
        await interaction.edit_original_response(content=f"{text}\n\n{said}", view=None)
