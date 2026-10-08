"""Counts captured speech per interval: who spoke, how much, and audio health (frames
received vs expected).

Every interval goes to the log as one line of IDs and numbers (`log_line`). The DM
screen only hears about it when audio went missing (`render`): what was said goes to
the transcript channel (#124), never here, and a screen full of "all fine" checks hid
the notes that need the DM (#134).
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import MIN_UTTERANCE_S

# Below this percentage of expected frames, audio is flagged (logs, test scoring).
HEALTH_WARN_PERCENT = 95
# The DM screen's ⚠️ means "DMbot is missing what people say" (#671), so it counts only
# speech worth writing down, over enough of it to matter: per person, over the last
# DM_WINDOW_S, under HEALTH_WARN_PERCENT got through AND at least DM_WARN_LOST_S lost. A
# piece shorter than gets transcribed (a blip, a cough) counts in neither column; a short
# answer ("yes", 0.3-0.6 s) counts, so one patchy "yes" never alarms but answers that keep
# breaking up add up. A TV's patchy bursts stay quiet through the 2 s floor. The window
# is timed from when health arrives: if transcription falls a minute behind, older gaps
# leave it before their speech is checked. Then once per person, and again only if it gets
# clearly worse or after a while: a phone on bad Wi-Fi mustn't bury the notes that need
# the DM.
# (A speaker whose audio is lost entirely, #631, is warned at once by ears' own path.)
DM_WINDOW_S = 60.0
DM_WARN_LOST_S = 2.0
FRAMES_PER_S = 50  # ears' health counts 20 ms frames (ears/src/health.ts FRAME_MS)
DM_WARN_LOST_FRAMES = int(DM_WARN_LOST_S * FRAMES_PER_S)
MIN_FRAMES = math.ceil(MIN_UTTERANCE_S * FRAMES_PER_S)  # long enough to be written down
# #699: the audio rule above no longer warns by itself; it starts a check of that
# speaker's lines (dmbot.audio_check). A loss this large warns at once, with no check.
LARGE_LOSS_PERCENT = 50
LARGE_LOSS_FRAMES = 10 * FRAMES_PER_S
WARN_AGAIN_DROP = 10
WARN_AGAIN_AFTER_S = 600.0
# Audio health whose speech hasn't arrived is kept for this many checks after the latest
# report (about a minute at one check every 15 s), then dropped: that speech isn't coming
# (the person opted out, or the speech queue was full), and old gaps mustn't land in a
# later, clean check. A speaker whose health keeps coming but whose speech doesn't (a
# long overload) is dropped this many checks after their first report, however recent.
HEALTH_WAIT_CHECKS = 4
HEALTH_WAIT_MAX_CHECKS = 2 * HEALTH_WAIT_CHECKS


def audio_health(received: int, expected: int) -> tuple[int, bool]:
    """(percent to show, whether to flag gaps) for `expected` > 0.

    Uses whole-number maths and rounds down, so a flagged result never shows as 95%
    or more (#44: 94.6% used to read "audio 95% ⚠️ audio gaps"). Received is capped at
    expected: ears caps each utterance (#36), and the sum here must stay capped too.
    """
    received = max(0, min(received, expected))
    flagged = received * 100 < HEALTH_WARN_PERCENT * expected
    return received * 100 // expected, flagged


@dataclass(slots=True)
class _SpeakerStats:
    utterances: int = 0
    seconds: float = 0.0
    frames_received: int = 0
    frames_expected: int = 0
    checks_waited: int = 0  # checks since the latest health report, without speech
    checks_since_first: int = 0  # checks since the first one, without speech

    @property
    def covered(self) -> bool:
        """Whether a check reports (and then resets) this speaker: once speech arrived."""
        return self.utterances > 0


@dataclass(frozen=True, slots=True)
class Due:
    """A speaker whose audio rule fired, for the check (#699)."""

    user_id: int
    percent: int  # got through, over the window
    lost: int  # frames lost, over the window
    large: bool  # so large it warns without a check
    lines: tuple[tuple[str, float | None], ...]  # their lines in the window, with confidence


def counts_for_dm(expected: int) -> bool:
    """Whether a piece's health counts towards the DM screen's warning (#671): the piece
    is long enough to be written down, however much of it was lost."""
    return expected >= MIN_FRAMES


class CaptureLog:
    def __init__(self) -> None:
        self._stats: dict[int, _SpeakerStats] = {}
        self._warned: dict[int, tuple[int, float]] = {}  # user → (percent, when)
        # Per person, the pieces that count for the DM: (when, received, expected).
        self._window: dict[int, deque[tuple[float, int, int]]] = {}
        # Per person, their transcript lines: (when, text, confidence) (#699).
        self._lines: dict[int, deque[tuple[float, str, float | None]]] = {}

    def _get(self, user_id: int) -> _SpeakerStats:
        return self._stats.setdefault(user_id, _SpeakerStats())

    def add_utterance(self, utterance: Utterance) -> None:
        stats = self._get(utterance.user_id)
        stats.utterances += 1
        stats.seconds += utterance.duration_s

    def add_health(
        self, user_id: int, received: int, expected: int, now: float | None = None
    ) -> None:
        """One piece's audio health. `now`: a monotonic time in seconds (default: now)."""
        stats = self._get(user_id)
        # Cap each report, so one over-counted clip can't hide a gap in another.
        received = max(0, min(received, expected))
        stats.frames_received += received
        stats.frames_expected += max(0, expected)
        stats.checks_waited = 0  # the wait for speech counts from the latest report
        at = time.monotonic() if now is None else now
        if counts_for_dm(expected):
            self._window.setdefault(user_id, deque()).append((at, received, expected))
        self._prune(user_id, at)

    def _prune(self, user_id: int, now: float) -> None:
        window = self._window.get(user_id)
        while window and window[0][0] < now - DM_WINDOW_S:
            window.popleft()
        if not window:
            self._window.pop(user_id, None)
        lines = self._lines.get(user_id)
        while lines and lines[0][0] < now - DM_WINDOW_S:
            lines.popleft()
        if not lines:
            self._lines.pop(user_id, None)

    def _dm_health(self, user_id: int, now: float) -> tuple[int, int] | None:
        """(percent got through, frames lost) over the last DM_WINDOW_S of the pieces
        that count, or None if there are none."""
        self._prune(user_id, now)
        window = self._window.get(user_id)
        if not window:
            return None
        received = sum(r for _, r, _ in window)
        expected = sum(e for _, _, e in window)
        percent, _ = audio_health(received, expected)
        return percent, expected - received

    def log_line(self) -> str | None:
        """One line for the terminal log: user IDs and numbers only, never names or
        words (#37). Call before render(), which resets the speakers it covers. None if
        nothing was captured."""
        parts: list[str] = []
        for user_id, s in sorted(self._stats.items()):
            if not s.covered:
                continue
            part = f"user {user_id}: {s.utterances} x speech, {s.seconds:.1f} s"
            if s.frames_expected > 0:
                percent, flagged = audio_health(s.frames_received, s.frames_expected)
                part += f", audio {percent}%{' (audio gaps)' if flagged else ''}"
            parts.append(part)
        if not parts:
            return None
        return f"Capture check: {len(parts)} speaker(s); " + "; ".join(parts)

    def add_line(
        self, user_id: int, text: str, confidence: float | None, now: float | None = None
    ) -> None:
        """A line of the transcript, as shown, with the engine's confidence if it gave one:
        what the check reads when the audio rule fires (#699). Kept for DM_WINDOW_S."""
        at = time.monotonic() if now is None else now
        self._lines.setdefault(user_id, deque()).append((at, text, confidence))
        self._prune(user_id, at)

    def forget(self, user_id: int) -> None:
        """They stopped being recorded: drop their counts and lines now (consent)."""
        self._stats.pop(user_id, None)
        self._window.pop(user_id, None)
        self._lines.pop(user_id, None)
        self._warned.pop(user_id, None)

    def due(self, now: float | None = None) -> list[Due]:
        """The speakers this check covers whose audio rule fires and who may be warned
        (not told already at this level), the most talkative first; then reset the
        speakers it covered. `now`: a monotonic time in seconds.

        A speaker whose audio health has arrived but whose speech hasn't (it's still
        being transcribed) isn't covered yet: their counts are kept for the next check
        (#120): HEALTH_WAIT_CHECKS checks after their latest report, and no more than
        HEALTH_WAIT_MAX_CHECKS after their first. Health and speech are paired per
        speaker, not per piece of speech, so a check's % can include a piece that is
        still being transcribed; every count is still reported exactly once."""
        now = time.monotonic() if now is None else now
        for user_id in {*self._window, *self._lines}:  # people gone quiet keep nothing old
            self._prune(user_id, now)
        found: list[Due] = []
        # sorted() copies, so entries can be deleted inside the loop.
        for user_id, s in sorted(self._stats.items(), key=lambda kv: -kv[1].seconds):
            if not s.covered:
                s.checks_waited += 1
                s.checks_since_first += 1
                if (
                    s.checks_waited > HEALTH_WAIT_CHECKS
                    or s.checks_since_first > HEALTH_WAIT_MAX_CHECKS
                ):
                    del self._stats[user_id]
                    self._window.pop(user_id, None)  # that speech isn't coming either
                continue
            del self._stats[user_id]
            health = self._dm_health(user_id, now)
            if health is None:
                continue
            percent, lost = health
            if percent >= HEALTH_WARN_PERCENT or lost < DM_WARN_LOST_FRAMES:
                continue
            last = self._warned.get(user_id)
            if not (
                last is None
                or percent <= last[0] - WARN_AGAIN_DROP
                or now - last[1] >= WARN_AGAIN_AFTER_S
            ):
                continue  # told already at this level: no check needed either
            large = percent < LARGE_LOSS_PERCENT and lost >= LARGE_LOSS_FRAMES
            lines = tuple((text, conf) for _, text, conf in self._lines.get(user_id, ()))
            found.append(Due(user_id, percent, lost, large, lines))
        return found

    def warn(
        self, name_of: Callable[[int], str], confirmed: list[Due], now: float | None = None
    ) -> str | None:
        """The DM screen's warning for the speakers confirmed (the large rule, or the
        check found their lines garbled), and remember it, so the same level isn't
        repeated. None if there are none."""
        now = time.monotonic() if now is None else now
        for d in confirmed:
            self._warned[d.user_id] = (d.percent, now)
        if not confirmed:
            return None
        tail = (
            "Some of their words may be missing from the transcript. If it keeps up, ask "
            "them to check their internet or rejoin voice. DMbot only mentions it again "
            "if it gets worse."
        )
        if len(confirmed) == 1:
            d = confirmed[0]
            return (
                f"⚠️ **{name_of(d.user_id)}'s voice is cutting out for DMbot** "
                f"({d.percent}% got through). {tail}"
            )
        who = ", ".join(f"{name_of(d.user_id)} {d.percent}%" for d in confirmed)
        return f"⚠️ **Voices cutting out for DMbot:** {who}. {tail}"

    def render(
        self,
        name_of: Callable[[int], str],
        now: float | None = None,
        confirm: Callable[[Due], bool] = lambda due: True,
    ) -> str | None:
        """For tests and tools: `due` then `warn`, with a synchronous `confirm` standing in
        for the check (default: the audio rule alone, the old behaviour). The bot runs the
        real check, which awaits (bot.post_summary)."""
        now = time.monotonic() if now is None else now
        dues = self.due(now)
        return self.warn(name_of, [d for d in dues if d.large or confirm(d)], now)


@dataclass(slots=True)
class SpeakerTotal:
    seconds: float = 0.0
    counted_received: int = 0  # pieces that count for the DM (#671)
    counted_expected: int = 0

    @property
    def percent(self) -> int | None:
        """How much of their speech got through, over the pieces that count for the DM,
        or None if nothing was measured."""
        if self.counted_expected == 0:
            return None
        return audio_health(self.counted_received, self.counted_expected)[0]


class SessionTotals:
    """How much each person spoke in the whole session, and how much of their audio got
    through, for the end-of-session summary (#109). Never reset; numbers only."""

    def __init__(self) -> None:
        self.speakers: dict[int, SpeakerTotal] = {}
        # People the DM was warned about this session (#699: the check found their lines
        # garbled, or the loss was large): only they "kept cutting out" in the summary.
        self.flagged: set[int] = set()

    def add_utterance(self, utterance: Utterance) -> None:
        total = self.speakers.setdefault(utterance.user_id, SpeakerTotal())
        total.seconds += utterance.duration_s

    def add_health(self, user_id: int, received: int, expected: int) -> None:
        total = self.speakers.setdefault(user_id, SpeakerTotal())
        received = max(0, min(received, expected))
        if counts_for_dm(expected):
            total.counted_received += received
            total.counted_expected += max(0, expected)
