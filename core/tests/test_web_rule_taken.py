"""The website's Undo of an update DMbot already took (#4841 C3, migration 0565): only a DM reads
what was taken, it gives core's own lines for what changed (`rules.set_diff`, as Discord's note),
nothing shows when no update was taken or it was undone, and the Undo puts the copy before back
only while the campaign is still on the version the page showed. Postgres. Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.store import MemoryStore
from dmbot.rules import rule_sets, set_diff
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


class TakenUpdate(SharingBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, previous_version, previous_content, updating,"
            " followed_by, followed_at) VALUES (%s, %s, %s, 'house_rules', 'Frost Rules', 'Two',"
            " 2, %s::jsonb, 1, %s::jsonb, true, %s, 5)",
            THURSDAY.id, self.one.id, SET_A, json.dumps(NEW), json.dumps(OLD), ALICE.id,
        )  # fmt: skip
        self.settled: list[str] = []

        async def settle(guild_id: int, campaign_id: str) -> None:
            self.settled.append(campaign_id)

        self.tools = RuleTools(
            rule_sets.RuleSetStore(self.db), MemoryStore(self.db), TakeStore(self.db), settle
        )
        self.actions = wa.ActionStore(self.db)

    async def taken(self, session: Any = None) -> Any:
        got = await rule_updates.read_taken(self.web, session or self.session, self.cref())
        return got["taken"]

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

    async def undo(self, version: int) -> wa.Outcome:
        await rule_updates.queue_undo_last(
            self.web, self.session, self.cref(), {"version": version}
        )
        (done,) = await self.drain()
        return done

    async def test_a_taken_update_gives_core_s_own_lines(self) -> None:
        rules = set_diff.diff(OLD, NEW)
        self.assertEqual(
            await self.taken(),
            {
                "name": "Frost Rules", "publisher": "Two", "from": 1, "to": 2,
                "words": set_diff.count_words(rules),
                "lines": [set_diff.describe(c) for c in rules], "more": 0,
            },
        )  # fmt: skip

    async def test_nothing_shows_when_nothing_was_taken_and_a_player_is_refused(self) -> None:
        with self.assertRaises(RecapError) as err:
            await self.taken(await self.signed(BOB))
        self.assertEqual(err.exception.code, "not_allowed")
        await self.sql(
            "UPDATE rule_set_follows SET previous_version = NULL, previous_content = NULL"
        )
        self.assertIsNone(await self.taken())

    async def test_the_undo_puts_the_copy_before_back_and_asks_nothing_again(self) -> None:
        done = await self.undo(2)
        self.assertEqual((done.error, done.counts), ("", "back:1"))
        self.assertEqual(self.settled, [self.one.id])
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT version, content, previous_version, declined_version"
                " FROM rule_set_follows WHERE campaign_id = %s",
                (self.one.id,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["version"], row["content"]), (1, OLD))
        self.assertEqual((row["previous_version"], row["declined_version"]), (None, 2))
        self.assertIsNone(await self.taken())  # (and a second press is too late)
        again = await self.undo(2)
        self.assertEqual(again.error, "too_late")

    async def test_an_old_page_cannot_undo_a_newer_take(self) -> None:
        done = await self.undo(1)  # the page showed a take that is no longer the campaign's
        self.assertEqual(done.error, "too_late")
        self.assertEqual((await self.taken())["to"], 2)

    async def test_the_answer_says_which_version_it_went_back_to(self) -> None:
        answer = await rule_updates.queue_undo_last(
            self.web, self.session, self.cref(), {"version": 2}
        )
        await self.drain()
        read = await relationships.read_answer(self.web, self.session, self.cref(), answer)
        self.assertEqual((read["state"], read["back"], read["error"]), ("done", 1, ""))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        for body in [{}, {"version": "2"}, {"version": True}, {"version": -1}, 5]:
            with self.assertRaises(RecapError) as err:
                await rule_updates.queue_undo_last(self.web, self.session, self.cref(), body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertEqual(await self.drain(), [])

    async def test_another_member_who_is_not_a_dm_is_refused_at_the_door(self) -> None:
        with self.assertRaises(RecapError):
            await rule_updates.queue_undo_last(
                self.web, await self.signed(BOB), self.cref(), {"version": 2}
            )

    async def test_a_long_list_of_changes_is_cut_and_counted(self) -> None:
        many = {
            "rules": [
                {"number": n, "rule": f"Rule {n}", "instead": None}
                for n in range(1, rule_updates.MAX_LINES + 8)
            ],
            "link": None,
        }
        await self.sql("UPDATE rule_set_follows SET content = %s::jsonb", json.dumps(many))
        got = await self.taken()
        self.assertEqual((len(got["lines"]), got["more"]), (rule_updates.MAX_LINES, 7 + 1 - 1))
