"""Speech-to-text hints that follow the scene (docs/PLAN.md, "Names at scale and hints
that follow the scene"; #126, #127). Pure: no Discord, no database, no AI.

Every written-down line (after its consent check) is matched against the campaign's
names, so DMbot knows which names were said lately and by whom. The hints sent with
each clip then go, most useful first:

1. **always:** the players' characters, then the players' display names;
2. **the scene:** confirmed names said in about the last 10 minutes, the most recent and
   most often first;
3. **the tip of the tongue:** names the DM connected to the scene's names (confirmed,
   non-secret connections), ranked by how strongly the scene points at them: talk of
   the Frostwolf tribe hints its chief before anyone says his name;
4. **recently:** names said earlier this session (names from the last sessions come with
   the stored mentions, next);
5. **fill:** the other confirmed names, then names DMbot only suggested.

Never a secret name, and a match on a secret name adds nothing: saying "the hooded
stranger" must not pull in the real name, or anything connected to it.

The line matcher (`mentions`) is the piece the Transcript Cleaner reuses: groups of 1 to
4 words, matched to exact names, other names and the DM's "fixed" spellings, skipping
"keep as heard"; no sound-alikes (they'd match almost anything).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, name_key

SCENE_WINDOW_S = 600.0  # a name said once fades out of the scene after this long
LONGEST_NAME_WORDS = 4  # "Caer Dineval", "the Frostwolf tribe"
NAMES_PER_ENTRY = 3  # an entry's own name and up to two other names
MAX_TIMES_KEPT = 50  # per entry: plenty to rank a scene, bounded in a long session
MAX_HINTS = 50  # Deepgram's limit (deepgram.MAX_KEYTERMS); local Whisper cuts by length
PLAYER_CHARACTER = "player_character"

_WORD = re.compile(r"[^\W_](?:[^\W_]|['’-](?=[^\W_]))*")


def mentions(lookup: CampaignLookup, text: str) -> set[str]:
    """Confirmed entries named in a line, spelled as the campaign knows them: their
    names, other names, or a spelling the DM said means them. A secret name, a name
    DMbot only suggested, and words the DM said to keep as heard don't count."""
    words = _WORD.findall(text)
    found: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + LONGEST_NAME_WORDS) + 1):
            phrase = " ".join(words[start:end])
            if lookup.keep_as_heard(phrase):
                continue
            for entry in lookup.exact(phrase):
                if not entry.secret:
                    found.add(entry.entity_id)
            found.update(lookup.fixes_for(phrase))
    return {e for e in found if (ent := lookup.entities.get(e)) and ent.status == CONFIRMED}


@dataclass(slots=True)
class _Said:
    times: list[tuple[float, int]] = field(default_factory=list)  # (when, speaker)


@dataclass(slots=True)
class _Prepared:
    """The parts of the hints that change only when the names change."""

    version: int
    texts: dict[str, list[str]]  # entry → its non-secret names, own name first
    characters: list[str]
    confirmed: list[str]
    suggested: list[str]


@dataclass(slots=True)
class SceneTracker:
    """Which entries were said this session, when (monotonic seconds) and by whom. Lives
    with the running session, in memory, and goes when it ends."""

    window_s: float = SCENE_WINDOW_S
    said: dict[str, _Said] = field(default_factory=dict)
    _prepared: _Prepared | None = None

    def note(self, entity_ids: Iterable[str], speaker: int, now: float) -> None:
        for entity_id in entity_ids:
            times = self.said.setdefault(entity_id, _Said()).times
            times.append((now, speaker))
            del times[:-MAX_TIMES_KEPT]

    def note_line(self, lookup: CampaignLookup, text: str, speaker: int, now: float) -> None:
        self.note(mentions(lookup, text), speaker, now)

    def forget_speaker(self, speaker: int) -> None:
        """They stopped being recorded: their lines stop counting."""
        for entity_id in list(self.said):
            times = [t for t in self.said[entity_id].times if t[1] != speaker]
            if times:
                self.said[entity_id].times = times
            else:
                del self.said[entity_id]

    def scene(self, now: float) -> dict[str, float]:
        """Entries in the scene and how strongly: each mention counts, more the more
        recent it was (1 just now, fading to 0 after the window). So a name said once
        is gone after 10 minutes, and one stays longer only by being said again."""
        scores: dict[str, float] = {}
        for entity_id, said in self.said.items():
            score = sum(
                1 - (now - t) / self.window_s for t, _ in said.times if now - t < self.window_s
            )
            if score > 0:
                scores[entity_id] = score
        return scores

    def earlier(self, now: float) -> list[str]:
        """Entries said this session but not lately, most recent first."""
        old = [
            (said.times[-1][0], entity_id)
            for entity_id, said in self.said.items()
            if said.times and now - said.times[-1][0] >= self.window_s
        ]
        return [entity_id for _, entity_id in sorted(old, reverse=True)]

    def prepared(self, lookup: CampaignLookup) -> _Prepared:
        if self._prepared is None or self._prepared.version != lookup.version:
            self._prepared = _prepare(lookup)
        return self._prepared


def _prepare(lookup: CampaignLookup) -> _Prepared:
    texts: dict[str, list[str]] = {}
    for entry in lookup.names:
        if not entry.secret and entry.confirmed:
            texts.setdefault(entry.entity_id, []).append(entry.text)
    for entry in lookup.names:  # suggested names last, for entries that only have those
        if not entry.secret and not entry.confirmed:
            texts.setdefault(entry.entity_id, []).append(entry.text)
    for entity_id, names in texts.items():  # the entry's own name first
        own = lookup.entities[entity_id].name if entity_id in lookup.entities else ""
        names.sort(key=lambda n: name_key(n) != name_key(own))
    by_name = sorted(lookup.entities.values(), key=lambda e: (name_key(e.name), e.id))
    confirmed = [e.id for e in by_name if e.status == CONFIRMED]
    return _Prepared(
        version=lookup.version,
        texts=texts,
        characters=[e.id for e in by_name if e.type == PLAYER_CHARACTER and e.status == CONFIRMED],
        confirmed=confirmed,
        suggested=[e.id for e in by_name if e.status != CONFIRMED],
    )


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
    ready = tracker.prepared(lookup)
    out: dict[str, str] = {}

    def add(texts: Iterable[str]) -> bool:
        for text in texts:
            out.setdefault(name_key(text), text)
            if len(out) >= limit:
                return True
        return False

    def entries(entity_ids: Iterable[str]) -> Iterable[str]:
        for entity_id in entity_ids:
            yield from ready.texts.get(entity_id, [])[:NAMES_PER_ENTRY]

    scene = tracker.scene(now)
    in_scene = sorted(scene, key=lambda e: (-scene[e], e))
    pull: dict[str, float] = {}
    for entity_id, score in scene.items():
        for other in lookup.linked(entity_id):
            if other not in scene:
                pull[other] = pull.get(other, 0.0) + score
    connected = sorted(pull, key=lambda e: (-pull[e], e))
    # Each tier stops early once the list is full.
    _ = (
        add(entries(ready.characters))
        or add(people)
        or add(entries(in_scene))
        or add(entries(connected))
        or add(entries(tracker.earlier(now)))
        or add(entries(ready.confirmed))
        or add(entries(ready.suggested))
    )
    return list(out.values())
