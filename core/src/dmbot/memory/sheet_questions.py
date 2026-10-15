"""When DMbot asks a player about their D&D Beyond sheet, and how rarely (#1128; docs/PLAN.md,
"Re-reading D&D Beyond"). Pure: no Discord, no network.

Questions must be rare, because the game has to stay quick (Supervisor's rule, 2026-10-13):

- at most **one message per player every 15 minutes**, with every question waiting for that
  player merged into it;
- **none during combat**: they are held, and sent after the fight;
- **never twice** about the same value while the sheet still shows the same number;
- a question whose sheet has changed since (the player already updated it) is dropped.

One `Questions` per running session, so nothing is shared between campaigns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

QUESTION_GAP_S = 15 * 60.0
MAX_QUESTIONS = 3  # in one message: a long list is a chore, not a courtesy


@dataclass(frozen=True, slots=True)
class Waiting:
    entity_id: str
    key: tuple[Any, ...]  # what was said: ("damage", 8, None)
    text: str
    shown: tuple[Any, ...]  # what the sheet showed when it was read
    speaker: int  # who said it, so it goes if they stop being recorded
    url: str


@dataclass(slots=True)
class Questions:
    waiting: dict[int, list[Waiting]] = field(default_factory=dict)
    last_asked: dict[int, float] = field(default_factory=dict)
    asked: dict[tuple[str, tuple[Any, ...]], tuple[Any, ...]] = field(default_factory=dict)
    cards: dict[int, Mapping[str, Any]] = field(default_factory=dict)

    def add(self, player: int, item: Waiting) -> bool:
        """Hold a question for `player`. False if it was already asked about this very value,
        or is already waiting."""
        if self.asked.get((item.entity_id, item.key)) == item.shown:
            return False
        held = self.waiting.setdefault(player, [])
        if any(w.entity_id == item.entity_id and w.key == item.key for w in held):
            return False
        held.append(item)
        return True

    def hold_card(self, player: int, snapshot: Mapping[str, Any]) -> None:
        """The newest status card to send when it is quiet (a part went ✅ to ⚠️ or ⛔)."""
        self.cards[player] = snapshot

    def take_card(self, player: int) -> Mapping[str, Any] | None:
        return self.cards.pop(player, None)

    def drop_speaker(self, user_id: int) -> None:
        """Someone stopped being recorded: questions about what they said go."""
        for player, held in list(self.waiting.items()):
            kept = [w for w in held if w.speaker != user_id]
            if kept:
                self.waiting[player] = kept
            else:
                del self.waiting[player]

    def any_waiting(self) -> bool:
        return bool(self.waiting or self.cards)

    def due(self, player: int, now: float) -> bool:
        """Is there anything for this player that may go now: a card, or questions once 15
        minutes have passed since the last message? (No database read needed to know.)"""
        if player in self.cards:
            return True
        if not self.waiting.get(player):
            return False
        return now - self.last_asked.get(player, -1e9) >= QUESTION_GAP_S

    def ready(
        self,
        player: int,
        now: float,
        *,
        shown_now: Callable[[str], tuple[Any, ...] | None],
    ) -> list[Waiting]:
        """The questions to send this player now, or none: not within 15 minutes of the last,
        and only those the sheet still doesn't answer. (Combat is the caller's check.)
        Sending marks them asked."""
        held = self.waiting.get(player)
        if not held:
            return []
        if now - self.last_asked.get(player, -1e9) < QUESTION_GAP_S:
            return []
        still = [w for w in held if shown_now(w.entity_id) == w.shown]
        send, rest = still[:MAX_QUESTIONS], still[MAX_QUESTIONS:]
        if rest:
            self.waiting[player] = rest
        else:
            self.waiting.pop(player, None)
        if not send:
            return []
        self.last_asked[player] = now
        for w in send:
            self.asked[(w.entity_id, w.key)] = w.shown
        return send
