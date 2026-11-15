"""The parts of a story thread (#4471, quests slice 2; design `docs/design/quests.md` §4 to §6a,
§10.2 and §13): each kind added, corrected, ticked and taken off, each one change with one Undo;
a tick is history and a correction is not; sub-steps and rewards given stand under the right part;
only the DM writes; players see only a shown part of a thread they may see; merges and kind
changes carry them; `branch_of` refuses loops and a second bigger thread. Invented names. The
pure helpers need no database; the rest needs Postgres."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import gate
from dmbot.memory import thread_parts as tp
from dmbot.memory.models import DM, MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest


def part(kind: str, **kw: object) -> tp.Part:
    base: dict[str, object] = {
        "id": "p", "thread_id": "t", "kind": kind, "words": "x", "state": tp.STATES[kind][0],
        "under_id": None, "who_id": None, "about_id": None, "amount": None, "unit": "",
        "position": None, "due": "", "terms": "", "limit_kind": "", "status": "confirmed",
        "source": "dm", "table_knows": False, "since": None, "closed": None,
    }  # fmt: skip
    base.update(kw)
    return tp.Part(**base)  # type: ignore[arg-type]


class Pure(unittest.TestCase):
    def test_an_amount_only_when_the_words_give_exactly_one(self) -> None:
        self.assertEqual(tp.read_amount("100 gold"), (100.0, "gold"))
        self.assertEqual(tp.read_amount("1,000 Gold pieces"), (1000.0, "gold"))
        self.assertEqual(tp.read_amount("a room whenever you pass"), (None, ""))
        self.assertEqual(tp.read_amount("50 now and 50 later"), (None, ""))  # (two promises)

    def test_owed_is_worked_out_in_one_unit_only(self) -> None:
        promise = part(tp.REWARD, id="r", amount=100.0, unit="gold")
        half = part(tp.REWARD_GIVEN, id="g", under_id="r", amount=50.0, unit="gold")
        self.assertEqual(tp.owed(promise, [half]), 50.0)
        self.assertEqual(tp.owed(promise, [half, half]), 0.0)
        other = part(tp.REWARD_GIVEN, id="h", under_id="r", amount=5.0, unit="silver")
        self.assertIsNone(tp.owed(promise, [half, other]))  # (can't be counted: the DM says)
        self.assertIsNone(tp.owed(part(tp.REWARD, id="f"), [half]))  # (a favour)

    def test_done_counts_standing_objectives_only(self) -> None:
        parts = [
            part(tp.OBJECTIVE, state="done"),
            part(tp.OBJECTIVE),
            part(tp.OBJECTIVE, state="to_do", closed=5),  # (history)
            part(tp.COMPLICATION),
        ]
        self.assertEqual(tp.done_count(parts), (1, 2))


class _Thread(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.quest = await self.add("Hlin's job: Sephek", type="quest", status="confirmed")
        self.hlin = await self.add("Hlin Trollbane", status="confirmed")
        self.dogs = await self.add("The sled dogs", type="item", status="confirmed")

    async def put(self, kind: str, words: str, **kw: object) -> tp.Part:
        done = await self.memory.add_thread_part(
            GUILD_A,
            self.c,
            self.quest,
            kind,
            words,
            source=DM,
            **kw,  # type: ignore[arg-type]
        )
        return done.value

    async def standing(self, viewer: gate.Viewer | None = None) -> list[tp.Part]:
        return await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], viewer or gate.Viewer.the_dm()
        )


class EachKind(_Thread):
    async def test_add_correct_tick_and_remove_are_each_one_undo(self) -> None:
        words = {
            tp.OBJECTIVE: "find where Sephek sleeps", tp.COMPLICATION: "the road north is cut",
            tp.REWARD: "100 gold", tp.REWARD_GIVEN: "50 gold", tp.HELP: "two sleds and eight dogs",
            tp.TIME_LIMIT: "before the new moon",
        }  # fmt: skip
        for kind, said in words.items():
            with self.subTest(kind):
                before = await self.snapshot()
                added = await self.memory.add_thread_part(
                    GUILD_A, self.c, self.quest, kind, said, source=DM
                )
                got = added.value
                self.assertEqual((got.state, got.status, got.table_knows),
                                 (tp.STATES[kind][0], "confirmed", False))  # fmt: skip
                fixed = await self.memory.correct_thread_part(
                    GUILD_A, self.c, got.id, words=said + " (as said)", source=DM
                )
                self.assertEqual(fixed.value.id, got.id)  # (a correction is never history)
                if len(tp.STATES[kind]) > 1:
                    ticked = await self.memory.set_thread_part_state(
                        GUILD_A, self.c, got.id, tp.STATES[kind][1], source=DM
                    )
                    self.assertNotEqual(ticked.value.id, got.id)  # (a tick is history)
                    with_history = await self.memory.parts_of_threads(
                        GUILD_A, self.c, [self.quest], gate.Viewer.the_dm(), history=True
                    )
                    self.assertEqual(
                        sorted(p.state for p in with_history if p.kind == kind),
                        sorted(tp.STATES[kind][:2]),
                    )
                    assert ticked.batch is not None
                    await self.memory.undo(GUILD_A, self.c, ticked.batch, source=DM)
                    (back,) = [p for p in await self.standing() if p.kind == kind]
                    self.assertEqual((back.id, back.state), (got.id, tp.STATES[kind][0]))
                removed = await self.memory.remove_thread_part(GUILD_A, self.c, got.id, source=DM)
                self.assertEqual([p for p in await self.standing() if p.kind == kind], [])
                assert removed.batch is not None and fixed.batch is not None
                assert added.batch is not None
                for batch in (removed.batch, fixed.batch, added.batch):
                    await self.memory.undo(GUILD_A, self.c, batch, source=DM)
                self.assertEqual(await self.snapshot(), before)

    async def test_the_same_part_twice_and_a_bad_state_are_refused(self) -> None:
        await self.put(tp.OBJECTIVE, "Find Sephek")
        with self.assertRaisesRegex(MemoryRuleError, tp.SAME_PART):
            await self.put(tp.OBJECTIVE, "find  sephek")
        mine = await self.put(tp.COMPLICATION, "a storm")
        with self.assertRaisesRegex(MemoryRuleError, tp.NOT_A_STATE):
            await self.memory.set_thread_part_state(GUILD_A, self.c, mine.id, "done", source=DM)
        lent = await self.put(tp.HELP, "the sled dogs", state="lent", who_id=self.hlin)
        self.assertEqual(lent.state, "lent")  # (help may start lent; nothing else starts later)
        with self.assertRaisesRegex(MemoryRuleError, tp.NOT_A_STATE):
            await self.put(tp.REWARD, "a room", state="settled")

    async def test_only_the_dm_writes_and_only_on_a_kept_thread(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, tp.ONLY_DM):
            await self.memory.add_thread_part(
                GUILD_A, self.c, self.quest, tp.OBJECTIVE, "x", source="background"
            )
        waiting = await self.add("Someone's errand", type="quest", status="proposed")
        npc = await self.add("Not a thread", status="confirmed")
        for target in (waiting, npc):
            with self.assertRaisesRegex(MemoryRuleError, tp.NOT_A_THREAD):
                await self.memory.add_thread_part(
                    GUILD_A, self.c, target, tp.OBJECTIVE, "x", source=DM
                )
        mine = await self.put(tp.OBJECTIVE, "x")
        with self.assertRaisesRegex(MemoryRuleError, tp.ONLY_DM):
            await self.memory.remove_thread_part(GUILD_A, self.c, mine.id, source="background")


class CorrectOrHistory(_Thread):
    """Supervisor's boundary on #4471: rewording is corrected in place; a new amount, due time,
    loan terms or soft-or-hard is a change in the story, so the old row stays as history."""

    async def test_rewording_in_place_and_a_new_amount_as_history(self) -> None:
        promise = await self.put(tp.REWARD, "100 gold", who_id=self.hlin)
        same = await self.memory.correct_thread_part(
            GUILD_A, self.c, promise.id, words="100 gold coins", source=DM
        )
        self.assertEqual((same.value.id, same.value.amount), (promise.id, 100.0))
        more = await self.memory.correct_thread_part(
            GUILD_A, self.c, promise.id, words="150 gold", source=DM
        )
        self.assertNotEqual(more.value.id, promise.id)
        self.assertEqual(more.value.amount, 150.0)
        later = await self.memory.correct_thread_part(
            GUILD_A, self.c, more.value.id, due=tp.AFTERWARDS, source=DM
        )
        self.assertNotEqual(later.value.id, more.value.id)
        # the DM says new words changed in the story: the old ones stay as history
        told = await self.memory.correct_thread_part(
            GUILD_A, self.c, later.value.id, words="150 gold and a horse", in_story=True,
            source=DM,
        )  # fmt: skip
        self.assertNotEqual(told.value.id, later.value.id)
        later = told
        history = await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], gate.Viewer.the_dm(), history=True
        )
        self.assertEqual(len(history), 4)  # (three closed rows and the one standing)
        self.assertEqual([p.id for p in await self.standing()], [later.value.id])


class Under(_Thread):
    async def test_sub_steps_one_level_deep_and_they_follow_a_tick(self) -> None:
        top = await self.put(tp.OBJECTIVE, "find Sephek")
        step = await self.put(tp.OBJECTIVE, "ask at the Lucky Liar", under_id=top.id)
        self.assertEqual((top.position, step.position), (0, 0))
        with self.assertRaisesRegex(MemoryRuleError, tp.WRONG_UNDER):
            await self.put(tp.OBJECTIVE, "deeper", under_id=step.id)
        done = await self.memory.set_thread_part_state(GUILD_A, self.c, top.id, "done", source=DM)
        (moved,) = [p for p in await self.standing() if p.words == "ask at the Lucky Liar"]
        self.assertEqual(moved.under_id, done.value.id)
        # taking the objective off leaves its sub-step at the top
        await self.memory.remove_thread_part(GUILD_A, self.c, done.value.id, source=DM)
        (alone,) = await self.standing()
        self.assertIsNone(alone.under_id)

    async def test_a_reward_given_counts_against_its_promise(self) -> None:
        promise = await self.put(tp.REWARD, "100 gold", who_id=self.hlin, due=tp.AFTERWARDS)
        self.assertEqual((promise.amount, promise.unit, promise.due), (100.0, "gold", "afterwards"))
        given = await self.put(tp.REWARD_GIVEN, "50 gold", under_id=promise.id, who_id=self.hlin)
        self.assertEqual(tp.owed(promise, [given]), 50.0)
        objective = await self.put(tp.OBJECTIVE, "find Sephek")
        with self.assertRaisesRegex(MemoryRuleError, tp.WRONG_PROMISE):
            await self.put(tp.REWARD_GIVEN, "a ring", under_id=objective.id)
        with self.assertRaisesRegex(MemoryRuleError, tp.NOT_WHO):
            await self.put(tp.REWARD, "a horse", who_id=self.dogs)  # (an item gives nothing)
        # a given reward never promised is recorded, and never owed
        free = await self.put(tp.REWARD_GIVEN, "a warm meal", who_id=self.hlin)
        self.assertIsNone(free.under_id)


class Players(_Thread):
    async def test_a_player_sees_only_a_shown_part_of_a_thread_they_may_see(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status="confirmed")
        party = gate.Viewer.player([pc])
        # (a thread made in Discord starts hidden from players, and so does this giver)
        for hide in (self.quest, self.hlin):
            await self.memory.set_hidden_from_players(GUILD_A, self.c, hide, True, source=DM)
        reward = await self.put(tp.REWARD, "100 gold", who_id=self.hlin)
        await self.memory.show_thread_part(GUILD_A, self.c, reward.id, True, source=DM)
        self.assertEqual(await self.standing(party), [])  # (the thread is hidden from them)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.quest, False, source=DM)
        hidden = await self.put(tp.COMPLICATION, "Sephek is no mortal")
        (seen,) = await self.standing(party)  # (the DM's own typing starts hidden)
        self.assertEqual((seen.id, seen.who_id), (reward.id, None))  # (Hlin isn't shown to them)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.hlin, False, source=DM)
        (seen,) = await self.standing(party)
        self.assertEqual(seen.who_id, self.hlin)
        await self.memory.show_thread_part(GUILD_A, self.c, hidden.id, True, source=DM)
        self.assertEqual(len(await self.standing(party)), 2)


class MergesAndKinds(_Thread):
    async def test_a_merged_thread_brings_its_parts_and_a_double_folds(self) -> None:
        other = await self.add("Hlin's contract", type="quest", status="confirmed")
        await self.put(tp.OBJECTIVE, "find Sephek")
        top = await self.memory.add_thread_part(
            GUILD_A, self.c, other, tp.OBJECTIVE, "Find Sephek", source=DM
        )
        await self.memory.add_thread_part(
            GUILD_A, self.c, other, tp.OBJECTIVE, "ask around", source=DM,
            under_id=top.value.id,
        )  # fmt: skip
        await self.memory.add_thread_part(GUILD_A, self.c, other, tp.REWARD, "100 gold", source=DM)
        await self.memory.merge(GUILD_A, self.c, self.quest, other, source=DM, dm_said_same=True)
        got = await self.standing()
        self.assertEqual(sorted(p.words for p in got), ["100 gold", "ask around", "find Sephek"])
        (kept,) = [p for p in got if p.words == "find Sephek"]
        (step,) = [p for p in got if p.words == "ask around"]
        self.assertEqual(step.under_id, kept.id)  # (the double folded into the kept one)

    async def test_no_longer_a_thread_drops_its_parts_and_undo_brings_them(self) -> None:
        await self.put(tp.OBJECTIVE, "find Sephek")
        changed = await self.memory.set_entity_type(GUILD_A, self.c, self.quest, "npc", source=DM)
        self.assertEqual(await self.standing(), [])
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch, source=DM)
        self.assertEqual([p.words for p in await self.standing()], ["find Sephek"])


class BranchOf(_Thread):
    async def test_one_bigger_thread_and_no_loop(self) -> None:
        wit = await self.add("Who killed Wit?", type="quest", status="confirmed")
        search = await self.add("Cerric's search", type="quest", status="confirmed")
        made = await self.memory.branch_of(GUILD_A, self.c, wit, search, source=DM)
        self.assertFalse(made.value.secret)  # (players see it when both threads are shown)
        with self.assertRaisesRegex(MemoryRuleError, "already a branch of another"):
            await self.memory.branch_of(GUILD_A, self.c, wit, self.quest, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, "loop"):
            await self.memory.branch_of(GUILD_A, self.c, search, wit, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, "Only a story thread"):
            await self.memory.branch_of(GUILD_A, self.c, self.hlin, search, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.branch_of(GUILD_A, self.c, self.quest, search, source="background")

    async def test_move_it_and_not_a_branch_each_one_undo(self) -> None:
        wit = await self.add("Who killed Wit?", type="quest", status="confirmed")
        search = await self.add("Cerric's search", type="quest", status="confirmed")
        await self.memory.branch_of(GUILD_A, self.c, wit, search, source=DM)
        moved = await self.memory.branch_of(GUILD_A, self.c, wit, self.quest, source=DM, move=True)
        self.assertEqual(await self.memory.bigger_thread(GUILD_A, self.c, wit), self.quest)
        assert moved.batch is not None
        await self.memory.undo(GUILD_A, self.c, moved.batch, source=DM)
        self.assertEqual(await self.memory.bigger_thread(GUILD_A, self.c, wit), search)
        off = await self.memory.not_a_branch(GUILD_A, self.c, wit, source=DM)
        self.assertTrue(off.value)
        self.assertIsNone(await self.memory.bigger_thread(GUILD_A, self.c, wit))

    async def test_a_merge_giving_a_thread_two_bigger_ones_is_refused(self) -> None:
        a = await self.add("Side errand", type="quest", status="confirmed")
        b = await self.add("Side errand too", type="quest", status="confirmed")
        big1 = await self.add("Big one", type="quest", status="confirmed")
        await self.memory.branch_of(GUILD_A, self.c, a, big1, source=DM)
        await self.memory.branch_of(GUILD_A, self.c, b, self.quest, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, "can't be joined yet"):
            await self.memory.merge(GUILD_A, self.c, b, a, source=DM, dm_said_same=True)


class BranchesOnRestore(unittest.TestCase):
    """A backup with two bigger threads for one, or a loop of branches, is refused (Supervisor's
    ruling on #4471: checked on restore as on add and merge)."""

    def check(self, *pairs: tuple[str, str]) -> None:
        from dmbot.memory.backup import _check_branches
        from dmbot.memory.ontology import CORE_PREDICATES, CORE_TYPES, Ontology

        onto = Ontology.build(CORE_TYPES, CORE_PREDICATES)
        ids = {i for pair in pairs for i in pair}
        rows: dict[str, list[dict[str, Any]]] = {
            "entity": [{"id": i, "type": "quest"} for i in sorted(ids)],
            "relation": [
                {"subject_id": a, "object_id": b, "predicate": "branch_of",
                 "status": "confirmed", "to_session_at": None}
                for a, b in pairs
            ],
        }  # fmt: skip
        _check_branches(rows, onto)

    def test_one_bigger_thread_and_no_loop(self) -> None:
        from dmbot.campaigns.models import CampaignError

        self.check(("a", "b"), ("c", "b"), ("b", "d"))  # (fine: a tree)
        with self.assertRaises(CampaignError):
            self.check(("a", "b"), ("a", "c"))
        with self.assertRaises(CampaignError):
            self.check(("a", "b"), ("b", "a"))


class ReviewersFindings(_Thread):
    """The reviewer's four on #4471: sub-steps stay one level deep (a backup always loads), a merge
    into something that isn't a thread drops its parts, a kind change takes a branch link off, and
    👁️ / 🙈 act on one part's own history only."""

    async def loads(self) -> None:
        from dmbot.memory.backup import MemorySection

        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        section.check(rows)  # (raises when the file would not restore)

    async def test_an_objective_anything_stood_under_never_goes_under_another(self) -> None:
        top = await self.put(tp.OBJECTIVE, "find Sephek")
        other = await self.put(tp.OBJECTIVE, "find the ship")
        step = await self.put(tp.OBJECTIVE, "ask around", under_id=top.id)
        ticked = await self.memory.set_thread_part_state(
            GUILD_A, self.c, step.id, "done", source=DM
        )
        await self.memory.remove_thread_part(GUILD_A, self.c, ticked.value.id, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, tp.WRONG_UNDER):
            await self.memory.correct_thread_part(
                GUILD_A, self.c, top.id, under_id=other.id, source=DM
            )
        await self.loads()

    async def test_a_merged_top_step_matching_a_sub_step_brings_its_steps_to_the_top(self) -> None:
        other = await self.add("Hlin's contract", type="quest", status="confirmed")
        top = await self.put(tp.OBJECTIVE, "find the ship")
        await self.put(tp.OBJECTIVE, "find Sephek", under_id=top.id)
        theirs = await self.memory.add_thread_part(
            GUILD_A, self.c, other, tp.OBJECTIVE, "Find Sephek", source=DM
        )
        await self.memory.add_thread_part(
            GUILD_A, self.c, other, tp.OBJECTIVE, "ask around", source=DM,
            under_id=theirs.value.id,
        )  # fmt: skip
        await self.memory.merge(GUILD_A, self.c, self.quest, other, source=DM, dm_said_same=True)
        (moved,) = [p for p in await self.standing() if p.words == "ask around"]
        self.assertIsNone(moved.under_id)
        await self.loads()

    async def test_merged_into_something_that_is_no_thread_drops_its_parts(self) -> None:
        event = await self.add("The ambush", type="event", status="confirmed")
        await self.put(tp.OBJECTIVE, "find Sephek")
        await self.memory.merge(GUILD_A, self.c, event, self.quest, source=DM, dm_said_same=True)
        got = await self.memory.parts_of_threads(
            GUILD_A, self.c, [event, self.quest], gate.Viewer.the_dm(), history=True
        )
        self.assertEqual(got, [])
        await self.loads()

    async def test_no_longer_a_thread_takes_its_branch_link_off(self) -> None:
        bigger = await self.add("Cerric's search", type="quest", status="confirmed")
        await self.memory.branch_of(GUILD_A, self.c, self.quest, bigger, source=DM)
        await self.memory.set_entity_type(GUILD_A, self.c, bigger, "event", source=DM)
        self.assertIsNone(await self.memory.bigger_thread(GUILD_A, self.c, self.quest))
        await self.loads()

    async def test_show_and_hide_act_on_one_parts_own_history(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status="confirmed")
        party = gate.Viewer.player([pc])
        first = await self.put(tp.OBJECTIVE, "find the cult leader")
        await self.memory.show_thread_part(GUILD_A, self.c, first.id, True, source=DM)
        done = await self.memory.set_thread_part_state(GUILD_A, self.c, first.id, "done", source=DM)
        told = await self.memory.correct_thread_part(
            GUILD_A, self.c, done.value.id, words="find the cult's leader", in_story=True,
            source=DM,
        )  # fmt: skip
        await self.memory.show_thread_part(GUILD_A, self.c, told.value.id, False, source=DM)
        seen = await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], party, history=True
        )
        self.assertEqual(seen, [])  # (its whole history hid with it)
        # a new part with the first one's words is its own: showing it shows nothing older
        await self.memory.remove_thread_part(GUILD_A, self.c, told.value.id, source=DM)
        again = await self.put(tp.OBJECTIVE, "find the cult leader")
        await self.memory.show_thread_part(GUILD_A, self.c, again.id, True, source=DM)
        seen = await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], party, history=True
        )
        self.assertEqual([p.id for p in seen], [again.id])

    async def test_taking_a_part_off_takes_its_history_with_it(self) -> None:
        step = await self.put(tp.OBJECTIVE, "find Sephek")
        done = await self.memory.set_thread_part_state(GUILD_A, self.c, step.id, "done", source=DM)
        await self.memory.remove_thread_part(GUILD_A, self.c, done.value.id, source=DM)
        got = await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], gate.Viewer.the_dm(), history=True
        )
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
