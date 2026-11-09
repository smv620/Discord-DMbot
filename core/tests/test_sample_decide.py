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

    def test_an_unnamed_key_entry_matches_only_a_description_and_articles_never_matter(
        self,
    ) -> None:
        self.assertIsNone(self.key.name_says("Guard at the gate"))  # (a description as a name)
        self.assertEqual(self.key.entry("unnamed - the guard at the gate"), "The guard at the gate")
        self.assertEqual(self.key.entry("The Tran Vosk"), "Tran Vosk")

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
        self.assertIn("(at most)", a.lines(["names"], upper=["names"])[0])


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
        self.assertEqual(d.excerpts(["the gate guard waits"], ["unnamed - the gate guard"]),
                         ["the gate guard waits"])  # fmt: skip
        self.assertEqual(d.excerpts(["all of us"], ["Al Vosk"]), [])  # (whole words only)

    def test_the_estimate_is_shown_in_dollars(self) -> None:
        self.assertGreater(d.estimate(60), 0)


def entity(eid: str, name: str, **kw: Any) -> Any:
    kind, role = kw.get("type", "character"), kw.get("role", "npc")
    return SimpleNamespace(id=eid, name=name, type=kind, role=role,
                           status=kw.get("status", "proposed"), source="extractor")  # fmt: skip


class FakeAI:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def complete(self, system: str, text: str, *, max_tokens: int) -> Reply:
        self.calls += 1
        got = self.answers.pop(0) if self.answers else {"answer": "not sure"}
        if isinstance(got, Exception):
            raise got
        if got == "cut":
            return Reply('{"answer": "yes"', True, 100, 20, "claude-sonnet-5-5")
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

    async def stage(self, ai: FakeAI, cap: int = 60, asker: d.Asker | None = None) -> d.Run:
        asker = asker or d.Asker(ai, "claude-sonnet-5-5", d.Budget(cap), d.Run(), self.record)
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
        found = [entity("u1", "unnamed - guard at the gate", status="confirmed"),
                 entity("e2", "Bruno Hale", status="confirmed")]  # fmt: skip

        async def entities(_g: int, _c: str, *, statuses: Any = ()) -> list[Any]:
            return [] if statuses == ("proposed",) else found

        self.memory.entities = entities
        offers = [[SimpleNamespace(unnamed_id="u1", named_id="e2", reasons=("same place",))], []]
        self.memory.same_person_offers = AsyncMock(side_effect=offers)
        got = await self.stage(FakeAI({"answer": "yes"}))
        self.memory.join_same_person.assert_awaited_once_with(1, "c1", "u1", "e2")
        self.assertEqual(got.agreement.wrong_merges, 1)

    async def test_an_unnamed_person_or_a_table_member_is_never_asked_as_a_name(self) -> None:
        self.names(entity("u1", "unnamed - the woman with the scar"),
                   entity("p1", "Mia", type="person", role=None))  # fmt: skip
        ai = FakeAI({"answer": "no"}, {"answer": "no"})
        await self.stage(ai)
        self.assertEqual(ai.calls, 0)
        self.memory.set_entity_status.assert_not_awaited()

    async def test_a_failed_or_cut_call_is_not_sure_and_counted_apart(self) -> None:
        self.names(entity("e1", "Tran"), entity("e2", "Bruno Hale"))
        got = await self.stage(FakeAI(RuntimeError("down"), "cut"))
        self.assertEqual(got.decided["names"], {j.NOT_SURE: 2})
        self.assertEqual((got.failed, got.cut), (1, 1))
        self.memory.confirm_entity.assert_not_awaited()

    async def test_one_cap_for_both_stages(self) -> None:
        self.names(entity("e1", "Tran"), entity("e2", "Bruno Hale"))
        ai = FakeAI()
        asker = d.Asker(ai, "m", d.Budget(3), d.Run(), self.record)
        await self.stage(ai, asker=asker)
        got = await self.stage(ai, asker=asker)
        self.assertEqual((ai.calls, got.over_cap["names"]), (3, 1))

    async def test_a_refused_which_one_pick_is_counted_refused_not_done(self) -> None:
        q = SimpleNamespace(id="q1", other="Tran", predicate="knows", word="Bruno", quote="",
                            candidates=(("e2", "Bruno Hale"), ("e3", "Shela Marr")))  # fmt: skip
        self.links.which_waiting = AsyncMock(return_value=[q])
        self.links.pick_which = AsyncMock(return_value="gone")
        got = await self.stage(FakeAI({"answer": "yes", "pick": 1}))
        self.assertEqual(got.refused["which one"], 1)
        self.assertNotIn("which one", got.agreement.by_kind)

    async def test_a_wrong_pick_and_a_condition_are_scored_fairly(self) -> None:
        q = SimpleNamespace(id="q1", other="Tran", predicate="knows", word="Bruno", quote="",
                            candidates=(("e2", "Bruno Hale"), ("e3", "Shela Marr")))  # fmt: skip
        self.links.which_waiting = AsyncMock(return_value=[q])
        dead = SimpleNamespace(id="k1", subject="Tran", relationship="is dead", object="",
                               details="")  # fmt: skip
        self.claims.waiting = AsyncMock(return_value=[dead])
        got = await self.stage(FakeAI({"answer": "yes", "pick": 2}, {"answer": "yes"}))
        self.assertEqual(got.agreement.by_kind["which one"][j.WRONG_YES], 1)
        self.assertEqual(got.agreement.by_kind["things said"][j.NOT_IN_KEY], 1)

    async def test_no_reaches_each_kind_s_own_call(self) -> None:
        q = SimpleNamespace(id="q1", other="Tran", predicate="knows", word="Bruno", quote="",
                            candidates=(("e2", "Bruno Hale"), ("e3", "Shela Marr")))  # fmt: skip
        self.links.which_waiting = AsyncMock(return_value=[q])
        claim = SimpleNamespace(id="k1", subject="Tran", relationship="knows", object="Shela Marr",
                                details="")  # fmt: skip
        self.claims.waiting = AsyncMock(return_value=[claim])
        tie = SimpleNamespace(id="r1", subject_id="e1", object_id="e2", status="proposed",
                              source="worked_out", detail="cousin")  # fmt: skip
        self.memory.relations = AsyncMock(return_value=[tie])
        offers = [[SimpleNamespace(unnamed_id="u1", named_id="e2", reasons=())], []]
        self.memory.same_person_offers = AsyncMock(side_effect=offers)
        await self.stage(FakeAI(*[{"answer": "no"}] * 4))
        self.links.pick_which.assert_awaited_once_with(1, "c1", "q1", None)
        self.claims.reject.assert_awaited_once_with(1, "c1", "k1")
        self.memory.update_relation.assert_awaited_once_with(
            1, "c1", "r1", source="dm", status="rejected"
        )
        self.memory.not_the_same_person.assert_awaited_once_with(1, "c1", "u1", "e2", user_id=2)

    async def test_the_printed_lines_hold_no_name(self) -> None:
        self.names(entity("e1", "Tran"), entity("e2", "Bruno Hale"))
        got = await self.stage(FakeAI({"answer": "yes", "kind": "npc"}, {"answer": "no"}))
        text = "\n".join(got.lines())
        for word in ("Tran", "Bruno"):
            self.assertNotIn(word, text)
        rows = [json.loads(line) for line in self.record.read_text().splitlines()]
        self.assertEqual([r["item"] for r in rows], ["e1", "e2"])
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)

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


class TheSteps(unittest.IsolatedAsyncioTestCase):
    async def test_the_decide_steps_are_a_run_of_their_own(self) -> None:
        from dmbot.devtools.sample_run import plan, steps

        seen: list[str] = []

        def act(name: str) -> Any:
            async def go() -> dict[str, int]:
                seen.append(name)
                return {}

            return go

        done: list[Any] = []
        await steps.run_steps({n: act(n) for n in plan.STEPS_DECIDE}, done, lambda _l: None,
                              plan.STEPS_DECIDE)  # fmt: skip
        self.assertEqual(seen, list(plan.STEPS_DECIDE))
        self.assertIn("AI decides (lore)", seen)


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
