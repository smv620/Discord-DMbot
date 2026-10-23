"""Everything the DM screen feature says to people. Plain words (CLAUDE.md, principle 0)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import discord

from dmbot import install
from dmbot.dm_screen.rules import Exposure
from dmbot.transcript.export import duration

# Shared by ⚙️ Settings and its hand-over buttons (a DM's screens), so both say the same.
# CAMPAIGN_GONE below is the players' version.
DM_CAMPAIGN_GONE = (
    "DMbot can't find this campaign anymore. It may have been deleted. To start a new one: "
    "`/dmbot start`."
)
LOAD_FAILED = "Sorry, something went wrong. Please try again."
PEEK_LABEL = "Peek behind the DM screen"
HIDE_LABEL = "Hide the DM screen from me"
YES_LABEL = "Yes, show me"
CANCEL_LABEL = "Cancel"
HELP_CARD_TITLE = "🛡️ **DM screen for "  # also used to find old cards to replace

# Buttons on the help card that let the DM change who can see the screen.
VISIBILITY_BUTTONS = {
    "private": ("🔒", "Only the DM"),
    "peek": ("👀", "Players can peek"),
    "open": ("📖", "Everyone in the server"),
}

WHO_CAN_SEE = {
    "private": "Only the DM.",
    "peek": "The DM. Players can choose to peek, after a spoiler warning.",
    "open": "Everyone in the server. They can read but not post.",
}

CAMPAIGN_GONE = "I can't find that campaign anymore. Ask your DM to set it up again."
SCREEN_GONE = "This campaign doesn't have a DM screen right now. Ask your DM to set it up."
PEEK_WARNING = (
    "**Peek behind the DM screen?**\n"
    "You'll see the DM's private notes, old ones too. They may spoil surprises.\n"
    "Your DM will see that you peeked. You can hide it again any time."
)


def peek_warning(rules_mention: str | None, npcs_mention: str | None = None) -> str:
    """The spoiler warning; names every channel a peek opens (docs/PLAN.md, #1752, #1761)."""
    named = (("House rules", rules_mention), ("People and groups", npcs_mention))
    opened = [f"• {what}: {mention}" for what, mention in named if mention is not None]
    if not opened:
        return PEEK_WARNING
    return (
        "**Peek behind the DM screen?**\n"
        "The DM screen shows the DM's private notes, old ones too. They may spoil surprises.\n"
        "This also lets you read:\n" + "\n".join(opened) + "\n"
        "Those only show what your DM has shared.\n"
        "Your DM will see that you peeked. You can hide it again any time."
    )


PEEK_CANCELLED = "OK, the DM screen stays hidden."
PEEK_EXPIRED = (
    "This expired. Press 👀 **Peek behind the DM screen** again if you still want to look."
)
PEEK_PRIVATE = "Your DM keeps this DM screen private."
ALREADY_PEEKING = "You're peeking at the DM screen right now. Want to hide it again?"
HIDE_DONE = "Done. The DM screen is hidden from you again."
HIDE_NOT_PEEKING = "You weren't peeking, so there's nothing to hide."
HIDE_IS_DM = "You're a DM of this campaign, so the DM screen stays visible to you."
HIDE_OPEN = "The DM screen is open to everyone, so it can't be hidden just for you."
PLAYER_FAILED = "Sorry, DMbot couldn't change the DM screen for you. Let your DM know."
STOP_LISTENING_LABEL = "Stop listening"
PAUSE_TABLE_LABEL = "Pause the table"  # with ⏸️: the DM's off the record (#2466)
NOT_LISTENING_NOW = (
    "DMbot isn't listening to this campaign right now. To start, run `/dmbot start`."
)
STOP_FAILED = "Something went wrong stopping DMbot. Try `/dmbot stop`."
# The Stop button asks first (#554): a thumb slip next to ⚙️ Settings mustn't end it.
STOP_YES_LABEL = "Yes, stop"
STOP_CANCEL_LABEL = "Cancel"
STOP_KEPT = "Still listening."
STOP_EXPIRED = "That's expired. Press ⏹ **Stop listening** again."
STOP_STALE = (
    "Nothing was stopped: that question was for an earlier session. To stop now, press "
    "⏹ **Stop listening** again."
)
STOPPING = "Stopping…"
ONLY_DM_STOPS = (
    "Only the DM can stop the session. To stop recording *you*, press **🗂️ Menu** in "
    "DMbot's private message, then **Stop recording me**, then **Yes**, or type "
    "`/consent revoke`."
)
STOP_CONFIRM_S = 60.0


def stop_question(campaign_name: str) -> str:
    """The Stop button's question (the name already escaped; it may be empty)."""
    if not campaign_name:
        return "Stop listening and end this session?"
    return f"Stop listening and end the session for **{campaign_name}**?"


NOT_THE_DM = "Only this campaign's DM (or a server manager) can change who can see the DM screen."
SOMETHING_WENT_WRONG = "Sorry, something went wrong changing the DM screen. Please try again."


def lost_access(channel_id: int) -> str:
    return (
        f"I can't see this campaign's DM screen (<#{channel_id}>) anymore. Give DMbot "
        "**View Channel** there again (Edit Channel → Permissions), or delete that channel "
        "and I'll make a new one."
    )


FORBIDDEN_HERE = (
    "Discord blocked me from setting up the DM screen here. This channel's category may "
    "not let DMbot manage channels. Try running the command from a channel outside that "
    "category, or ask a server admin to check the category's permissions for DMbot."
)
# Fixed text: DMbot finds its earlier pin notes by matching it exactly.
CANT_PIN = (
    "📌 I couldn't pin the 🛡️ DM screen card, so it may scroll out of sight. "
    "Ask a server admin to turn on **Pin Messages** for DMbot "
    "(Server Settings → Roles → DMbot). I'll pin it next time you run `/dmbot start`."
)


def needs_permissions(missing: list[str]) -> str:
    return (
        f"I need {install.human_list(missing)} to set up the DM screen. "
        f"{install.fix_hint(install.install_link())} Then run `/dmbot start` again."
    )


def discord_error(reason: str) -> str:
    return (
        f"Discord didn't let me set up the DM screen ({reason}). Try again in a minute. "
        "If it keeps happening, check the server or category isn't full of channels."
    )


def topic(campaign_name: str, visibility: str) -> str:
    who = {
        "private": "Only the DM can see this.",
        "peek": "Hidden from players unless they choose to peek. It may spoil the game.",
        "open": "Everyone in this server can see this. It may spoil surprises.",
    }[visibility]
    return f"DM screen for {campaign_name}. {who}"


def help_card(campaign_name: str, visibility: str, dm_bot_channel_id: int | None = None) -> str:
    lines = [
        f"{HELP_CARD_TITLE}{campaign_name}**",
        "DMbot writes notes for the DM here. It never makes rulings or story. "
        "The DM decides everything.",
        f"**Who can see this:** {WHO_CAN_SEE[visibility]}",
        "**To start:** `/dmbot start`. **To stop:** `/dmbot stop`, or ⏹ **Stop listening** "
        "on the newest ✅ Listening message.",
    ]
    if visibility != "open":
        lines.append("Server owners and admins can always see every channel.")
    # Settings and options live in the DM Bot channel (#1666); /dmbot settings still works.
    where = f"in <#{dm_bot_channel_id}>" if dm_bot_channel_id else "with `/dmbot settings`"
    lines.append(
        f"**DM:** who can see this and how much DMbot says are in ⚙️ **Settings** (under 👤 "
        f"**DMbot Main Menu**), {where}."
    )
    if visibility == "peek":
        lines.append(f"**Peeking?** Press 🙈 **{HIDE_LABEL}** below to stop.")
    return "\n".join(lines)


def level_changed(level: str) -> str:
    """In the DM screen when someone changes how much DMbot says (#553)."""
    icon = "🔇" if level == "quiet" else "🔔"
    return f"{icon} How much DMbot says: {level.capitalize()}."


def peekers_lose_access(visibility: str, *, was: str) -> bool:
    """Players who were peeking can't see the screen anymore."""
    return was == "peek" and visibility == "private"


def visibility_changed(visibility: str, *, was: str) -> str:
    text = f"Done. **Who can see the DM screen:** {WHO_CAN_SEE[visibility]}"
    if peekers_lose_access(visibility, was=was):
        text += " Players who were peeking can't see it anymore."
    return text


def peek_invite() -> str:
    return (
        "👀 **Curious what the DM sees?** You can peek behind the DM screen. "
        "It may spoil surprises."
    )


def peek_done(channel_mention: str) -> str:
    return (
        f"You can now see {channel_mention}. To hide it again, press **{PEEK_LABEL}** "
        f"again in the voice chat, or **{HIDE_LABEL}** below."
    )


def peek_is_dm(channel_mention: str) -> str:
    return f"You're the DM, so you can already see {channel_mention}."


def peek_open(channel_mention: str) -> str:
    return f"The DM screen is open to everyone: {channel_mention}."


def peeked_note(name: str) -> str:
    return f"👀 {name} peeked behind the DM screen."


def unpeeked_note(name: str) -> str:
    return f"🙈 {name} stopped peeking."


def exposure_warning(exposure: Exposure) -> str | None:
    """A warning for the DM, or None if nobody unexpected can see the screen."""
    if not exposure:
        return None
    who: list[str] = []
    if exposure.everyone:
        who.append("everyone in the server")
    who += [f"<@&{role_id}>" for role_id in exposure.roles]
    who += [f"<@{user_id}>" for user_id in exposure.members]
    return (
        f"⚠️ These can see this DM screen: {', '.join(who)}. To hide it from them: "
        "Edit Channel → Permissions → pick each one → set **View Channel** to ✗."
    )


# ---- the live transcript channel (#124) --------------------------------------------

TRANSCRIPT_CARD_TITLE = "📜 **Live transcript for "  # also used to find old cards
TRANSCRIPT_FORBIDDEN = (
    "Discord won't let DMbot make or change channels here. Ask a server admin to give "
    "DMbot **Manage Channels** and **Manage Roles** in this category."
)
TRANSCRIPT_NO_ANSWER = "Discord didn't answer. This is often temporary."


def transcript_discord_error(detail: str) -> str:
    return f"Discord said: {detail}. A category can hold only 50 channels, so check it isn't full."


def transcript_topic(campaign_name: str) -> str:
    return (
        f"Live transcript for {campaign_name}. Anyone in this server can read it; only "
        "DMbot posts here. Only people who said yes to recording are recorded."
    )


def transcript_card(campaign_name: str) -> str:
    return "\n".join(
        [
            f"{TRANSCRIPT_CARD_TITLE}{campaign_name}**",
            "Anyone in this server can read this. Only DMbot posts here, not even the DM.",
            "While DMbot is listening, what people say shows up here a few seconds later. "
            "Chat that's clearly not about the game may be shortened to a note like \"[8s of "
            'off-topic chat skipped]"; every word is still saved in the "As heard" transcript.',
            "**Who is recorded:** only people who said yes in DMbot's private message. "
            "Changed your mind? Press **🗂️ Menu** in that message, then **Stop recording me**, "
            "then **Yes**, "
            "or type `/consent revoke` here or in any channel. Only you see the answer. "
            "What's already here stays.",
            "DMbot's notes for the DM never appear here.",
            "To download a session as a text file, type `/transcript`.",
            "No pop-ups from here. To hide the unread dot too, mute this channel "
            "(right-click it, or long-press on a phone).",
        ]
    )


TRANSCRIPT_NOT_SAVED = (
    "⚠️ **DMbot can't save this session's transcript right now.** It keeps trying, so "
    "nothing is lost yet. The live transcript and these notes still work."
)
TRANSCRIPT_END_LOST = (
    "⚠️ DMbot couldn't save the last part of this session's transcript, so the download "
    "is missing it. The live transcript channel still has it."
)


def transcript_failed(cause: str) -> str:
    """For the DM screen: setup failed. The session goes on without it."""
    return (
        f"⚠️ **No live transcript this session.** {cause} Listening and these notes still "
        "work. To try again: fix that, then `/dmbot stop` and `/dmbot start`."
    )


def transcript_stopped(channel_id: int) -> str:
    """For the DM screen: DMbot lost the channel mid-session."""
    return (
        f"⚠️ **The live transcript stopped:** DMbot can't post in <#{channel_id}> any "
        "more. Give DMbot **Send Messages** there, or delete that channel and DMbot makes "
        "a new one at the next `/dmbot start`."
    )


# ---- end-of-session summary (#109) ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spoke:
    name: str  # display name, or a <@id> mention if DMbot can't look it up
    seconds: float  # how long they spoke
    percent: int | None  # how much of their speech got through; None if not measured
    flagged: bool  # warned about during the session (#699): only then "cut out at times"


def _who(name: str) -> str:
    """A mention as-is (it shows the person, and pings are off); any other name shown
    exactly as written."""
    if re.fullmatch(r"<@!?[0-9]+>", name):
        return name
    return discord.utils.escape_markdown(name)


def session_summary(
    campaign_name: str,
    started_at: int,
    ended_at: int,
    spoke: list[Spoke],
    problems: list[str],
    *,
    downloads_sent: int,
    names: str = "",
) -> str:
    """One plain summary for the DM screen when a session ends. Names and numbers only:
    never anything that was said. `names`: the one line of counts from `names_line`."""
    lines = [
        f"📋 **Session ended: {discord.utils.escape_markdown(campaign_name)}**",
        f"Started <t:{started_at}:t>, ran {duration(ended_at - started_at)}.",
    ]
    people = sorted(spoke, key=lambda p: -p.seconds)
    if people:
        who = ", ".join(f"{_who(p.name)} {duration(int(p.seconds))}" for p in people)
        lines.append(f"🎙 **Who spoke:** {who}.")
    else:
        lines.append(
            "🎙 Nobody was recorded. DMbot only records people who said yes, and only when "
            "they speak."
        )
    for p in people:
        if p.flagged:
            lines.append(
                f"⚠️ {_who(p.name)}'s voice cut out at times, so some of their words may be "
                "missing from the transcript."
            )
    lines.extend(f"⚠️ {problem}" for problem in problems)
    if problems:
        lines.append("If this happens often, tell whoever runs DMbot for your server.")
    elif people and not any(p.flagged for p in people):  # no all-clear under a ⚠️
        lines.append("✅ Everything DMbot heard was written down.")
    if names:
        lines.append(names)
    if downloads_sent:
        lines.append(
            "📄 DMbot sent each recorded player a private message to download the "
            "transcript. Anyone in the server can also get it with `/transcript`."
        )
    elif people:
        lines.append("📄 Anyone in the server can get the transcript with `/transcript`.")
    return "\n".join(lines)


def names_line(
    fixed: int, asked: int, unanswered: int, edited: int, suggested: int, waiting: int
) -> str:
    """One sentence of counts on what happened with names (#1510), so a DM can tell whether it is
    worth a look: "Names: DMbot fixed 3 by itself, asked you about 2 (1 not answered), you
    corrected 1, players suggested 2 (1 still waiting)." Plain verbs say who did what; only the
    parts above zero; empty when there is nothing to say."""
    parts = []
    if fixed:
        parts.append(f"DMbot fixed {fixed} by itself")
    if asked:
        parts.append(
            f"asked you about {asked}" + (f" ({unanswered} not answered)" if unanswered else "")
        )
    if edited:
        parts.append(f"you corrected {edited}")
    if suggested:
        parts.append(
            f"players suggested {suggested}" + (f" ({waiting} still waiting)" if waiting else "")
        )
    return f"✏️ Names: {', '.join(parts)}." if parts else ""


def summary_problems(missed: int, failed: int, caught_up: bool) -> list[str]:
    problems = []
    if missed:
        bits = "1 bit" if missed == 1 else f"{missed} bits"
        problems.append(
            f"DMbot fell behind and missed {bits} of speech. They aren't in the transcript."
        )
    if failed:
        times = "once" if failed == 1 else f"{failed} times"
        problems.append(f"DMbot couldn't write down speech {times}, so the transcript has gaps.")
    if not caught_up:
        problems.append(
            "The last few words before the stop may not be in the transcript: DMbot waited "
            "for them, then gave up."
        )
    return problems


# ---- who is being recorded (#107) -------------------------------------------------------


def _names(names: list[str]) -> str:
    shown = [_who(n) for n in names]
    return ", ".join(shown[:-1]) + f" and {shown[-1]}" if len(shown) > 1 else shown[0]


def listening_message(
    voice_channel_id: int, campaign_name: str, recorded: list[str], waiting: list[str]
) -> str:
    """The DM screen's "listening" message: who in the voice channel is recorded now.
    `recorded`: people there who said yes; `waiting`: people there who haven't (DMbot is
    asking them privately). A line follows for each answer and each join."""
    campaign = f" for **{discord.utils.escape_markdown(campaign_name)}**" if campaign_name else ""
    lines = [f"✅ Listening in <#{voice_channel_id}>{campaign}."]
    if recorded:
        lines.append(f"🎙 Recording: {_names(recorded)}.")
    if waiting:
        lines.append(
            f"✉️ Not recorded yet: {_names(waiting)}. DMbot is asking privately; each answer "
            "shows up here."
        )
    if not recorded and not waiting:
        lines.append(
            "Nobody is in the voice channel yet. DMbot asks each person privately as they join."
        )
    return "\n".join(lines)


def agreed_message(name: str) -> str:
    return f"🎙 **{_who(name)}** said yes: DMbot is recording them now."


def stopped_message(name: str) -> str:
    return f"🛑 **{_who(name)}** said stop. DMbot no longer records them."


def joined_recorded_message(name: str) -> str:
    return f"🎙 **{_who(name)}** joined and is recorded (they said yes before)."


def voice_lost_message(name: str) -> str:
    """ears couldn't hear someone (#631). No promise of when it hears them again: after
    giving up it waits for their next speech, while the watchdog keeps listening (#645)."""
    return (
        f"⚠️ DMbot missed some of **{_who(name)}**'s words just now: their voice cut out. "
        "If this happens again, ask them to leave the voice channel and join again."
    )


def joined_not_recorded_message(name: str) -> str:
    return f"✉️ **{_who(name)}** joined. Not recorded unless they say yes."


# A test server (#1019): said once at /dmbot start, in the DM screen.
TEST_SESSION = (
    '🧪 Test session. Only players who chose "Save my voice for tests" have their voice kept. '
    "Everyone else is still written down as usual. Players can stop any time in 🗂️ Menu."
)


# ---- the players' game-time channel (#1116) ----------------------------------------

GAMETIME_FAILED = (
    "⚠️ **Couldn't make the game-time channel** ({cause}). The game goes on; players just "
    "have no clock channel yet. To retry, fix that and run `/dmbot start`."
)


def gametime_failed(cause: str) -> str:
    return GAMETIME_FAILED.format(cause=cause)


def gametime_topic(campaign_name: str) -> str:
    return f"{campaign_name}: the day and a rough time of day. Posted by DMbot, view only."


# ---- the DM Bot channel (#1666) ---------------------------------------------------------

DM_BOT_FAILED = (
    "⚠️ **Couldn't make the DM Bot channel** ({cause}). The game goes on. For settings, type "
    "`/dmbot settings`. To retry: fix that, then run `/dmbot start`."
)


def dm_bot_failed(cause: str) -> str:
    return DM_BOT_FAILED.format(cause=cause)


def dm_bot_topic(campaign_name: str) -> str:
    return f"{campaign_name}: your buttons for settings and help. Only you see the answers."


# ---- the table's DMbot chat channel (#1187) -------------------------------------------

DMBCHAT_FAILED = (
    "⚠️ **Couldn't make the DMbot chat channel** ({cause}). The game goes on; players can still "
    'say "Hey DMbot" at the table. To retry, fix that and run `/dmbot start`.'
)

# Plain words for the channel's description (CLAUDE.md, "Simple enough for a child").
RULES_TOPIC = "Your table's house rules. You can read but not post. Your DM decides what applies."


def rules_card(campaign_name: str) -> str:
    return (
        "📖 **What's this channel?**\n"
        f"The house rules for **{campaign_name}**, saved by your DM. Only DMbot posts here, so "
        "you can read but not write. Your DM decides what applies. If a rule isn't clear, ask "
        "your DM."
    )


NPCS_TOPIC = "People and groups your DM has shared. Read only."


def npcs_card(campaign_name: str) -> str:
    name = discord.utils.escape_markdown(campaign_name)
    return (
        "📖 **What's this channel?**\n"
        f"The people and groups DMbot remembers for **{name}**, as your DM confirmed them. "
        "Only DMbot posts here, so you can read but not write. Nothing secret is shown. "
        "If something looks wrong, ask your DM."
    )


def npcs_failed(cause: str) -> str:
    return (
        f"⚠️ DMbot couldn't set up the people list channel. {cause} The rest of DMbot still works."
    )


def rules_failed(cause: str) -> str:
    return f"⚠️ DMbot couldn't set up the house-rules channel. {cause}"


DMBCHAT_TOPIC = "Ask DMbot a quick rules question. Your DM has the final say."


def dmbchat_failed(cause: str) -> str:
    return DMBCHAT_FAILED.format(cause=cause)
