"""Turns transcribed lines into Discord messages for the transcript channel. Pure: no
Discord, so the rules can be tested.

- Lines are grouped: Discord lets a bot post about 5 messages every 5 seconds in a
  channel, so lines collected over a couple of seconds go out as one message, never
  longer than Discord's 2,000 characters.
- Each line is `**Speaker:** text`; Discord shows the message's own time, so lines have
  none. Names and words are escaped, so speech can never format text or ping anyone
  (and messages are also sent with pings turned off).
- Only what's handed in is shown. Callers hand in only consenting speakers' lines, and
  `drop_speaker` throws away anything still waiting from someone who just stopped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MESSAGE_MAX = 2000  # Discord's limit for one message
LINE_MAX = 1800  # a single very long line is cut to this, leaving room around it

# Inside a line (which starts with the bold speaker name, so headings, quotes and lists
# can't start there), these are the characters that change how Discord shows text.
_MARKDOWN = re.compile(r"([\\*_~`|\[\]<>])")
_MENTION = re.compile(r"@(everyone|here)\b|<(@[!&]?|#|/)[^>]*>")


def _defuse(match: re.Match[str]) -> str:
    return match.group(0).replace("@", "@​").replace("<", "<​")


def escape(text: str) -> str:
    """Show text exactly as said: no bold, spoilers, links or mentions."""
    return _MARKDOWN.sub(r"\\\1", _MENTION.sub(_defuse, text))


def line(speaker: str, text: str) -> str:
    body = " ".join(text.split())
    if len(body) > LINE_MAX:
        body = body[: LINE_MAX - 1].rstrip() + "…"
    return f"**{escape(speaker)}:** {escape(body)}"


def started(campaign_name: str, when: int, *, resumed: bool = False) -> str:
    what = "🔴 Listening again after a restart" if resumed else "🔴 Session started"
    return f"── {what} · **{escape(campaign_name)}** · <t:{when}:f> ──"


def ended() -> str:
    return "── ⏹ Session ended ──"


@dataclass(slots=True)
class _Pending:
    speaker_id: int | None  # None for dividers
    text: str


class TranscriptStream:
    """Lines waiting to be posted to one session's transcript channel."""

    def __init__(self) -> None:
        self._pending: list[_Pending] = []

    def __len__(self) -> int:
        return len(self._pending)

    def add(self, speaker_id: int, speaker: str, text: str) -> None:
        if text.strip():
            self._pending.append(_Pending(speaker_id, line(speaker, text)))

    def add_divider(self, text: str) -> None:
        self._pending.append(_Pending(None, text))

    def drop_speaker(self, speaker_id: int) -> None:
        """They stopped recording: what they said and isn't posted yet is thrown away."""
        self._pending = [p for p in self._pending if p.speaker_id != speaker_id]

    def take(self) -> list[str]:
        """Everything waiting, as messages of at most MESSAGE_MAX characters."""
        messages: list[str] = []
        current = ""
        for pending in self._pending:
            if current and len(current) + 1 + len(pending.text) > MESSAGE_MAX:
                messages.append(current)
                current = ""
            current = f"{current}\n{pending.text}" if current else pending.text
        if current:
            messages.append(current)
        self._pending.clear()
        return messages
