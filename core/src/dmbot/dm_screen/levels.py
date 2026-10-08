"""How much DMbot says in the DM screen (docs/PLAN.md, "How much DMbot says"; #504).
Pure: no Discord, no database.

A campaign setting: **Quiet** (only what you ask for), **Normal** (the default:
questions and fixes, one at a time) or **Chatty** (also what it noticed). Every post that
a level can turn off (questions, fix notes, notices) asks `allows` first, with its kind.
Alerts (transcription stopped or working again, hours warnings) always post and don't
ask: the DM needs them to know the bot is working. The level is read when a session
starts; a later way to change it mid-session must update the running table too.
"""

from __future__ import annotations

from dmbot.campaigns.models import CHATTY, NORMAL, QUIET

QUESTION = "question"  # "Did they mean…?" and other questions during play
FIX_NOTE = "fix_note"  # ✏️ Name fixes to check (fixes DMbot isn't sure of, with Undo)
ALERT = "alert"  # something the DM must know: always shown
NOTICE = "notice"  # things DMbot noticed: chatty only (nothing uses it yet)

_LOWEST = {ALERT: QUIET, QUESTION: NORMAL, FIX_NOTE: NORMAL, NOTICE: CHATTY}
_ORDER = (QUIET, NORMAL, CHATTY)


def allows(level: str, kind: str) -> bool:
    """Whether a DM screen at `level` shows this kind of post. An unknown level counts
    as normal; an unknown kind is never shown, so a new kind can't slip past quiet."""
    if kind not in _LOWEST:
        return False
    rank = _ORDER.index(level) if level in _ORDER else _ORDER.index(NORMAL)
    return rank >= _ORDER.index(_LOWEST[kind])
