"""How much DMbot says in the DM screen (docs/PLAN.md, "How much DMbot says"; #504).
Pure: no Discord, no database.

A campaign setting: **Quiet** (only what you ask for), **Normal** (the default:
questions and fixes, one at a time) or **Chatty** (also what it noticed). Every post that
a level can turn off (questions, fix notes, notices) asks `allows` first, with its kind.
Alerts (transcription stopped or working again, hours warnings) always post and don't
ask: the DM needs them to know the bot is working. So does "🙈 Left out as off-topic"
(#677): it's the DM's only chance to undo, and one message edited in place never pings.
Rules cards (#931) are not governed by the level: turning the setting on in ⚙️ Settings is
the DM's own choice, and it wins, even on Quiet (one card a minute at most).
The level is read when a session starts; ⚙️ Settings changes it mid-session too
(`DMBot.set_screen_level`, #515).
"""

from __future__ import annotations

from dmbot.campaigns.models import CHATTY, NORMAL, QUIET

QUESTION = "question"  # "Did they mean…?" and other questions during play
FIX_NOTE = "fix_note"  # ✏️ Name fixes to check (fixes DMbot isn't sure of, with Undo)
ALERT = "alert"  # something the DM must know: always shown
NOTICE = "notice"  # things DMbot noticed: chatty only (nothing uses it yet)
# A quiet reminder, not a warning (TimeBot's "where are we", #1738): unlike the rules cards it
# is governed by the level, because nothing is wrong if it is missed. None on Quiet, every
# half hour on Normal, every quarter hour on Chatty.
REMINDER = "reminder"
# A live heads-up (a character confirmed dead is voiced, #2068): none on Quiet, which means "only
# what you ask for" (docs/STORY_MEMORY.md, section 4).
WARNING = "warning"
REMINDER_GAP_S: dict[str, float | None] = {QUIET: None, NORMAL: 1800.0, CHATTY: 900.0}

_LOWEST = {
    ALERT: QUIET,
    QUESTION: NORMAL,
    FIX_NOTE: NORMAL,
    NOTICE: CHATTY,
    REMINDER: NORMAL,
    WARNING: NORMAL,
}


def reminder_gap_s(level: str) -> float | None:
    """Real seconds between reminders at this level, or None for no reminders. An unknown
    level counts as Normal, like `allows`."""
    return REMINDER_GAP_S.get(level, REMINDER_GAP_S[NORMAL])


_ORDER = (QUIET, NORMAL, CHATTY)


def allows(level: str, kind: str) -> bool:
    """Whether a DM screen at `level` shows this kind of post. An unknown level counts
    as normal; an unknown kind is never shown, so a new kind can't slip past quiet."""
    if kind not in _LOWEST:
        return False
    rank = _ORDER.index(level) if level in _ORDER else _ORDER.index(NORMAL)
    return rank >= _ORDER.index(_LOWEST[kind])
