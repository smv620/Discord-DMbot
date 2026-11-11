"""A one-of-a-kind item changing hands (#4115): the DM's "Does A still have it?" question, its
answers, Undo, the remembered "Yes, both", the end reason, and the history line. Invented names."""

from __future__ import annotations

import unittest
from dataclasses import replace

from psycopg import errors

from dmbot.memory import hand_over
from dmbot.memory._changes import RELATIONS
from dmbot.memory.checks import HELD_TWICE
from dmbot.memory.hand_over import HAS_NOW, USED_TO, history
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, Relation
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

S1, S2 = 1_700_000_000, 1_700_100_000  # two sessions' starts


class TheQuestion(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claude = await self.add("Claude", type="character")
        self.belleros = await self.add("Belleros", type="character")
        self.brand = await self.add("Frost Brand", type="item")
        await self.memory.set_unique(GUILD_A, self.c, self.brand, True, source=DM)

    async def tie(self, who: str, pred: str, at: int, item: str | None = None) -> str:
        made = await self.relate(who, pred, item or self.brand, status=CONFIRMED,
                                 from_session_at=at)  # fmt: skip
        return str(made.value[0].id)

    async def waiting(self) -> list[hand_over.Question]:
        return await self.memory.hand_over_waiting(GUILD_A, self.c, source=DM)

    async def row(self, relation_id: str) -> dict[str, object]:
        async with self.memory._read(GUILD_A, self.c) as scope:
            got = await scope.get(RELATIONS, relation_id, lock=False)
        assert got is not None
        return got

    async def answer(self, q: hand_over.Question, *, still_has: bool) -> int | None:
        written = await self.memory.answer_hand_over(
            GUILD_A, self.c, q, still_has=still_has, source=DM
        )
        return written.batch

    async def test_a_second_owner_of_a_unique_item_raises_one_question(self) -> None:
        old = await self.tie(self.claude, "owns", S1)
        new = await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        self.assertEqual(
            (q.item_id, q.older_id, q.newer_id, q.predicate, q.older_relation_id,
             q.newer_relation_id),
            (self.brand, self.claude, self.belleros, "owns", old, new),
        )  # fmt: skip

    async def test_an_item_that_is_not_one_of_a_kind_raises_none(self) -> None:
        sword = await self.add("longsword", type="item")
        await self.memory.set_unique(GUILD_A, self.c, sword, False, source=DM)
        await self.tie(self.claude, "owns", S1, sword)
        await self.tie(self.belleros, "owns", S2, sword)
        self.assertEqual(await self.waiting(), [])

    async def test_an_owner_and_someone_else_holding_it_is_no_question(self) -> None:
        # (a thief has what someone else owns: Supervisor's ruling)
        await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "carries", S2)
        self.assertEqual(await self.waiting(), [])

    async def test_no_not_any_more_ends_the_older_ties_as_history(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        carries = await self.tie(self.claude, "carries", S1)
        new = await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        await self.answer(q, still_has=False)
        for ended in (owns, carries):  # (what Claude no longer owns, Claude no longer holds)
            got = await self.row(ended)
            self.assertEqual((got["to_session_at"], got["end_reason"], got["status"]),
                             (S2, None, CONFIRMED))  # fmt: skip
        self.assertIsNone((await self.row(new))["to_session_at"])
        self.assertEqual(await self.waiting(), [])

    async def test_two_holders_ended_clear_the_held_twice_alert(self) -> None:
        await self.tie(self.claude, "carries", S1)
        await self.tie(self.belleros, "carries", S2)
        flags = await self.memory.flags(GUILD_A, self.c)
        self.assertTrue(any(f.kind == HELD_TWICE for f in flags))
        (q,) = await self.waiting()
        self.assertEqual(q.predicate, "carries")
        await self.answer(q, still_has=False)
        flags = await self.memory.flags(GUILD_A, self.c)
        self.assertFalse(any(f.kind == HELD_TWICE for f in flags))

    async def test_a_held_carrying_only_ends_the_carrying(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        carries = await self.tie(self.claude, "carries", S1)
        await self.tie(self.belleros, "carries", S2)
        (q,) = await self.waiting()
        await self.answer(q, still_has=False)
        self.assertIsNone((await self.row(owns))["to_session_at"])  # still Claude's
        self.assertEqual((await self.row(carries))["to_session_at"], S2)

    async def test_undo_reopens_the_older_tie(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        batch = await self.answer(q, still_has=False)
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source=DM)
        got = await self.row(owns)
        self.assertEqual((got["to_session_at"], got["end_reason"]), (None, None))
        self.assertEqual(len(await self.waiting()), 1)  # (asked again: nothing was answered)

    async def test_yes_both_is_remembered_and_ends_nothing(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        self.assertIsNone(await self.answer(q, still_has=True))  # (nothing to undo)
        self.assertEqual(await self.waiting(), [])
        self.assertIsNone((await self.row(owns))["to_session_at"])

    async def test_a_third_holder_is_asked_about_again(self) -> None:
        await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        await self.answer(q, still_has=True)
        mira = await self.add("Mira", type="character")
        await self.tie(mira, "owns", S2 + 1)
        asked = {(x.older_id, x.newer_id) for x in await self.waiting()}
        self.assertEqual(asked, {(self.claude, mira), (self.belleros, mira)})

    async def test_an_answer_to_a_question_gone_meanwhile_saves_nothing(self) -> None:
        await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        await self.answer(q, still_has=True)
        with self.assertRaises(MemoryRuleError):
            await self.answer(q, still_has=False)

    async def test_only_the_dm_sees_or_answers(self) -> None:
        await self.tie(self.claude, "owns", S1)
        await self.tie(self.belleros, "owns", S2)
        (q,) = await self.waiting()
        with self.assertRaises(MemoryRuleError):
            await self.memory.hand_over_waiting(GUILD_A, self.c, source="extractor")
        with self.assertRaises(MemoryRuleError):
            await self.memory.answer_hand_over(
                GUILD_A, self.c, q, still_has=False, source="extractor"
            )

    async def test_a_hand_over_with_a_reason_keeps_it_until_reopened(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            ended = await hand_over.hand_over(w, self.brand, self.claude, at=S2, reason="sold")
        self.assertEqual(ended, [owns])
        self.assertEqual((await self.row(owns))["end_reason"], "sold")
        async with self.memory._write(GUILD_A, self.c, DM) as w:  # reopened: it holds again
            await w.update(RELATIONS, owns, {"to_session_at": None})
        self.assertIsNone((await self.row(owns))["end_reason"])

    async def test_a_hand_over_ends_only_owns_and_carries(self) -> None:
        # ("found" and every other event-like tie happened and stay true: the owner, #4115)
        knows = await self.tie(self.claude, "knows", S1, self.belleros)
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            with self.assertRaises(ValueError):
                await hand_over.hand_over(w, self.brand, self.claude, at=S2, reason=None,
                                          predicates=("found",))  # fmt: skip
            with self.assertRaises(ValueError):
                await hand_over.hand_over(w, self.brand, self.claude, at=S2, reason="burnt")
        self.assertIsNone((await self.row(knows))["to_session_at"])

    async def test_the_database_refuses_a_reason_on_a_current_tie(self) -> None:
        owns = await self.tie(self.claude, "owns", S1)
        with self.assertRaises(errors.CheckViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "UPDATE memory_relations SET end_reason = 'sold' WHERE id = %s", (owns,)
                )


def _tie(rid: str, who: str, pred: str, start: int, end: int | None = None,
         reason: str | None = None) -> Relation:  # fmt: skip
    return Relation(
        rid, who, pred, "brand", "", 1.0, CONFIRMED, DM, (), start, end, None, None, False,
        start, end_reason=reason,
    )  # fmt: skip


NAMES = {"claude": "Claude", "belleros": "Belleros"}


class TheHistoryLine(unittest.TestCase):
    def test_nothing_ended_tells_nothing(self) -> None:
        self.assertEqual(history([_tie("a", "claude", "owns", 1)], NAMES.get), [])

    def test_a_sale_names_the_buyer(self) -> None:
        ties = [_tie("a", "claude", "owns", 1, 2, "sold"), _tie("b", "belleros", "owns", 2)]
        self.assertEqual(
            history(ties, NAMES.get), ["Claude sold it to Belleros", "Belleros has it now"]
        )

    def test_no_reason_reads_used_to_have_it(self) -> None:
        ties = [_tie("a", "claude", "owns", 1, 2), _tie("b", "belleros", "owns", 2)]
        self.assertEqual(
            history(ties, NAMES.get),
            [USED_TO.format(a="Claude"), HAS_NOW.format(a="Belleros")],
        )

    def test_a_theft_says_the_victim_still_owns_it(self) -> None:
        ties = [
            _tie("a", "claude", "owns", 1),
            _tie("b", "claude", "carries", 1, 2, "stolen"),
            _tie("c", "belleros", "carries", 2),
        ]
        self.assertEqual(
            history(ties, NAMES.get),
            ["it was stolen from Claude, who still owns it", "Belleros has it now"],
        )

    def test_a_holder_the_reader_may_not_see_is_never_named(self) -> None:
        ties = [_tie("a", "claude", "owns", 1, 2, "sold"), _tie("b", "belleros", "owns", 2)]
        self.assertEqual(history(ties, {"claude": "Claude"}.get), ["Claude sold it"])

    def test_a_rejected_tie_is_no_history(self) -> None:
        gone = replace(_tie("a", "claude", "owns", 1, 2, "sold"), status="rejected")
        self.assertEqual(history([gone, _tie("b", "belleros", "owns", 2)], NAMES.get), [])

    def test_a_hidden_buyer_is_never_replaced_by_a_later_holder(self) -> None:
        # (ux-critic, #4115) Claude sold it to a hidden Belleros, who gave it to Dain
        ties = [
            _tie("a", "claude", "owns", 1, 2, "sold"),
            _tie("b", "belleros", "owns", 2, 3, "gave"),
            _tie("c", "dain", "owns", 3),
        ]
        seen = {"claude": "Claude", "dain": "Dain"}.get
        self.assertEqual(history(ties, seen), ["Claude sold it", "Dain has it now"])

    def test_a_holder_who_got_it_back_comes_last(self) -> None:
        ties = [
            _tie("a", "claude", "owns", 1, 2, "sold"),
            _tie("b", "belleros", "owns", 2, 3, "sold"),
            _tie("c", "claude", "owns", 3),
        ]
        self.assertEqual(
            history(ties, NAMES.get), ["Belleros sold it to Claude", "Claude has it now"]
        )
