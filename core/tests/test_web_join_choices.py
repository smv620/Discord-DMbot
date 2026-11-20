"""The website's "Let me choose" for the join question (#4799, migration 0569): the bot works the
choices out for a DM and keeps them (ids, counts, thread names); the page reads them through a
DM-only door that gives a row only to the DM it was made for; a pick goes to `join_choose`, which
checks again; the Undo is the existing `join-undo` and takes back only a pick this DM made; the
rows are swept after an hour. Postgres. Invented names."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import Written
from dmbot.memory.store import JoinChoices
from dmbot.memory.web_join_choices import JoinChoicesStore
from dmbot.web import join_choices, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web

GROUP = "a" * 32
THREAD = "b" * 32


class ChoosingOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.kept_store = JoinChoicesStore(self.db)
        self.tavin = await self.add("Tavin", "character", role="player_character")
        self.calls: list[tuple[int, str, str, int]] = []
        self.picks: list[dict[str, Any]] = []

    def worked_out(self) -> JoinChoices:
        return JoinChoices(
            members=[self.tavin],
            sessions=[(5_000, 3, {self.tavin: 14})],
            threads=[(THREAD, "The drowned bell", {self.tavin: 6})],
            sessions_in_all=1,
            threads_in_all=1,
            nobody_caught_up=False,
        )

    async def save_row(self, who: int = ALICE.id) -> None:
        await self.kept_store.save(self.g, self.one.id, GROUP, who, self.worked_out())

    async def bob_session(self) -> Any:
        who: Any = BOB
        found = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert found is not None
        return found

    async def read(self, who: Any = None) -> dict[str, Any]:
        return await join_choices.read(self.web, who or self.session, self.cref, GROUP)

    async def loop(self, memory: Any = None) -> list[wa.Outcome]:
        async def work_out(guild: int, cid: str, group: str, user: int) -> bool:
            self.calls.append((guild, cid, group, user))
            await self.save_row(user)
            return True

        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                memory or self.memory, self.g, {ALICE.id}, action,
                actions=self.actions, join_choices=work_out,
            )  # fmt: skip
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    def stub_memory(
        self, told: dict[str, int] | None, batch: int | None, first: bool = True
    ) -> Any:
        async def join_choose(guild: int, cid: str, group: str, **pick: Any) -> Written[Any]:
            self.picks.append({"group": group, **pick})
            return Written((told or {}, first), batch)

        return SimpleNamespace(join_choose=join_choose)

    async def test_nothing_is_kept_until_the_bot_has_worked_it_out(self) -> None:
        self.assertEqual(await self.read(), {"madeAt": None, "choices": None})

    async def test_the_kept_choices_come_back_with_the_characters_names(self) -> None:
        await self.save_row()
        got = await self.read()
        self.assertEqual(
            got["choices"],
            {
                "members": [{"id": self.tavin, "name": "Tavin"}],
                "sessions": [{"at": 5_000, "number": 3, "counts": {self.tavin: 14}}],
                "threads": [{"id": THREAD, "name": "The drowned bell", "counts": {self.tavin: 6}}],
                "sessionsInAll": 1, "threadsInAll": 1, "nobody": False,
            },
        )  # fmt: skip
        self.assertGreater(got["madeAt"], 0)

    async def test_a_new_read_replaces_the_row_and_only_its_dm_gets_it(
        self,
    ) -> None:
        await self.save_row()
        await self.save_row()  # (replaced, not added)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_join_choices")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)
        # a co-DM gets nothing of Alice's row, and a player is refused
        await self.sql(
            "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
            self.g, self.one.id, BOB.id,
        )  # fmt: skip
        bob = await self.bob_session()
        self.assertEqual(await self.read(bob), {"madeAt": None, "choices": None})
        await self.sql("DELETE FROM campaign_dms WHERE user_id = %s", BOB.id)
        with self.assertRaises(RecapError) as err:
            await self.read(bob)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_asking_works_the_choices_out_for_that_dm(
        self,
    ) -> None:
        answer = await join_choices.queue_read(self.web, self.session, self.cref, {"group": GROUP})
        (done,) = await self.loop()
        self.assertEqual(done.error, "")
        self.assertEqual(self.calls, [(self.g, self.one.id, GROUP, ALICE.id)])
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((read["state"], read["error"]), ("done", ""))
        self.assertEqual(len((await self.read())["choices"]["sessions"]), 1)
        for body in ({"group": "nope"}, {}, 5):
            with self.assertRaises(RecapError) as err:
                await join_choices.queue_read(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")

    async def test_a_pick_gives_what_the_bot_says_and_its_undo_is_the_dms_own(self) -> None:
        stub = self.stub_memory({self.tavin: 14}, batch=77, first=True)
        answer = await join_choices.queue_pick(
            self.web, self.session, self.cref, {"group": GROUP, "session": 5_000}
        )
        (done,) = await self.loop(stub)
        self.assertEqual(self.picks, [{"group": GROUP, "session_at": 5_000}])
        self.assertEqual((done.error, done.batches), ("", (77,)))
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual(read["chose"], [{"id": self.tavin[:8], "count": 14}])
        self.assertEqual((read["first"], read["choseMore"]), (True, 0))
        self.assertEqual(read["batch"], 77)
        # (the page's Undo is the existing one, which accepts this DM's own done pick)
        self.assertTrue(await self.actions.join_batch_made(self.g, self.one.id, ALICE.id, 77))
        self.assertFalse(await self.actions.join_batch_made(self.g, self.one.id, BOB.id, 77))
        self.assertFalse(await self.actions.join_batch_made(self.g, self.one.id, ALICE.id, 78))

    async def test_a_thread_pick_nothing_left_and_a_refused_pick(
        self,
    ) -> None:
        await join_choices.queue_pick(
            self.web, self.session, self.cref, {"group": GROUP, "thread": THREAD}
        )
        (done,) = await self.loop(self.stub_memory({self.tavin: 6}, batch=5, first=False))
        self.assertEqual(self.picks[-1], {"group": GROUP, "thread_id": THREAD})
        self.assertEqual(done.error, "")
        await join_choices.queue_pick(
            self.web, self.session, self.cref, {"group": GROUP, "session": 5_000}
        )
        (empty,) = await self.loop(self.stub_memory({}, batch=None))
        self.assertEqual(empty.error, wa.NOTHING_LEFT)
        # the real store: no such question is "gone", never an error of its own
        await join_choices.queue_pick(
            self.web, self.session, self.cref, {"group": GROUP, "session": 5_000}
        )
        (gone,) = await self.loop()
        self.assertEqual(gone.error, wa.GONE)

    async def test_a_bad_pick_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [
            {}, {"group": GROUP}, {"group": "x", "session": 1}, {"group": GROUP, "session": True},
            {"group": GROUP, "session": 0}, {"group": GROUP, "thread": "nope"},
            {"group": GROUP, "session": 1, "thread": THREAD}, 5,
        ]  # fmt: skip
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await join_choices.queue_pick(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_row_older_than_an_hour_is_swept_and_a_fresh_one_stays(self) -> None:
        await self.save_row()
        await self.sql("UPDATE web_join_choices SET created_at = 5")
        await self.save_row(BOB.id)
        self.assertEqual(await self.kept_store.sweep(self.g), 1)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT user_id FROM web_join_choices")
            rows = await cur.fetchall()
        self.assertEqual([r["user_id"] for r in rows], [BOB.id])
        self.assertLess(time.time() - 3600, time.time())

    async def test_a_big_pick_keeps_first_and_stays_within_the_answer_limit(self) -> None:
        told = {f"{n:08x}" + "0" * 24: 123 + n for n in range(25)}
        stub = self.stub_memory(told, batch=9, first=True)
        answer = await join_choices.queue_pick(
            self.web, self.session, self.cref, {"group": GROUP, "session": 5_000}
        )
        (done,) = await self.loop(stub)
        self.assertLessEqual(len(done.counts), 200)
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertTrue(read["first"])
        self.assertEqual((len(read["chose"]), read["choseMore"]), (wa.CHOSE_MAX, 15))

    async def test_an_empty_question_keeps_nothing_and_says_gone(self) -> None:
        async def nothing(guild: int, cid: str, group: str, user: int) -> bool:
            return False

        await join_choices.queue_read(self.web, self.session, self.cref, {"group": GROUP})
        (action,) = [await self.actions.claim_next(self.g)]
        assert action is not None
        done = await wa.apply(
            self.memory, self.g, {ALICE.id}, action, actions=self.actions, join_choices=nothing
        )
        self.assertEqual(done.error, wa.GONE)

    async def test_a_deleted_character_is_dropped_from_the_page(self) -> None:
        await self.kept_store.save(
            self.g, self.one.id, GROUP, ALICE.id,
            JoinChoices([self.tavin, "f" * 32], [], [], 0, 0, False),
        )  # fmt: skip
        got = await self.read()
        self.assertEqual([m["id"] for m in got["choices"]["members"]], [self.tavin])
