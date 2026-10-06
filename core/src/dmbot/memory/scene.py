"""Speech-to-text hints that follow the scene (docs/PLAN.md, "Names at scale and hints
that follow the scene"; #126, #127). Pure: no Discord, no database, no AI.

Every written-down line is matched against the campaign's names, so DMbot knows which
names were said lately. The hints sent with each clip then go, most useful first:

1. the players' characters;
2. **the scene:** names said in about the last 10 minutes, the most recent and most
   often first;
3. **the tip of the tongue:** names the DM linked to the scene's names, ranked by how
   strongly the scene points at them (talk of the Frostwolf tribe hints its chief);
4. the people at the table (display names: few, and said all the time);
5. names said earlier this session;
6. everything else: confirmed names, then names DMbot only suggested.

Never a secret name: an outside service mustn't be nudged towards a hidden identity, and
saying a secret name ("the hooded stranger") doesn't put the real one in the scene.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, name_key

SCENE_WINDOW_S = 600.0  # a name fades out of the scene after this long unsaid
LONGEST_NAME_WORDS = 4  # "Caer Dineval", "the Frostwolf tribe"
NAMES_PER_ENTRY = 3  # an entry's name and up to two other names
MAX_TIMES_KEPT = 50  # per entry: plenty to rank a scene
MAX_HINTS = 50  # Deepgram's limit (deepgram.MAX_KEYTERMS)
PLAYER_CHARACTER = "player_character"

_WORD = re.compile(r"[^\W_](?:[^\W_]|['’-](?=[^\W_]))*")


def mentions(lookup: CampaignLookup, text: str) -> set[str]:
    """Entries named in a line, spelled as the campaign knows them. A secret name
    doesn't count, nor does a name DMbot only suggested."""
    words = _WORD.findall(text)
    found: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + LONGEST_NAME_WORDS) + 1):
            for entry in lookup.exact(" ".join(words[start:end])):
                entity = lookup.entities.get(entry.entity_id)
                if not entry.secret and entity is not None and entity.status == CONFIRMED:
                    found.add(entry.entity_id)
    return found


@dataclass(slots=True)
class SceneTracker:
    """When each entry was said this session (monotonic seconds), newest last."""

    window_s: float = SCENE_WINDOW_S
    said: dict[str, list[float]] = field(default_factory=dict)

    def note(self, entity_ids: Iterable[str], now: float) -> None:
        for entity_id in entity_ids:
            times = self.said.setdefault(entity_id, [])
            times.append(now)
            del times[:-MAX_TIMES_KEPT]  # a long session can't grow this without end

    def note_line(self, lookup: CampaignLookup, text: str, now: float) -> None:
        self.note(mentions(lookup, text), now)

    def scene(self, now: float) -> dict[str, float]:
        """Entries in the scene and how strongly: each time said counts, more the more
        recent it was (1 just now, fading to 0 at the window's edge)."""
        scores: dict[str, float] = {}
        for entity_id, times in self.said.items():
            score = sum(1 - (now - t) / self.window_s for t in times if now - t < self.window_s)
            if score > 0:
                scores[entity_id] = score
        return scores

    def earlier(self, now: float) -> list[str]:
        """Entries said this session but not lately, most recent first."""
        old = [(times[-1], e) for e, times in self.said.items() if now - times[-1] >= self.window_s]
        return [e for _, e in sorted(old, reverse=True)]


def scene_hints(
    lookup: CampaignLookup,
    tracker: SceneTracker,
    now: float,
    *,
    people: Iterable[str] = (),
    limit: int = MAX_HINTS,
) -> list[str]:
    """The hints for the next clip, most useful first, one per name however it's
    written, never a secret name."""
    texts: dict[str, list[str]] = {}  # entry → its non-secret names, confirmed first
    for entry in sorted(lookup.names, key=lambda e: not e.confirmed):
        if not entry.secret:
            texts.setdefault(entry.entity_id, []).append(entry.text)

    out: dict[str, str] = {}

    def add(text: str) -> bool:
        out.setdefault(name_key(text), text)
        return len(out) >= limit

    def add_entries(entity_ids: Iterable[str]) -> bool:
        for entity_id in entity_ids:
            for text in texts.get(entity_id, [])[:NAMES_PER_ENTRY]:
                if add(text):
                    return True
        return False

    entities = lookup.entities
    characters = sorted(
        (e for e in entities.values() if e.type == PLAYER_CHARACTER and e.status == CONFIRMED),
        key=lambda e: e.name,
    )
    scene = tracker.scene(now)
    in_scene = sorted(scene, key=lambda e: (-scene[e], entities[e].name if e in entities else e))
    pull: dict[str, float] = {}
    for entity_id, score in scene.items():
        for other in lookup.linked(entity_id):
            if other not in scene and other in entities:
                pull[other] = pull.get(other, 0.0) + score
    linked = sorted(pull, key=lambda e: (-pull[e], entities[e].name))
    rest = sorted(
        (e for e in entities.values() if e.id not in scene and e.id not in pull),
        key=lambda e: (e.status != CONFIRMED, e.name),
    )
    # Each step stops early once the list is full.
    _ = (
        add_entries(e.id for e in characters)
        or add_entries(in_scene)
        or add_entries(linked)
        or any(add(p) for p in people)
        or add_entries(tracker.earlier(now))
        or add_entries(e.id for e in rest)
    )
    return list(out.values())
