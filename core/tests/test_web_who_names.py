"""DMbot's answers to "Who is this?" on the website (#3602, slice 3): the DM-only door, the labels
and the ready answers, and the one-name read. Real Postgres; invented names."""

from __future__ import annotations

import unittest
from typing import Any, cast

from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer, WhoAnswers
from dmbot.web import sessions
from dmbot.web import who_names as web
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_name_checks import GUILD, WithChecks


class Pure(unittest.TestCase):
    def test_labels_and_ready_answers(self) -> None:
        same = {
            "pick": "entity",
            "targetId": "a" * 32,
            "targetName": "Ilse",
            "targetKind": "character",
        }
        self.assertEqual(web.label_of(same), "a.k.a. Ilse, a character")
        self.assertEqual(web.answer_of(same), {"action": "same", "known": "a" * 32})
        new = {"pick": "new", "newKind": "place"}
        self.assertEqual(web.label_of(new), "new place")
        self.assertEqual(web.answer_of(new), {"action": "confirm", "kind": "place"})
        self.assertEqual(web.kind_word("item"), "an item")

    def test_the_rows_become_each_names_ranked_answers_with_words_for_the_confidence(self) -> None:
        rows = [
            {"id": "n1", "pick": "new", "newKind": "item", "confidence": "maybe"},
            {"id": "n2", "pick": "new", "newKind": "place", "confidence": "sure"},
        ]
        got = web.answers_by_name(rows)
        self.assertEqual(got["n1"][0]["confidenceWords"], "DMbot is not sure")
        self.assertEqual(got["n2"][0]["answer"], {"action": "confirm", "kind": "place"})


class OnTheDoor(WithChecks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.who = WhoAnswers(self.memory)

    async def bob(self) -> Any:
        """Someone signed in to the server who is not a DM of this campaign."""
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def keep(self, name_id: str, *answers: StoredAnswer) -> None:
        await self.who.keep(
            GUILD, self.one.id, name_id, list(answers), speaker_ids=(), opted_in=set(),
            session_started_at=1, prompt_version="who-1", model="m",
        )  # fmt: skip

    async def test_a_dm_reads_the_ranked_answers_of_a_waiting_name(self) -> None:
        name = await self.suggest("Vale")
        ilse = await self.known("Ilse", "character")
        await self.keep(
            name,
            StoredAnswer(ENTITY, "likely", target_id=ilse),
            StoredAnswer(NEW, "maybe", new_kind="character"),
        )
        got = await web.read_one(self.web, self.session, self.ref, name)
        self.assertEqual(got["id"], name)
        self.assertEqual(
            [(a["label"], a["answer"]["action"], a["confidence"]) for a in got["answers"]],
            [("a.k.a. Ilse, a character", "same", "likely"), ("new character", "confirm", "maybe")],
        )
        self.assertEqual(got["answers"][0]["answer"]["known"], ilse)
        self.assertTrue(got["kinds"])

    async def test_a_name_with_no_answers_yet_has_an_empty_list(self) -> None:
        name = await self.suggest("Vale")
        got = await web.read_one(self.web, self.session, self.ref, name)
        self.assertEqual(got["answers"], [])

    async def test_a_player_and_a_stranger_are_told_nothing(self) -> None:
        name = await self.suggest("Vale")
        await self.keep(name, StoredAnswer(NEW, "sure", new_kind="place"))
        for who in (await self.bob(),):
            self.assertEqual(
                await self.code(web.read_one(self.web, who, self.ref, name)), "not_found"
            )
            self.assertEqual(
                await self.code(web.read_answers(self.web, who, self.ref, [name])), "not_found"
            )

    async def test_a_hidden_entry_is_named_for_the_dm_only(self) -> None:
        name = await self.suggest("Vale")
        fox = await self.known("The Grey Fox", "character", secret=True)
        await self.keep(name, StoredAnswer(ENTITY, "likely", target_id=fox))
        mine = await web.read_answers(self.web, self.session, self.ref, [name])
        self.assertIn("The Grey Fox", mine[name][0]["label"])
        self.assertEqual(
            await self.code(web.read_answers(self.web, await self.bob(), self.ref, [name])),
            "not_found",
        )

    async def test_an_answer_goes_when_the_name_is_answered_or_the_entry_it_names_is_gone(
        self,
    ) -> None:
        name = await self.suggest("Vale")
        ilse = await self.known("Ilse", "character")
        await self.keep(
            name,
            StoredAnswer(ENTITY, "likely", target_id=ilse),
            StoredAnswer(NEW, "maybe", new_kind="place"),
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE id = %s", (ilse,)
            )
        got = await web.read_answers(self.web, self.session, self.ref, [name])
        self.assertEqual([a["label"] for a in got[name]], ["new place"])
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (name,)
            )
        self.assertEqual(await web.read_answers(self.web, self.session, self.ref, [name]), {})

    async def test_a_name_not_waiting_in_this_recap_is_not_found(self) -> None:
        other = await self.suggest("Orrin", link=False)
        self.assertEqual(
            await self.code(web.read_one(self.web, self.session, self.ref, other)), "not_found"
        )
        self.assertEqual(
            await self.code(web.read_one(self.web, self.session, self.ref, "nonsense")), "not_found"
        )

    async def test_another_campaigns_answers_never_come_back(self) -> None:
        name = await self.suggest("Vale")
        await self.keep(name, StoredAnswer(NEW, "sure", new_kind="place"))
        foreign = await web.read_answers(self.web, self.session, f"{self.ref}", ["f" * 32])
        self.assertEqual(foreign, {})
