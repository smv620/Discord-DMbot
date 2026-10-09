"""Turns transcribed lines into Discord messages for the transcript channel. Pure: no
Discord, so the rules can be tested.

- Lines are grouped: Discord lets a bot post about 5 messages every 5 seconds in a
  channel, so lines collected over a couple of seconds go out as one message, never
  longer than Discord's 2,000 characters.
- Lines wait in order of when the speech *started*, so a quick "Yes!" doesn't jump ahead
  of the long question it answered.
- Each line is `**Speaker:** text`; Discord shows the message's own time, so lines have
  none. Names and words are escaped, so speech can never format text, ping anyone or
  make a link (and messages are also sent with pings and link previews turned off).
- A line leaves the queue only once it's posted (`next_message`, then `posted`), and
  every line is checked against `allowed` as the next message is built: someone who
  presses Stop never has another word posted, even mid-flush.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

MESSAGE_MAX = 2000  # Discord's limit for one message
LINE_MAX = 1800  # one line, after escaping, leaving room for the speaker's name
NAME_MAX = 80
MAX_WAITING = 300  # lines kept while posting fails; the oldest go first
EDIT_WINDOW_S = 30.0  # a posted message can still be edited for a late fix this long
UNKNOWN_SPEAKER = "Someone"

# Inside a line (which starts with the bold speaker name, so headings, quotes and lists
# can't start there), these are the characters that change how Discord shows text.
# Escaping < and > also stops role, channel and command mentions, timestamps and custom
# emoji; @everyone and @here need their own fix.
_MARKDOWN = re.compile(r"([\\*_~`|\[\]<>])")
_EVERYONE = re.compile(r"@(everyone|here)\b")
_LINK = re.compile(r"://")


def escape(text: str) -> str:
    """Show text exactly as said: no bold, spoilers, links or mentions."""
    text = _EVERYONE.sub(lambda m: "@​" + m.group(1), text)
    text = _LINK.sub(":​//", text)  # not clickable
    return _MARKDOWN.sub(r"\\\1", text)


def _cut(escaped: str, limit: int) -> str:
    """Shorten escaped text without leaving half an escape at the end."""
    if len(escaped) <= limit:
        return escaped
    cut = escaped[: limit - 1]
    trailing = len(cut) - len(cut.rstrip("\\"))
    if trailing % 2:  # a lone backslash would escape the "…"
        cut = cut[:-1]
    return cut.rstrip() + "…"


def speaker_name(raw: str | None) -> str:
    """A display name fit for the transcript: no invisible direction or format
    characters (which can flip how a line reads), never empty."""
    name = "".join(c for c in (raw or "") if unicodedata.category(c) != "Cf")
    name = " ".join(name.split())
    return name[:NAME_MAX] or UNKNOWN_SPEAKER


def line(speaker: str, text: str) -> str:
    body = _cut(escape(" ".join(text.split())), LINE_MAX)
    return f"**{_cut(escape(speaker_name(speaker)), NAME_MAX * 2)}:** {body}"


def started(campaign_name: str, when: int, *, resumed: bool = False) -> str:
    name = _cut(escape(campaign_name), NAME_MAX * 2)
    if resumed:
        return (
            f"── 🔴 Back after a short break (DMbot restarted) · **{name}** · <t:{when}:f> ──\n"
            "Anything said during the break wasn't written down."
        )
    return f"── 🔴 Session started · **{name}** · <t:{when}:f> ──"


def ended(campaign_name: str) -> str:
    return f"── ⏹ Session ended · **{_cut(escape(campaign_name), NAME_MAX * 2)}** ──"


@dataclass(order=True, slots=True)
class _Waiting:
    started_ms: int
    seq: int  # keeps arrival order among lines that started together
    speaker_id: int | None = field(compare=False)  # None for dividers
    text: str = field(compare=False)
    speaker: str = field(default="", compare=False)  # the name shown, to write it again


class Editable(Protocol):
    """A posted message that can be edited (a Discord message)."""

    async def edit(self, *, content: str, allowed_mentions: Any = ...) -> Any: ...


@dataclass(slots=True)
class _Posted:
    """A message already in the channel, kept a little while for late fixes."""

    ref: Editable  # the Discord message
    at: float  # monotonic seconds
    items: list[_Waiting]


Allowed = Callable[[int], bool]


class TranscriptStream:
    """Lines waiting to be posted to one session's transcript channel."""

    def __init__(self) -> None:
        self._waiting: list[_Waiting] = []
        self._seq = 0
        self.dropped = 0  # lines thrown away because posting kept failing
        self._recent: list[_Posted] = []  # posted in the last EDIT_WINDOW_S
        self._built: list[_Waiting] = []  # the lines in the message `next_message` built

    def __len__(self) -> int:
        return len(self._waiting)

    def _insert(self, item: _Waiting) -> None:
        bisect.insort(self._waiting, item)
        if len(self._waiting) > MAX_WAITING:
            del self._waiting[0]
            self.dropped += 1

    def add(self, speaker_id: int, speaker: str, text: str, started_ms: int) -> None:
        if text.strip():
            self._seq += 1
            self._insert(_Waiting(started_ms, self._seq, speaker_id, line(speaker, text), speaker))

    def add_divider(self, text: str, at_ms: int) -> None:
        self._seq += 1
        self._insert(_Waiting(at_ms, self._seq, None, text))

    def drop_speaker(self, speaker_id: int) -> None:
        """They stopped recording: what they said and isn't posted yet is thrown away."""
        self._waiting = [w for w in self._waiting if w.speaker_id != speaker_id]

    def clear(self) -> None:
        self._waiting.clear()

    def next_message(self, allowed: Allowed) -> tuple[str, int] | None:
        """The next message to post and how many waiting lines it holds, or None.

        Lines from speakers no longer `allowed` are thrown away first. Nothing leaves
        the queue until `posted` is called, so a failed post is tried again.
        """
        self._waiting = [w for w in self._waiting if w.speaker_id is None or allowed(w.speaker_id)]
        text = ""
        count = 0
        for w in self._waiting:
            joined = f"{text}\n{w.text}" if text else w.text
            if text and len(joined) > MESSAGE_MAX:
                break
            text, count = joined, count + 1
        if not count:
            return None
        self._built = self._waiting[:count]
        return _cut(text, MESSAGE_MAX), count

    def posted(self, count: int, ref: Editable | None = None, *, now: float) -> None:
        """The message `next_message` built went out (`ref`, kept for EDIT_WINDOW_S so a
        late fix can edit it). Exactly those lines leave the queue: a line that started
        earlier may have been queued ahead of them while the message was being sent."""
        built = {id(w) for w in self._built[:count]}
        items = [w for w in self._waiting if id(w) in built]
        self._waiting = [w for w in self._waiting if id(w) not in built]
        self._built = []
        self._recent = [p for p in self._recent if now - p.at <= EDIT_WINDOW_S]
        if ref is not None:
            self._recent.append(_Posted(ref, now, items))

    def can_change(self, speaker_id: int, started_ms: int, now: float) -> bool:
        """The line is still waiting, or was posted in the last EDIT_WINDOW_S: `relabel`
        can change it in the channel (#677)."""
        if any(w.speaker_id == speaker_id and w.started_ms == started_ms for w in self._waiting):
            return True
        return any(
            w.speaker_id == speaker_id and w.started_ms == started_ms
            for posted in self._recent
            if now - posted.at <= EDIT_WINDOW_S
            for w in posted.items
        )

    def relabel(
        self, speaker_id: int, started_ms: int, text: str, now: float
    ) -> tuple[Editable, str] | None:
        """A line's words changed after it was queued (an Undo, #296). Still waiting: it
        goes out with the new words. Posted in the last EDIT_WINDOW_S: the message and
        its new text, to edit it. Older: None (too late for the channel)."""

        def same(w: _Waiting) -> bool:
            return w.speaker_id == speaker_id and w.started_ms == started_ms

        for w in self._waiting:
            if same(w):
                w.text = line(w.speaker, text)
                return None
        for posted in self._recent:
            if now - posted.at > EDIT_WINDOW_S:
                continue
            for w in posted.items:
                if same(w):
                    w.text = line(w.speaker, text)
                    joined = "\n".join(item.text for item in posted.items)
                    return posted.ref, _cut(joined, MESSAGE_MAX)
        return None
