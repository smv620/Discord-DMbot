"""Speech-to-text hints that follow the scene (docs/PLAN.md, "Names at scale and hints
that follow the scene"; #126, #127). Pure: no Discord, no database, no AI.

Every written-down line (after its consent check) is matched against the campaign's
names, so DMbot knows which names were said lately and by whom. The hints sent with
each clip then go, most useful first:

1. **always:** the players' characters, then the players' display names;
2. **the scene:** confirmed names said in about the last 10 minutes, the most recent and
   most often first;
3. **the tip of the tongue:** confirmed names the DM connected to the scene's names
   (confirmed, non-secret connections), ranked by how strongly the scene points at
   them (each scene name pointing counts, weighted by how recently it was said): talk
   of the Frostwolf tribe hints its chief before anyone says his name;
4. **recently:** names said earlier this session, then in the last session or two, then
   names never said yet (newest first: a prepared NPC list helps from the start);
5. **fill:** the other confirmed names, most said first (names unsaid for about six
   months drop out until they're said again), then guesses (names DMbot only
   suggested, and suggested other names of known entries): a guess never pushes out a
   real name.

How often and when names were said comes from the stored mentions, written once at the
end of each session.

Never a secret name, and a match on a secret name adds nothing: saying "the hooded
stranger" must not pull in the real name or anything connected to it, not even through
a shorter name hiding inside the secret one.

The line matcher (`mentions`) is the piece the Transcript Cleaner reuses: groups of 1 to
4 words, matched to confirmed names and other names and the DM's "fixed" spellings,
skipping "keep as heard"; no sound-alikes (they'd match almost anything).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, name_key

SCENE_WINDOW_S = 600.0  # a name said once fades out of the scene after this long
LONGEST_NAME_WORDS = 4  # "Caer Dineval", "the Frostwolf tribe"
NAMES_PER_ENTRY = 3  # an entry's own name and up to two other names
MAX_TIMES_KEPT = 20  # per entry: plenty to rank a scene, bounded in a long session
LONG_UNSAID_S = 180 * 86400  # names unsaid for about six months drop out of hints
MAX_HINTS = 50  # Deepgram's limit (deepgram.MAX_KEYTERMS); local Whisper cuts by length
PLAYER_CHARACTER = "player_character"

_WORD = re.compile(r"[^\W_](?:[^\W_]|['’-](?=[^\W_]))*")


@dataclass(frozen=True, slots=True)
class Found:
    entity_id: str
    span: tuple[int, int]  # characters in the line
    method: str  # "exact": a name or other name; "spelling": a spelling the DM fixed


def find_mentions(lookup: CampaignLookup, text: str) -> list[Found]:
    """Confirmed entries named in a line, where, and how: their confirmed names and
    other names, or a spelling the DM said means them. A secret name (and any shorter
    name inside it), a name DMbot only suggested, and words the DM said to keep as heard
    don't count. One find per entry per place."""
    words = list(_WORD.finditer(text))
    keys = [name_key(w.group()) for w in words]  # once per word, not per group
    n = len(keys)
    groups = [
        (start, end, " ".join(keys[start:end]))
        for start in range(n)
        for end in range(start + 1, min(n, start + LONGEST_NAME_WORDS) + 1)
    ]
    hidden = [False] * n  # words inside a secret name
    for start, end, key in groups:
        if any(e.secret for e in lookup.by_key.get(key, ())):
            hidden[start:end] = [True] * (end - start)
    found: dict[tuple[str, int, int], Found] = {}
    for start, end, key in groups:
        if any(hidden[start:end]) or key in lookup.keep_keys:
            continue
        span = (words[start].start(), words[end - 1].end())
        hits = [
            (e.entity_id, "exact")
            for e in lookup.by_key.get(key, ())
            if e.confirmed and not e.secret
        ]
        hits += [(entity_id, "spelling") for entity_id in lookup.fixes.get(key, ())]
        for entity_id, method in hits:
            entity = lookup.entities.get(entity_id)
            if entity is not None and entity.status == CONFIRMED:
                found.setdefault((entity_id, *span), Found(entity_id, span, method))
    return list(found.values())


def mentions(lookup: CampaignLookup, text: str) -> set[str]:
    """The entries named in a line (see `find_mentions`)."""
    return {f.entity_id for f in find_mentions(lookup, text)}


@dataclass(frozen=True, slots=True)
class HintParts:
    """The parts of the hints that change only when the names change: worked out once
    per change (off the event loop), not per clip."""

    version: int
    names: dict[str, tuple[str, ...]]  # confirmed entry → its confirmed names, own first
    characters: tuple[str, ...]
    recent: tuple[str, ...]  # said in the last session or two, then never said yet
    fill: tuple[str, ...]  # the rest, most said first; long-unsaid ones left out
    guesses: tuple[str, ...]  # suggested names, never secret


def prepare(lookup: CampaignLookup, now: float) -> HintParts:
    """`now`: Unix seconds, to leave out names unsaid for months."""
    entities = lookup.entities
    own = {e.id: name_key(e.name) for e in entities.values()}
    names: dict[str, list[str]] = {}
    guesses: list[str] = []
    for entry in lookup.names:
        if entry.secret:
            continue
        entity = entities.get(entry.entity_id)
        if entry.confirmed and entity is not None and entity.status == CONFIRMED:
            names.setdefault(entry.entity_id, []).append(entry.text)
        else:
            guesses.append(entry.text)
    ordered: dict[str, tuple[str, ...]] = {}
    for entity_id, texts in names.items():
        mine = own.get(entity_id, "")
        ordered[entity_id] = tuple(sorted(texts, key=lambda t: name_key(t) != mine))
    confirmed = [e for e in entities.values() if e.status == CONFIRMED]
    by_name = sorted(confirmed, key=lambda e: (own[e.id], e.id))
    heard = lookup.heard
    since = min(lookup.recent_sessions) if lookup.recent_sessions else None

    def last(entity_id: str) -> int:
        return heard[entity_id].last_session_at or 0

    recently = sorted(
        (e for e in confirmed if since is not None and e.id in heard and last(e.id) >= since),
        key=lambda e: (-last(e.id), -heard[e.id].times, own[e.id]),
    )
    # Names never said yet (just added, or imported before the first session) count as
    # recent, newest first, so a prepared NPC list helps from the first session.
    never = sorted(
        (e for e in confirmed if e.id not in heard), key=lambda e: (-e.created_at, own[e.id])
    )
    taken = {e.id for e in recently} | {e.id for e in never}
    fill = sorted(
        (e for e in confirmed if e.id not in taken and last(e.id) >= now - LONG_UNSAID_S),
        key=lambda e: (-heard[e.id].times, own[e.id]),
    )
    return HintParts(
        version=lookup.version,
        names=ordered,
        characters=tuple(e.id for e in by_name if e.type == PLAYER_CHARACTER),
        recent=tuple(e.id for e in [*recently, *never]),
        fill=tuple(e.id for e in fill),
        guesses=tuple(sorted(guesses, key=name_key)),
    )


@dataclass(slots=True)
class SceneTracker:
    """Which entries were said this session, when (monotonic seconds) and by whom. Lives
    with the running session, in memory, and goes when it ends."""

    window_s: float = SCENE_WINDOW_S
    said: dict[str, list[tuple[float, int]]] = field(default_factory=dict)

    def note(self, entity_ids: Iterable[str], speaker: int, now: float) -> None:
        for entity_id in entity_ids:
            times = self.said.setdefault(entity_id, [])
            times.append((now, speaker))
            if len(times) > MAX_TIMES_KEPT:
                del times[0]

    def note_line(self, lookup: CampaignLookup, text: str, speaker: int, now: float) -> None:
        self.note(mentions(lookup, text), speaker, now)

    def forget_speaker(self, speaker: int) -> None:
        """They stopped being recorded: their lines stop counting."""
        for entity_id in list(self.said):
            times = [t for t in self.said[entity_id] if t[1] != speaker]
            if times:
                self.said[entity_id] = times
            else:
                del self.said[entity_id]

    def scene(self, now: float) -> dict[str, float]:
        """Entries in the scene and how strongly: each mention counts, more the more
        recent it was (1 just now, fading to 0 after the window). So a name said once
        is gone after 10 minutes, and one stays longer only by being said again."""
        scores: dict[str, float] = {}
        for entity_id, times in self.said.items():
            if now - times[-1][0] >= self.window_s:
                continue  # newest is too old, so all are
            ages = (now - t for t, _ in times)
            scores[entity_id] = sum(1 - age / self.window_s for age in ages if age < self.window_s)
        return scores

    def earlier(self, now: float) -> list[str]:
        """Entries said this session but not lately, most recent first."""
        old = [
            (times[-1][0], entity_id)
            for entity_id, times in self.said.items()
            if now - times[-1][0] >= self.window_s
        ]
        return [entity_id for _, entity_id in sorted(old, reverse=True)]


def scene_hints(
    lookup: CampaignLookup,
    parts: HintParts,
    tracker: SceneTracker,
    now: float,
    *,
    people: Iterable[str] = (),
    limit: int = MAX_HINTS,
) -> list[str]:
    """The hints for the next clip, most useful first, one per name however it's
    written, never a secret name. `parts`: `prepare(lookup)`, made once per change."""
    out: dict[str, str] = {}

    def entries(entity_ids: Iterable[str]) -> Iterator[str]:
        for entity_id in entity_ids:
            yield from parts.names.get(entity_id, ())[:NAMES_PER_ENTRY]

    scene = tracker.scene(now)
    in_scene = sorted(scene, key=lambda e: (-scene[e], e))
    pull: dict[str, float] = {}
    for entity_id, score in scene.items():
        for other in lookup.linked(entity_id):
            if other not in scene and other in parts.names:  # confirmed entries only
                pull[other] = pull.get(other, 0.0) + score
    connected = sorted(pull, key=lambda e: (-pull[e], e))
    tiers: list[Iterable[str]] = [
        entries(parts.characters),
        people,
        entries(in_scene),
        entries(connected),
        entries(tracker.earlier(now)),
        entries(parts.recent),
        entries(parts.fill),
        parts.guesses,
    ]
    for tier in tiers:
        for text in tier:
            if len(out) >= limit:
                return list(out.values())
            out.setdefault(name_key(text), text)
    return list(out.values())
