"""Matching a proposed story role to one the campaign has (#3172): the choice (strong, weak, close,
none), the seeded vectors made once per text, an added role's vector kept in its own campaign, the
reading using the held role instead of a near-duplicate, and a failed match adding the role as
before. The golden set and its scorer with a stand-in embedder. Invented words; the store and step
tests need Postgres."""

from __future__ import annotations

import json
import math
import unittest
from collections.abc import Collection, Sequence

from dmbot.ai import Reply
from dmbot.campaigns import CampaignStore
from dmbot.devtools import role_match_check as check
from dmbot.memory import importance_read as ir
from dmbot.memory import importance_step as step
from dmbot.memory import role_golden, roles
from dmbot.memory import role_match as rm
from dmbot.memory import vectors as vs
from dmbot.memory.models import CONFIRMED, DM
from dmbot.memory.role_store import RoleStore
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

SEEDED = [r for r in roles.SEEDED_ROLES if r.code != roles.COMMONER]
INDEX = {r.code: i for i, r in enumerate(SEEDED)}
DIM = len(SEEDED) + 8  # (room for roles of their own)
BY_TEXT = {r.embed_text: i for i, r in enumerate(SEEDED)}


def axis(i: int, lean: float = 0.0, toward: int | None = None) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    if toward is not None:
        v[toward] = lean
    return v


class FakeEmbedder:
    """A seeded role's text is its own direction; any other text is looked up in `extra`."""

    def __init__(self, extra: dict[str, list[float]] | None = None, fail: bool = False) -> None:
        self.extra = extra or {}
        self.fail = fail
        self.calls: list[int] = []

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        self.calls.append(len(texts))
        if self.fail:
            raise RuntimeError("down")
        return [axis(BY_TEXT[t]) if t in BY_TEXT else self.extra[t] for t in texts]


class TheChoice(unittest.TestCase):
    def test_a_strong_clear_match_is_used(self) -> None:
        best = rm.nearest(axis(0), {"a": axis(0), "b": axis(1)})
        self.assertEqual(rm.decide(best), ("a", ""))

    def test_a_weak_match_is_not(self) -> None:
        best = rm.nearest(axis(5, 1.0, 0), {"a": axis(0), "b": axis(1)})  # cosine about 0.71
        self.assertEqual(rm.decide(best), (None, "below_threshold"))

    def test_a_close_second_is_not(self) -> None:
        between = [1.0, 0.98] + [0.0] * (DIM - 2)
        best = rm.nearest(between, {"a": axis(0), "b": axis(1)})
        self.assertEqual(rm.decide(best, threshold=0.5), (None, "close_second"))

    def test_nothing_to_compare_or_a_bad_vector_is_no_match(self) -> None:
        self.assertEqual(rm.decide(rm.nearest(axis(0), {})), (None, "no_match"))
        self.assertIsNone(rm.nearest(axis(0), {"a": [1.0, 0.0]}))  # (another length)
        self.assertIsNone(rm.nearest([math.nan] * DIM, {"a": axis(0)}))

    def test_one_role_alone_can_still_be_matched(self) -> None:
        self.assertEqual(rm.decide(rm.nearest(axis(0), {"a": axis(0)})), ("a", ""))


class TheSeededVectors(unittest.IsolatedAsyncioTestCase):
    async def test_made_once_per_model_and_every_call_counted(self) -> None:
        embedder, counted = FakeEmbedder(), []

        async def count() -> None:
            counted.append(1)

        seeds = rm.SeededVectors()
        first = await seeds.get(embedder, "m1", count)
        again = await seeds.get(embedder, "m1", count)
        self.assertEqual(first, again)
        self.assertEqual(set(first), set(INDEX))  # (never commoner: it is never stored)
        self.assertEqual(embedder.calls, [len(SEEDED)])
        await seeds.get(embedder, "m2", count)  # (another model: made again)
        self.assertEqual((len(embedder.calls), len(counted)), (2, 2))

    async def test_a_failed_call_keeps_nothing(self) -> None:
        seeds = rm.SeededVectors()
        with self.assertRaises(RuntimeError):
            await seeds.get(FakeEmbedder(fail=True), "m", None)
        self.assertEqual(len(await seeds.get(FakeEmbedder(), "m", None)), len(SEEDED))


class TheGoldenSet(unittest.IsolatedAsyncioTestCase):
    def test_every_case_is_well_formed_and_none_copies_a_seeded_text(self) -> None:
        codes = [c.code for c in role_golden.CASES]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertGreaterEqual(len(role_golden.CASES), 55)
        seeded_texts = {r.embed_text for r in roles.SEEDED_ROLES}
        for case in role_golden.CASES:
            self.assertIn(case.expected, (*INDEX, role_golden.NONE), case.code)
            self.assertIsNone(
                roles.check_added(case.code, case.code, case.description, case.examples, ""),
                case.code,
            )
            self.assertNotIn(case.text, seeded_texts)
        self.assertGreaterEqual(sum(c.expected == role_golden.NONE for c in role_golden.CASES), 10)

    async def test_a_perfect_embedder_scores_every_case_and_chooses_a_pair(self) -> None:
        own = len(SEEDED)

        async def perfect(texts: Sequence[str]) -> list[list[float]]:
            by_text = {c.text: c for c in role_golden.CASES}
            out = []
            for t in texts:
                if t in BY_TEXT:
                    out.append(axis(BY_TEXT[t]))
                elif by_text[t].expected == role_golden.NONE:
                    out.append(axis(own))  # (nowhere near any seeded role)
                else:
                    out.append(axis(INDEX[by_text[t].expected], 0.1, own))
            return out

        result = await check.evaluate(perfect)
        self.assertEqual(result.typed + result.nones, result.cases)
        self.assertIsNotNone(result.chosen)
        assert result.chosen is not None
        self.assertEqual((result.chosen.wrong, result.chosen.none_wrong), (0, 0))
        self.assertEqual(result.chosen.right, result.typed)
        text = check.report(result)
        self.assertIn("choose: threshold", text)
        for case in role_golden.CASES:  # (counts only: no role's words)
            self.assertNotIn(case.description, text)

    async def test_no_pair_is_chosen_when_none_cases_are_swallowed(self) -> None:
        async def everything_is_a_mentor(texts: Sequence[str]) -> list[list[float]]:
            return [axis(BY_TEXT[t]) if t in BY_TEXT else axis(INDEX["mentor"]) for t in texts]

        result = await check.evaluate(everything_is_a_mentor)
        self.assertIsNone(result.chosen)
        self.assertIn("choose: no pair", check.report(result))


def proposed(code: str, words: str) -> dict[str, object]:
    return {
        "code": code, "label": code.replace("_", " "), "description": words,
        "examples": [f"{words} one.", f"{words} two.", f"{words} three."], "not_this": "",
    }  # fmt: skip


def text_of(p: dict[str, object]) -> str:
    examples = p["examples"]
    assert isinstance(examples, list)
    return roles.embed_text(str(p["description"]), tuple(str(e) for e in examples))


class TheMatcher(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        campaigns = CampaignStore(self.db)
        self.g = 7902
        self.c = (await campaigns.create(self.g, "Rime", 1)).id
        self.other = (await campaigns.create(self.g, "Frost", 1)).id
        self.roles = RoleStore(self.db)
        self.store = vs.VectorStore(self.db)
        self.vex = await self.entry("Vex")
        self.counted = 0

    async def entry(self, name: str) -> str:
        made = await self.memory.add_entity(
            self.g, self.c, type="character", name=name, source=DM, status=CONFIRMED
        )
        return str(made.value.id)

    def matcher(self, embedder: FakeEmbedder) -> rm.RoleMatcher:
        return rm.RoleMatcher(
            embedder, self.store, self.roles, "m", rm.SeededVectors(), count_call=self.count
        )

    async def count(self) -> None:
        self.counted += 1

    async def consenting(self) -> Collection[int]:
        return set()

    async def read(
        self,
        embedder: FakeEmbedder,
        *new: dict[str, object],
        holds: list[str] | None = None,
        ended: list[str] | None = None,
        item: ir.Item | None = None,
    ) -> step.Report:
        await self.roles.ensure_codes(self.g, self.c)
        codes = await self.roles.codes(self.g, self.c)
        held = holds if holds is not None else [str(p["code"]) for p in new]
        reply = json.dumps({
            "entries": [{"entry": 1, "rank": 2, "roles": held, "ended_roles": ended or []}],
            "new_roles": list(new),
        })  # fmt: skip

        class AI:
            async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
                return Reply(reply, False)

        return await step.run(
            AI(), self.db, [item or ir.Item(1, self.vex, "Vex", "character", 1)], codes, {},
            guild_id=self.g, campaign_id=self.c, session_started_at=100,
            consenting=self.consenting, max_calls=3, matcher=self.matcher(embedder),
        )  # fmt: skip

    async def held(self) -> set[str]:
        return {r.role for r in await self.roles.held(self.g, self.c)}

    async def test_a_new_role_that_means_a_seeded_one_is_stored_as_that_one(self) -> None:
        henchman = proposed("henchman", "A thug doing a villain's work")
        report = await self.read(FakeEmbedder({text_of(henchman): axis(INDEX["minion"])}), henchman)
        self.assertEqual((report.codes_matched, report.codes_added), (1, 0))
        self.assertEqual(await self.held(), {"minion"})
        self.assertNotIn("henchman", await self.roles.codes(self.g, self.c))

    async def test_a_role_of_its_own_is_added_and_its_vector_kept_in_its_campaign(self) -> None:
        heir = proposed("heir", "Next in line to rule")
        report = await self.read(FakeEmbedder({text_of(heir): axis(len(SEEDED))}), heir)
        self.assertEqual((report.codes_matched, report.codes_added), (0, 1))
        self.assertEqual(report.lost["role_match_below_threshold"], 1)
        self.assertEqual(await self.held(), {"heir"})
        kept = await self.store.vectors(self.g, self.c, "m", vs.ROLE)
        self.assertEqual([k.owner_id for k in kept], ["heir"])
        self.assertEqual(kept[0].hash, vs.text_hash(text_of(heir)))
        self.assertEqual(await self.store.vectors(self.g, self.other, "m", vs.ROLE), [])

    async def test_a_later_role_that_means_an_added_one_is_stored_as_the_added_one(self) -> None:
        heir = proposed("heir", "Next in line to rule")
        claimant = proposed("claimant", "Has a claim to the throne")
        embedder = FakeEmbedder({
            text_of(heir): axis(len(SEEDED)), text_of(claimant): axis(len(SEEDED), 0.1, 0),
        })  # fmt: skip
        await self.read(embedder, heir)
        report = await self.read(embedder, claimant)
        self.assertEqual(report.codes_matched, 1)
        self.assertNotIn("claimant", await self.roles.codes(self.g, self.c))

    async def test_another_campaign_s_added_role_is_never_matched(self) -> None:
        await self.roles.add_code(
            self.g, self.other, "heir", "heir", "Next in line to rule",
            ("a one.", "a two.", "a three."), "",
        )  # fmt: skip
        other_text = roles.embed_text("Next in line to rule", ("a one.", "a two.", "a three."))
        await self.store.save(self.g, self.other, "m", [
            vs.Item(vs.ROLE, "heir", vs.text_hash(other_text), axis(len(SEEDED)))
        ])  # fmt: skip
        claimant = proposed("claimant", "Has a claim to the throne")
        report = await self.read(FakeEmbedder({text_of(claimant): axis(len(SEEDED))}), claimant)
        self.assertEqual((report.codes_matched, report.codes_added), (0, 1))

    async def test_a_missing_vector_is_made_again_and_a_gone_code_s_is_dropped(self) -> None:
        await self.roles.add_code(
            self.g, self.c, "heir", "heir", "Next in line to rule",
            ("a one.", "a two.", "a three."), "",
        )  # fmt: skip
        await self.store.save(self.g, self.c, "m", [vs.Item(vs.ROLE, "gone", "h", axis(0))])
        heir_text = roles.embed_text("Next in line to rule", ("a one.", "a two.", "a three."))
        claimant = proposed("claimant", "Has a claim to the throne")
        embedder = FakeEmbedder({
            heir_text: axis(len(SEEDED)), text_of(claimant): axis(len(SEEDED)),
        })  # fmt: skip
        report = await self.read(embedder, claimant)
        self.assertEqual(report.codes_matched, 1)  # (to the heir, made again)
        kept = {k.owner_id for k in await self.store.vectors(self.g, self.c, "m", vs.ROLE)}
        self.assertEqual(kept, {"heir"})

    async def test_a_failed_match_adds_the_role_as_before_and_is_counted(self) -> None:
        heir = proposed("heir", "Next in line to rule")
        report = await self.read(FakeEmbedder(fail=True), heir)
        self.assertEqual((report.match_failed, report.codes_added), (1, 1))
        self.assertEqual(await self.held(), {"heir"})
        self.assertIn("1 match(es) failed", report.line())

    async def test_holding_the_new_role_and_its_match_stores_it_once(self) -> None:
        henchman = proposed("henchman", "A thug doing a villain's work")
        embedder = FakeEmbedder({text_of(henchman): axis(INDEX["minion"])})
        await self.read(embedder, henchman, holds=["henchman", "minion"])
        rows = [r.role for r in await self.roles.held(self.g, self.c)]
        self.assertEqual(rows, ["minion"])

    async def test_a_role_held_through_the_swap_is_not_ended_in_the_same_answer(self) -> None:
        henchman = proposed("henchman", "A thug doing a villain's work")
        embedder = FakeEmbedder({text_of(henchman): axis(INDEX["minion"])})
        report = await self.read(embedder, henchman, ended=["minion"])
        self.assertEqual(await self.held(), {"minion"})
        self.assertEqual(report.roles_ended, 0)

    async def test_every_call_is_counted_and_not_this_is_never_sent(self) -> None:
        heir = {**proposed("heir", "Next in line to rule"), "not_this": "Unusual warning words"}
        sent: list[str] = []
        embedder = FakeEmbedder({text_of(heir): axis(len(SEEDED))})
        real = embedder.embed

        async def watch(texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
            sent.extend(texts)
            return await real(texts, query=query)

        embedder.embed = watch  # type: ignore[method-assign]
        report = await self.read(embedder, heir)
        self.assertEqual(report.codes_added, 1)
        self.assertEqual(self.counted, len(embedder.calls))  # (seeded once, the proposed once)
        self.assertEqual(self.counted, 2)
        self.assertFalse(any("Unusual warning words" in t for t in sent))

    async def test_a_kept_vector_of_another_length_is_made_again(self) -> None:
        await self.roles.add_code(
            self.g, self.c, "heir", "heir", "Next in line to rule",
            ("a one.", "a two.", "a three."), "",
        )  # fmt: skip
        heir_text = roles.embed_text("Next in line to rule", ("a one.", "a two.", "a three."))
        await self.store.save(
            self.g, self.c, "m", [vs.Item(vs.ROLE, "heir", vs.text_hash(heir_text), [1.0, 0.0])]
        )
        claimant = proposed("claimant", "Has a claim to the throne")
        embedder = FakeEmbedder({
            heir_text: axis(len(SEEDED)), text_of(claimant): axis(len(SEEDED)),
        })  # fmt: skip
        report = await self.read(embedder, claimant)
        self.assertEqual(report.codes_matched, 1)
        (kept,) = await self.store.vectors(self.g, self.c, "m", vs.ROLE)
        self.assertEqual(len(kept.vector), DIM)


if __name__ == "__main__":
    unittest.main()
