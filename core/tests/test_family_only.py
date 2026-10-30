"""Sessions mine family connections only (#2960; owner, 2026-10-29): a claim that is not a family
role is dropped in code, whatever the AI returned; a speaker who stopped being recorded gives
nothing; the readings question is not asked in this mode. Invented names; the AI is canned."""

from __future__ import annotations

import json
import unittest
from collections.abc import Collection, Sequence
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import extractor
from dmbot.memory.claims import Extracted, Proposed, is_family
from tests.test_extractor import ALICE, BOB, DM, go, session

FAMILY = "is the niece of"


def said(relationship: str, subject: str = "Mira", obj: str = "Aldric") -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "player_said")


class TheRule(unittest.TestCase):
    def test_a_family_role_is_family(self) -> None:
        for words in (
            FAMILY,
            "is the uncle of",
            "has a niece",
            "was the mother of",
            "is a cousin to",
        ):
            self.assertTrue(is_family(said(words)), words)

    def test_every_other_kind_is_not(self) -> None:
        for words in ("carries", "is an ally of", "is an enemy of", "is a member of", "works for",
                      "is the king of", "is family of", "is related to", "lives in",
                      "is the personal assistant of", "is the grandest of"):  # fmt: skip
            with self.subTest(words=words):
                self.assertFalse(is_family(said(words)), words)


class Mixed:
    """An AI that returns one family claim and two others for every batch."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.systems.append(system)
        first = int(text.split("\n")[1].split(" ")[0])
        rows = [
            {"subject": "Mira", "relationship": FAMILY, "object": "Aldric", "details": "",
             "lines": [first], "how": "player_said"},
            {"subject": "Mira", "relationship": "is an ally of", "object": "Bram", "details": "",
             "lines": [first], "how": "player_said"},
            {"subject": "Bram", "relationship": "carries", "object": "Heartseeker", "details": "",
             "lines": [first], "how": "dm_said"},
        ]  # fmt: skip
        return Reply(json.dumps({"claims": rows}), False, 10, 5, "m-fast")


class Handed:
    def __init__(self) -> None:
        self.claims: list[Extracted] = []

    async def propose(
        self, guild_id: int, campaign_id: str, claims: Sequence[Extracted],
        speakers: Any, *, opted_in: Collection[int], **kw: Any,
    ) -> Proposed:  # fmt: skip
        self.claims.extend(claims)
        return Proposed(len(claims), 0, 0, kw["malformed"], {})


class TheRun(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_family_claim_is_handed_over_and_the_rest_is_counted(self) -> None:
        store = Handed()
        report = await go(Mixed(), store, session(10), family_only=True)
        self.assertEqual([c.relationship for c in store.claims], [FAMILY])
        self.assertEqual(report.dropped["not family"], 2)

    async def test_off_it_keeps_everything_as_before(self) -> None:
        store = Handed()
        await go(Mixed(), store, session(10))
        self.assertEqual(len(store.claims), 3)

    async def test_the_readings_question_is_not_asked_in_this_mode(self) -> None:
        ai = Mixed()
        report = await go(ai, Handed(), session(10), family_only=True, readings=True)
        self.assertEqual((report.calls, ai.systems), (1, [extractor.SYSTEM]))

    async def test_a_line_from_someone_who_stopped_being_recorded_gives_nothing(self) -> None:
        async def only_bob() -> Collection[int]:
            return {DM, BOB}

        store = Handed()
        report = await go(Mixed(), store, session(10, ALICE), consenting=only_bob, family_only=True)
        self.assertEqual((store.claims, report.calls), ([], 0))  # (nothing was even sent)
