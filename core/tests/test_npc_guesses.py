"""DMbot's guess of which NPCs and groups know a lore statement (#2959 slice 3; STORY_MEMORY 9b
item 4): the answer is checked (its quote is in the document), a name's guesses wait for the DM's
yes and a connection's for its keep, they are written below 1.0, never for a player character or
over the DM's word, and never show a hidden statement to a player. Invented names."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.campaigns import CampaignStore
from dmbot.memory import background as bg
from dmbot.memory import gate, lore_knowledge
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED, PROPOSED, NewName
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.ui import name_background
from tests.pg import DatabaseTest

DOC = (
    "The Tower Council of Harrowgate keeps a list of every member's apprentice. Mirelle Dane is "
    "the apprentice of councillor Brannoch Vale."
)


def answer(*guesses: dict[str, Any]) -> str:
    return json.dumps({"guesses": list(guesses)})


def guess(
    s: int, who: int, how: str = "likely", quote: str = "keeps a list of every member's apprentice"
) -> dict[str, Any]:
    return {"statement": s, "who": who, "how": how, "quote": quote}


class TheAnswer(unittest.TestCase):
    def test_a_guess_needs_its_quote_in_the_document(self) -> None:
        got = lore_knowledge.read_guesses(
            answer(guess(0, 0, "sure"), guess(1, 0, quote="the council knows everything")),
            2, 1, DOC,
        )  # fmt: skip
        self.assertEqual(got, {0: {0: lore_knowledge.NPC_SURE}})

    def test_numbers_off_the_lists_and_long_quotes_are_left_out(self) -> None:
        long = " ".join(["word"] * 13)
        got = lore_knowledge.read_guesses(
            answer(guess(5, 0), guess(0, 3), guess(0, 0, quote=long)), 2, 1, DOC
        )
        self.assertEqual(got, {})

    def test_at_most_three_for_one_statement_and_never_certain(self) -> None:
        got = lore_knowledge.read_guesses(answer(*(guess(0, w) for w in range(5))), 1, 5, DOC)
        assert got is not None
        self.assertEqual(len(got[0]), lore_knowledge.GUESSES_PER_STATEMENT)
        self.assertTrue(all(c < 1.0 for c in got[0].values()))

    def test_prose_is_never_read(self) -> None:
        self.assertIsNone(lore_knowledge.read_guesses("statement 0 is known by 0", 1, 1, DOC))


class _Canned:
    def __init__(self, text: str) -> None:
        self.text, self.calls = text, 0

    async def complete(self, *_: Any, **__: Any) -> Reply:
        self.calls += 1
        return Reply(self.text, False)


class TheCall(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_without_people_or_an_ai(self) -> None:
        ai = _Canned(answer(guess(0, 0)))
        self.assertEqual(await lore_knowledge.guess_npcs(ai, DOC, ["a"], []), {})
        self.assertEqual(await lore_knowledge.guess_npcs(None, DOC, ["a"], ["x"]), {})
        self.assertEqual(ai.calls, 0)

    async def test_an_answer_that_fails_gives_no_guess(self) -> None:
        self.assertEqual(await lore_knowledge.guess_npcs(_Canned("no"), DOC, ["a"], ["x"]), {})


def found(name: str, kind: str = "npc") -> bg.Found:
    return bg.Found(name, kind, (), (), name)


class TheWiring(unittest.IsolatedAsyncioTestCase):
    async def test_names_wait_connections_ride_and_a_player_character_is_never_guessed(
        self,
    ) -> None:
        council, mirelle, tavin = (
            found("Tower Council", "faction"),
            found("Mirelle Dane"),
            found("Tavin Ash", "player_character"),
        )
        link = bg.Connection("Mirelle Dane", "member_of", "Tower Council", "q")
        people_asked: list[list[str]] = []

        async def fake(ai: Any, text: str, lines: Any, people: Any, until: Any = None) -> Any:
            people_asked.append(list(people))
            return {1: {0: 0.6}, 3: {0: 0.9}}  # (Mirelle known by the council; the link too)

        named = (("Tower Council", "a"), ("Mirelle Dane", "b"), ("Tavin Ash", "c"))
        entities = [SimpleNamespace(name=n, id=c * 32) for n, c in named]
        memory: Any = SimpleNamespace(
            entities=AsyncMock(return_value=entities), add_npc_guesses=AsyncMock(return_value=1)
        )
        campaign: Any = SimpleNamespace(guild_id=1, id="c")
        from unittest.mock import patch

        with patch.object(lore_knowledge, "guess_npcs", fake):
            out = await name_background._npc_guesses(
                memory, campaign, [council, mirelle, tavin], [link], DOC, {"tavin ash"}, object()
            )
        self.assertEqual(people_asked, [["Tower Council", "Mirelle Dane"]])  # (never Tavin)
        memory.add_npc_guesses.assert_awaited_once_with(1, "c", [("b" * 32, "a" * 32, 0.6)])
        self.assertEqual(out, {("mirelle dane", "member_of", "tower council"): {"a" * 32: 0.9}})


GUILD, DM = 1, 7


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def entry(self, name: str, kind: str = "npc", status: str = CONFIRMED) -> str:
        written = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName(name, kind, status)], source=DM_SOURCE
        )
        (eid,) = written.value
        assert eid is not None
        return eid

    async def knowers(self, entity_id: str) -> dict[str, list[str]]:
        return await self.memory.statement_knowers(GUILD, self.campaign.id, "entry", [entity_id])

    async def test_a_name_s_guess_waits_for_the_dm_s_yes_and_its_undo_takes_it_back(self) -> None:
        cid = self.campaign.id
        council = await self.entry("Tower Council", "faction")
        mirelle = await self.entry("Mirelle Dane", status=PROPOSED)
        await self.memory.add_npc_guesses(GUILD, cid, [(mirelle, council, 0.6)])
        self.assertEqual(await self.knowers(mirelle), {})  # (nothing while it waits)
        written = await self.memory.set_entity_status(
            GUILD, cid, mirelle, CONFIRMED, source=DM_SOURCE
        )
        self.assertEqual(await self.knowers(mirelle), {mirelle: [council]})
        assert written.batch is not None
        await self.memory.undo(GUILD, cid, written.batch)
        self.assertEqual(await self.knowers(mirelle), {})

    async def test_never_a_player_character_and_never_over_the_dm_s_word(self) -> None:
        cid = self.campaign.id
        tavin = await self.entry("Tavin Ash", "player_character", status=PROPOSED)
        council = await self.entry("Tower Council", "faction")
        mirelle = await self.entry("Mirelle Dane")
        await self.memory.set_statement_knowers(
            GUILD, cid, "entry", mirelle, {council: 1.0}, source=DM_SOURCE
        )
        written = await self.memory.add_guessed_knowers(
            GUILD, cid, "entry", mirelle, {council: 0.6, tavin: 0.9}, source=DM_SOURCE
        )
        self.assertEqual(written.value, 0)

    async def test_a_connection_s_guess_rides_its_row_and_is_written_when_kept(self) -> None:
        from dmbot.memory import backstory_keep
        from dmbot.memory.backstory_links import BackstoryLinks, NewLink

        cid = self.campaign.id
        links = BackstoryLinks(self.db)
        council = await self.entry("Tower Council", "faction")
        mirelle, brannoch = await self.entry("Mirelle Dane"), await self.entry("Brannoch Vale")
        await links.add(
            GUILD, cid, [NewLink(mirelle, "knows", brannoch, "q", npc_guesses={council: 0.6})]
        )
        (row,) = await links.waiting(GUILD, cid)
        self.assertEqual(dict(row.npc_guesses), {council: 0.6})
        await backstory_keep.keep_all(self.memory, links, GUILD, cid, [row])
        (made,) = [
            r for r in await self.memory.relations(GUILD, cid, include_secret=True)
            if r.subject_id == mirelle
        ]  # fmt: skip
        got = await self.memory.statement_knowers(GUILD, cid, "connection", [made.id])
        self.assertEqual(got, {made.id: [council]})

    async def test_a_guessed_npc_never_shows_a_hidden_name_to_a_player(self) -> None:
        cid = self.campaign.id
        council = await self.entry("Tower Council", "faction")
        mirelle = await self.entry("Mirelle Dane", status=PROPOSED)  # (hidden from players)
        await self.memory.add_npc_guesses(GUILD, cid, [(mirelle, council, 0.9)])
        await self.memory.set_entity_status(GUILD, cid, mirelle, CONFIRMED, source=DM_SOURCE)
        party = await self.memory.visible_to(GUILD, cid, gate.Viewer.the_party())
        self.assertNotIn(mirelle, party.entries)


if __name__ == "__main__":
    unittest.main()
