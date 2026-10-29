"""A small invented session and canned AI answers for the recap tests (#1382). Made-up names
only; no AI call anywhere."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from typing import Any

from dmbot.ai import Reply
from dmbot.recaps import draft as rd
from dmbot.transcript.models import Line

DM, ALICE, BOB = 7, 8, 9
NAMES = ["Mira", "Oskar", "Greywater Keep"]

LINES = [
    (DM, "The party reaches Greywater Keep at dusk and the gate stands open."),
    (ALICE, "Mira draws her blade and steps inside."),
    (BOB, "Oskar lights a torch and follows Mira."),
    (DM, "The hall is empty and a cold wind blows through it."),
    (ALICE, "Mira finds a locked door with a silver lock."),
    (BOB, "Oskar picks the silver lock and it clicks open."),
    (DM, "Behind the door a stair descends into darkness and something breathes below."),
]


def lines() -> list[Line]:
    return [Line(n * 1000, user, text, text) for n, (user, text) in enumerate(LINES, start=1)]


BEATS = {
    "beats": [
        {
            "what": "The party reached Greywater Keep at dusk",
            "who": [],
            "lines": [1],
            "kind": "event",
        },
        {
            "what": "Mira drew her blade and went in",
            "who": ["Mira"],
            "lines": [2],
            "kind": "event",
        },
        {
            "what": "Oskar lit a torch and followed Mira",
            "who": ["Oskar", "Mira"],
            "lines": [3],
            "kind": "event",
        },
        {
            "what": "Mira found a locked door with a silver lock",
            "who": ["Mira"],
            "lines": [5],
            "kind": "event",
        },
        {
            "what": "Oskar picked the lock and something breathed in the dark below",
            "who": ["Oskar"],
            "lines": [6, 7],
            "kind": "moment",
        },
    ]
}
BEATS_ANSWER = json.dumps(BEATS)

_PARAS = [
    "《Tense》 ((pause)) Dusk settled over the road as Mira and Oskar came to Greywater Keep, and "
    "the great gate stood open with no guard to challenge them. The party had travelled for "
    "days, and every one of them felt the quiet press against their shoulders. Mira drew her "
    "blade without a word, and Oskar kindled a torch whose small flame shook in the cold air.",
    "They stepped inside together. The hall beyond was bare and wide, its high windows dark, "
    "its stones scattered with old dust that no boot had disturbed for a long time. Somewhere "
    "above, a loose shutter knocked against its frame, and each knock made the two of them "
    "stop and listen before going on. Nothing answered. No voice called out. The silence "
    "seemed to be waiting for them to make a mistake.",
    "《Ominous》 At the far end they found a heavy door bound with a silver lock, bright and "
    "clean in a place where everything else had gone grey. Mira studied it for a long moment, "
    "certain that it did not belong there, and said nothing of her doubts. Oskar knelt, set "
    "his torch against the wall and worked at the lock with patient fingers while the others "
    "watched the shadows behind them.",
    "The lock gave way with a soft click that sounded far too loud. Oskar drew back and the "
    "door swung inward on a stair that fell away into darkness. Cold air rose from below, "
    "carrying a smell of wet stone, and under it came a slow and steady sound, as of "
    "something large breathing in its sleep.",
    "Time passed, though none of them could have said how much. The torch burned down a little. "
    "Mira counted her breaths to keep her hands from shaking, and Oskar muttered a prayer so "
    "quietly that it was barely a prayer at all. Still the stair waited, black and patient, "
    "and still the sound rose from it, unhurried and deep.",
    "Mira lifted her blade. Oskar raised the torch higher. Neither of them moved, and the "
    "sound below went on, patient and unhurried, as though it had known all along that someone "
    "would come.",
]
SUMMARY = "\n\n".join(_PARAS)
MUSIC = (
    "1. Low strings and a distant wind, slow and sparse, quiet under a spoken voice.\n"
    "2. Soft, uneasy drones with faint wind ambience, sitting low under a spoken voice."
)


def prose_answer(summary: str, music: str = MUSIC) -> str:
    """A prose answer as the writer sends it now (#2039): the JSON of sections, built from the
    text form the tests write (`《mood》` cues in the summary, numbered music lines)."""
    prompts = tuple(
        re.sub(r"^\s*\d+[.)]\s*", "", m).strip() for m in music.splitlines() if m.strip()
    )
    return rd.prose_json(rd.Prose(summary, prompts))


def finish_answer(title: str = "The Silver Lock", *bullets: tuple[int, str]) -> str:
    return json.dumps(
        {"title": title, "housekeeping": [{"beat": b, "text": t} for b, t in bullets]},
        ensure_ascii=False,
    )


PROSE_ANSWER = prose_answer(SUMMARY)
FINISH_ANSWER = finish_answer("The Silver Lock", (2, "Mira drew her blade."))
GRADE_ANSWER = '{"in_world":5,"no_quotes":5,"tension":4,"ending":4,"cues":4}'
WEAK_GRADE = '{"in_world":5,"no_quotes":5,"tension":2,"ending":4,"cues":4}'


CLEAN_FACTS = '{"unsupported":[],"missing":[]}'


def facts_answer(unsupported: Sequence[tuple[int, str]] = (), missing: Sequence[int] = ()) -> str:
    return json.dumps(
        {
            "unsupported": [{"sentence": n, "kind": k} for n, k in unsupported],
            "missing": list(missing),
        }
    )


class Canned:
    """An AI that returns the answers it was given, in order, and records what it was sent."""

    def __init__(
        self, *answers: str | Exception | Reply, facts: Sequence[str | Reply] = ()
    ) -> None:
        self.answers = list(answers)
        # The fact check (#2064) is a call of its own: it takes the next of `facts`, or finds
        # nothing wrong, and is kept apart from `sent` so the tests that count the writer's calls
        # need not know about it.
        self.facts = list(facts)
        self.fact_sent: list[str] = []
        self.fact_tokens: list[int] = []  # the room each fact check was given (#2345)
        self.tokens: list[int] = []  # the room each call was given
        self.sent: list[str] = []
        self.systems: list[str] = []
        self.schemas: list[
            dict[str, Any] | None
        ] = []  # what each call asked the answer's shape to be

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = 0,
        schema: dict[str, Any] | None = None,
    ) -> Reply:
        if system in (rd.FACTS_SYSTEM, rd.FACTS_SYSTEM_TRANSCRIPT):
            self.fact_sent.append(text)
            self.fact_tokens.append(max_tokens)
            given = self.facts.pop(0) if self.facts else CLEAN_FACTS
            return given if isinstance(given, Reply) else Reply(given, False, 10, 10, "m")
        self.sent.append(text)
        self.systems.append(system)
        self.schemas.append(schema)
        self.tokens.append(max_tokens)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, Reply):
            return answer
        return Reply(answer, False, 10, 10, "m")


async def everyone() -> Collection[int]:
    return {DM, ALICE, BOB}


def with_args(**kw: Any) -> dict[str, Any]:
    return kw
