"""The sample run's careful-model decisions (#3900): the key scoring, and each kind reaching the
store's DM call, with stand-ins for the store and the model. Invented names (Tran Vosk, Bruno Hale,
Shela Marr, the guard at the gate)."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import Reply
from dmbot.devtools import accept_all
from dmbot.devtools.sample_run import decide as d
from dmbot.devtools.sample_run import judge as j

KEY = {
    "nodes": [
        {"name": "Tran Vosk", "aliases": ["Tran"]},
        {"name": "Bruno Hale"},
        {"name": "Shela Marr"},
        {"name": "The guard at the gate", "aliases": ["guard at the gate"], "unnamed": True},
    ],
    "pairs": [{"a": "Tran Vosk", "b": "Bruno Hale", "relation": "knows"}],
    "must_not_names": ["Owlbear"],
}


class TheKey(unittest.TestCase):
    def setUp(self) -> None:
        self.key = j.Key(KEY)

    def test_names(self) -> None:
        self.assertEqual(self.key.name_says("Tran"), j.YES)
        self.assertEqual(self.key.name_says("Owlbear"), j.NO)
        self.assertIsNone(self.key.name_says("Someone Else"))

    def test_ties(self) -> None:
        self.assertEqual(self.key.tie_says("Bruno Hale", "Tran"), j.YES)
        self.assertIsNone(self.key.tie_says("Bruno Hale", "Shela Marr"))
        self.assertEqual(self.key.tie_says("Owlbear", "Tran"), j.NO)

    def test_same_person_and_the_unnamed_label(self) -> None:
        self.assertEqual(self.key.same_says("unnamed - guard at the gate", "Bruno Hale"), j.NO)
        self.assertEqual(self.key.same_says("Tran", "Tran Vosk"), j.YES)
        self.assertIsNone(self.key.same_says("unnamed - a cook", "Bruno Hale"))

    def test_verdicts(self) -> None:
        self.assertEqual(j.verdict(j.YES, j.YES), j.RIGHT_YES)
        self.assertEqual(j.verdict(j.NO, j.YES), j.WRONG_NO)
        self.assertEqual(j.verdict(j.YES, j.NO), j.WRONG_YES)
        self.assertEqual(j.verdict(j.NO, j.NO), j.RIGHT_NO)
        self.assertEqual(j.verdict(j.YES, None), j.NOT_IN_KEY)
        self.assertIsNone(j.verdict(j.NOT_SURE, j.YES))

    def test_agreement_per_kind_and_wrong_merges_apart(self) -> None:
        a = j.Agreement()
        a.add("names", j.YES, j.YES)
        a.add("names", j.YES, j.NO)
        a.add("look-alikes", j.YES, j.NO, merge=True)
        lines = a.lines(["names", "look-alikes"])
        self.assertIn("agreement with the key, names: 50%", lines[0])
        self.assertEqual(lines[-1], "merged two the key keeps apart: 1")


class TheAnswer(unittest.TestCase):
    def test_an_unreadable_answer_is_not_sure(self) -> None:
        self.assertEqual(d.parse("nonsense")["answer"], j.NOT_SURE)
        self.assertEqual(d.parse('{"answer": "maybe"}')["answer"], j.NOT_SURE)
        self.assertEqual(d.parse('ok {"answer": "Yes", "kind": "npc"}')["answer"], j.YES)

    def test_excerpts_hold_the_name_and_are_few(self) -> None:
        doc = "\n".join(["Tran rode north."] * 10 + ["Nothing here."])
        got = d.excerpts([doc], ["Tran Vosk"])
        self.assertEqual(len(got), d.EXCERPTS)
        self.assertTrue(all("Tran" in line for line in got))


def entity(eid: str, name: str, **kw: Any) -> Any:
    kind, role = kw.get("type", "character"), kw.get("role", "npc")
    return SimpleNamespace(id=eid, name=name, type=kind, role=role, status="proposed")


class FakeAI:
    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def complete(self, system: str, text: str, *, max_tokens: int) -> Reply:
        self.calls += 1
        got = self.answers.pop(0) if self.answers else {"answer": "not sure"}
        return Reply(json.dumps(got), False, 100, 20, "claude-sonnet-5-5")


class TheStage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.record = Path(self.tmp.name) / "decisions.jsonl"
        written = SimpleNamespace(batch=7)
        self.memory = SimpleNamespace(
            entities=AsyncMock(return_value=[]),
            relations=AsyncMock(return_value=[]),
            same_person_offers=AsyncMock(return_value=[]),
            confirm_entity=AsyncMock(return_value=written),
            set_entity_status=AsyncMock(return_value=written),
            update_relation=AsyncMock(return_value=written),
            join_same_person=AsyncMock(return_value=written),
            not_the_same_person=AsyncMock(return_value=None),
        )
        self.links = SimpleNamespace(
            which_waiting=AsyncMock(return_value=[]), pick_which=AsyncMock(return_value="done")
        )
        self.claims = SimpleNamespace(
            waiting=AsyncMock(return_value=[]), confirm=AsyncMock(), reject=AsyncMock(),
            placed_batch=AsyncMock(return_value=9),
        )  # fmt: skip

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def stage(self, ai: FakeAI, cap: int = 60) -> d.Run:
        asker = d.Asker(ai, "claude-sonnet-5-5", d.Budget(cap), d.Run(), self.record)
        with patch.object(accept_all, "check_campaign", AsyncMock()):
            return await d.run(
                None, self.memory, self.links, self.claims, asker, j.Key(KEY), 1, "c1",
                docs=["Tran rode north with Bruno."], opted_in={5}, dm=2,
            )  # fmt: skip

    def names(self, *found: Any) -> None:
        async def entities(_g: int, _c: str, *, statuses: Any = ()) -> list[Any]:
            return list(found)

        self.memory.entities = entities

    async def test_yes_confirms_no_rejects_and_not_sure_is_left(self) -> None:
        self.names(entity("e1", "Tran"), entity("e2", "Owlbear"), entity("e3", "Shela Marr"))
        ai = FakeAI({"answer": "yes", "kind": "npc"}, {"answer": "no"}, {"answer": "not sure"})
        got = await self.stage(ai)
        self.memory.confirm_entity.assert_awaited_once_with(1, "c1", "e1", "npc", source="dm")
        self.memory.set_entity_status.assert_awaited_once_with(
            1, "c1", "e2", "rejected", source="dm"
        )
        self.assertEqual(got.decided["names"], {j.YES: 1, j.NO: 1, j.NOT_SURE: 1})
        self.assertEqual(got.agreement.by_kind["names"][j.RIGHT_YES], 1)
        self.assertEqual(got.agreement.by_kind["names"][j.RIGHT_NO], 1)
        rows = [json.loads(line) for line in self.record.read_text().splitlines()]
        self.assertEqual([r["decision"] for r in rows], [j.YES, j.NO, j.NOT_SURE])
        self.assertEqual({r["tier"] for r in rows}, {"careful"})

    async def test_a_player_character_is_never_asked(self) -> None:
        self.names(entity("e1", "Tran", role="player_character"))
        ai = FakeAI({"answer": "yes", "kind": "npc"})
        await self.stage(ai)
        self.assertEqual(ai.calls, 0)

    async def test_the_cap_stops_it(self) -> None:
        self.names(entity("e1", "Tran"), entity("e2", "Bruno Hale"), entity("e3", "Shela Marr"))
        ai = FakeAI({"answer": "yes", "kind": "npc"}, {"answer": "yes", "kind": "npc"})
        got = await self.stage(ai, cap=1)
        self.assertEqual((ai.calls, got.over_cap["names"]), (1, 2))

    async def test_a_yes_with_no_kind_is_left_and_counted_refused(self) -> None:
        self.names(entity("e1", "Tran"))
        got = await self.stage(FakeAI({"answer": "yes"}))
        self.memory.confirm_entity.assert_not_awaited()
        self.assertEqual(got.refused["names"], 1)

    async def test_a_wrong_merge_shows_in_its_own_row(self) -> None:
        self.names(entity("u1", "unnamed - guard at the gate"), entity("e2", "Bruno Hale"))
        offers = [[SimpleNamespace(unnamed_id="u1", named_id="e2", reasons=("same place",))], []]
        self.memory.same_person_offers = AsyncMock(side_effect=offers)
        got = await self.stage(FakeAI({"answer": "not sure"}, {"answer": "not sure"},
                                      {"answer": "yes"}))  # fmt: skip
        self.memory.join_same_person.assert_awaited_once_with(1, "c1", "u1", "e2")
        self.assertEqual(got.agreement.wrong_merges, 1)

    async def test_things_said_and_which_one_reach_their_calls(self) -> None:
        claim = SimpleNamespace(id="k1", subject="Tran", relationship="knows", object="Bruno Hale",
                                details="")  # fmt: skip
        self.claims.waiting = AsyncMock(return_value=[claim])
        q = SimpleNamespace(id="q1", other="Tran", predicate="knows", word="Bruno", quote="",
                            candidates=(("e2", "Bruno Hale"), ("e3", "Shela Marr")))  # fmt: skip
        self.links.which_waiting = AsyncMock(return_value=[q])
        got = await self.stage(FakeAI({"answer": "yes", "pick": 1}, {"answer": "yes"}))
        self.links.pick_which.assert_awaited_once_with(1, "c1", "q1", "e2")
        self.claims.confirm.assert_awaited_once_with(1, "c1", "k1", opted_in={5})
        self.assertEqual(got.agreement.by_kind["which one"][j.RIGHT_YES], 1)
        self.assertEqual(got.agreement.by_kind["things said"][j.RIGHT_YES], 1)
        self.assertIn(9, got.batches)


class TheGuard(unittest.IsolatedAsyncioTestCase):
    async def test_another_campaign_is_refused_before_anything_is_asked(self) -> None:
        class Db:
            @asynccontextmanager
            async def guild(self, _g: int) -> Any:
                cur = SimpleNamespace(fetchone=AsyncMock(return_value={"name": "My real game"}))
                yield SimpleNamespace(execute=AsyncMock(return_value=cur))

        ai = FakeAI({"answer": "yes"})
        with tempfile.TemporaryDirectory() as tmp:
            asker = d.Asker(ai, "m", d.Budget(5), d.Run(), Path(tmp) / "r.jsonl")
            with self.assertRaises(accept_all.WrongCampaign):
                await d.run(Db(), None, None, None, asker, j.Key(KEY), 1, "c1", docs=[],
                            opted_in=set(), dm=2)  # fmt: skip
        self.assertEqual(ai.calls, 0)


if __name__ == "__main__":
    unittest.main()
