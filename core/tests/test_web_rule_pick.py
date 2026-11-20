"""The website's Pick changes on a followed house-rules set and its Undo (#4841 C2, migration 0562):
leaving a change out ends the follow and keeps the campaign's own copy, keeping every change is
refused (the page never takes an update itself), a newer version since the page was read is
`changed`, and the Undo (a day-long handle, this DM's own and the newest only) puts the follow and
its content back exactly. Postgres. Invented names."""

from __future__ import annotations

import json
import time
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.store import MemoryStore
from dmbot.rules import rule_sets
from dmbot.rules.web_updates import RuleTools, TakeStore
from dmbot.web import relationships, rule_updates
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_rule_sharing import SET_A, SharingBase

OLD = {"rules": [{"number": 1, "rule": "Crits double", "instead": None}], "link": None}
NEW = {
    "rules": [
        {"number": 1, "rule": "Crits double the dice", "instead": None},
        {"number": 2, "rule": "No flanking", "instead": None},
    ],
    "link": None,
}


class PickingChanges(SharingBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.publish(self.two.id, SET_A, "Frost Rules")
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(NEW), SET_A,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, followed_by, followed_at)"
            " VALUES (%s, %s, %s, 'house_rules', 'Frost Rules', 'Two', 1, %s::jsonb, true, %s, 5)",
            THURSDAY.id, self.one.id, SET_A, json.dumps(OLD), ALICE.id,
        )  # fmt: skip
        self.clock = [float(int(time.time()))]
        self.sets = rule_sets.RuleSetStore(self.db)
        self.takes = TakeStore(self.db, clock=lambda: self.clock[0])
        self.settled: list[str] = []

        async def settle(guild_id: int, campaign_id: str) -> None:
            self.settled.append(campaign_id)

        self.tools = RuleTools(self.sets, MemoryStore(self.db), self.takes, settle)
        self.actions = wa.ActionStore(self.db)

    async def drain(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(THURSDAY.id)) is not None:
            outcome = await wa.apply(
                MemoryStore(self.db), THURSDAY.id, {ALICE.id}, action,
                actions=self.actions, rules=self.tools,
            )  # fmt: skip
            await self.actions.finish(
                THURSDAY.id, action, error=outcome.error, result_id=outcome.result_id,
                result_counts=outcome.counts, result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def pick(self, kept: list[int], version: int = 2) -> wa.Outcome:
        await rule_updates.queue_pick(
            self.web, self.session, self.cref(), {"version": version, "kept": kept}
        )
        (done,) = await self.drain()
        return done

    async def undo(self, take: str, session: Any = None) -> wa.Outcome:
        await rule_updates.queue_undo(self.web, session or self.session, self.cref(), take)
        (done,) = await self.drain()
        return done

    async def follow_row(self) -> dict[str, Any] | None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT version, content, updating FROM rule_set_follows WHERE campaign_id = %s",
                (self.one.id,),
            )
            return await cur.fetchone()

    async def own_rules(self) -> list[int]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT number FROM house_rules WHERE campaign_id = %s ORDER BY number",
                (self.one.id,),
            )
            return [int(r["number"]) for r in await cur.fetchall()]

    async def test_leaving_a_change_out_ends_the_follow_and_keeps_the_campaigns_own_copy(
        self,
    ) -> None:
        done = await self.pick([0])  # keep the changed rule 1, leave out the new rule 2
        self.assertEqual(done.error, "")
        self.assertEqual(done.counts, "picked:1 left:1")
        self.assertIsNone(await self.follow_row())  # (no longer following)
        self.assertEqual(await self.own_rules(), [1])  # the campaign's own copy
        self.assertTrue(done.result_id.isdigit())

    async def test_the_undo_puts_the_follow_back_exactly(self) -> None:
        before = await self.follow_row()
        done = await self.pick([0])
        undone = await self.undo(done.result_id)
        self.assertEqual(undone.error, "")
        after = await self.follow_row()
        assert before is not None and after is not None
        self.assertEqual(
            (after["version"], after["content"]), (before["version"], before["content"])
        )
        self.assertEqual(await self.own_rules(), [])
        self.assertEqual(
            await self.takes.newest_id(THURSDAY.id, self.one.id), None
        )  # (the handle goes)

    async def test_keeping_every_change_is_refused_because_the_page_never_takes_an_update(
        self,
    ) -> None:
        done = await self.pick([0, 1])
        self.assertEqual(done.error, wa.NOTHING_LEFT_OUT)
        row = await self.follow_row()
        assert row is not None
        self.assertEqual(row["version"], 1)  # (nothing was taken)

    async def test_a_newer_version_since_the_page_was_read_is_changed(self) -> None:
        done = await self.pick([0], version=1)  # the page read version 1's offer; 2 is on offer
        self.assertEqual(done.error, wa.CHANGED)

    async def test_a_pick_naming_changes_that_are_not_there_is_gone_and_changes_nothing(
        self,
    ) -> None:
        done = await self.pick([7])
        self.assertEqual(done.error, wa.GONE)
        row = await self.follow_row()
        assert row is not None
        self.assertEqual(row["version"], 1)

    async def test_another_dms_undo_is_refused_and_so_is_one_for_a_made_up_id(self) -> None:
        done = await self.pick([0])
        await self.sql("UPDATE name_actions SET user_id = %s", BOB.id)  # (picked by someone else)
        self.assertEqual((await self.undo(done.result_id)).error, wa.GONE)
        self.assertEqual((await self.undo("999999")).error, wa.GONE)

    async def test_an_undo_after_a_newer_take_or_after_a_day_is_too_late(self) -> None:
        done = await self.pick([0])
        saved = await self.takes.get(THURSDAY.id, self.one.id, int(done.result_id))
        assert saved is not None
        newer = await self.takes.save(THURSDAY.id, self.one.id, saved.action_id, saved.taken, None)
        self.assertGreater(newer, int(done.result_id))
        self.assertEqual((await self.undo(done.result_id)).error, wa.TOO_LATE)  # (not the newest)
        await self.takes.delete(THURSDAY.id, self.one.id, newer)
        self.clock[0] += 24 * 60 * 60 + 5
        self.assertEqual((await self.undo(done.result_id)).error, wa.TOO_LATE)  # (a day later)
        self.assertIsNone(await self.follow_row())  # (and nothing was undone)

    async def test_the_sweep_removes_handles_older_than_a_day(self) -> None:
        done = await self.pick([0])
        self.assertEqual(await self.takes.sweep(THURSDAY.id), 0)
        self.clock[0] += 24 * 60 * 60 + 5
        self.assertEqual(await self.takes.sweep(THURSDAY.id), 1)
        self.assertIsNone(await self.takes.get(THURSDAY.id, self.one.id, int(done.result_id)))

    async def test_the_answer_the_page_polls_carries_the_counts_and_the_handle(self) -> None:
        answer = await rule_updates.queue_pick(
            self.web, self.session, self.cref(), {"version": 2, "kept": [0]}
        )
        (done,) = await self.drain()
        read = await relationships.read_answer(self.web, self.session, self.cref(), answer)
        self.assertEqual((read["state"], read["kept"], read["left"]), ("done", 1, 1))
        self.assertEqual(read["take"], int(done.result_id))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [
            {"version": "2", "kept": [0]},
            {"version": 2, "kept": [60]},
            {"version": 2, "kept": "x"},
            {"version": True, "kept": []},
            5,
        ]
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await rule_updates.queue_pick(self.web, self.session, self.cref(), body)
            self.assertEqual(err.exception.code, "bad_action")
        with self.assertRaises(RecapError) as err2:
            await rule_updates.queue_undo(self.web, self.session, self.cref(), "x1")
        self.assertEqual(err2.exception.code, "not_found")
        self.assertIsNone(await self.actions.claim_next(THURSDAY.id))

    async def test_only_a_dm_queues_a_pick(self) -> None:
        bob = await self.signed(BOB)
        with self.assertRaises(RecapError) as err:
            await rule_updates.queue_pick(self.web, bob, self.cref(), {"version": 2, "kept": [0]})
        self.assertEqual(err.exception.code, "not_allowed")
