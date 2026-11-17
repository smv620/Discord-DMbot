"""The "Progress to check" lines on the website (#4616, migration 0528): a database door for a DM of
the campaign and nobody else, that lists a line only while every speaker is recorded now, and
three queue actions (keep, none, Undo) that the bot applies through the same store calls as
Discord's 🧵 card, a keep being one change with one Undo. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import thread_parts as parts
from dmbot.memory import thread_progress as tp
from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM, GOING, OPEN
from dmbot.web import relationships, thread_progress
from dmbot.web.recaps import RecapError, _scoped
from tests.agreed import agree
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

BEL, KIA = 22, 33
LINE_MS = 1_700_000_005_000
NOW = 1_700_000_100


class Progress(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, self.g, BEL, KIA)
        self.progress = tp.ThreadProgressStore(self.db)
        made = await self.memory.new_thread(
            self.g, self.one.id, "Find Garret", "quest", OPEN, source=DM
        )
        self.thread = made.value.id
        goal = await self.memory.add_thread_part(
            self.g, self.one.id, self.thread, parts.OBJECTIVE, "find Garret", source=DM
        )
        self.goal = goal.value.id
        self.sid = "s" * 32

    async def offer(self, *changes: tp.Change, speakers: tuple[int, ...] = (BEL,)) -> None:
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
            " VALUES (%s, %s, %s, 1700000000) ON CONFLICT DO NOTHING",
            self.sid, self.g, self.one.id,
        )  # fmt: skip
        await self.progress.offer(
            self.g, self.one.id, self.sid, 1_700_000_000,
            [(self.thread, c, (LINE_MS,), speakers) for c in changes], now=NOW,
        )  # fmt: skip

    async def listed(self) -> list[dict[str, Any]]:
        got = await thread_progress.read(self.web, self.session, self.cref)
        threads: list[dict[str, Any]] = got["threads"]
        return threads

    async def waiting_ids(self) -> list[str]:
        return [x.id for x in await self.progress.waiting(self.g, self.one.id)]

    async def waiting_of(self, kind: str) -> str:
        """The one waiting line of this kind (lines offered together share a second, so their
        order is not the order they were offered in)."""
        (found,) = [
            x.id for x in await self.progress.waiting(self.g, self.one.id) if x.change.kind == kind
        ]
        return found

    async def queue_one(self, action: str, text: str, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.queue_one(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome

    async def parts_now(self) -> list[parts.Part]:
        from dmbot.memory.gate import Viewer

        return await self.memory.parts_of_threads(
            self.g, self.one.id, [self.thread], Viewer.the_dm()
        )


class TheDoor(Progress):
    async def test_the_lines_come_by_thread_in_the_cards_words_with_no_ids_but_handles(
        self,
    ) -> None:
        await self.offer(
            tp.Change(tp.PART, parts.COMPLICATION, "the cave is trapped"),
            tp.Change(tp.PART_STATE, state="done", part_id=self.goal),
            tp.Change(tp.THREAD_STATE, state=GOING),
        )
        (one,) = await self.listed()
        self.assertEqual((one["name"], one["more"], one["sessions"]), ("Find Garret", 0, 1))
        self.assertEqual(one["sessionAt"], 1_700_000_000)
        self.assertEqual(
            sorted(x["text"] for x in one["lines"]),
            ["Done: find Garret", "New · In the way: the cave is trapped",
             "This thread: happening now"],
        )  # fmt: skip

    async def test_a_line_goes_when_a_speaker_stops_being_recorded_and_returns_with_them(
        self,
    ) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING), speakers=(BEL, KIA))
        self.assertEqual(len(await self.listed()), 1)
        await self.sql("DELETE FROM consent WHERE guild_id = %s AND user_id = %s", self.g, KIA)
        self.assertEqual(await self.listed(), [])
        await agree(self.db, self.g, KIA)
        self.assertEqual(len(await self.listed()), 1)

    async def test_an_answered_line_is_not_listed_and_a_player_gets_nothing(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        await self.progress.turn_down(self.g, self.one.id, await self.waiting_ids())
        self.assertEqual(await self.listed(), [])
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as refused:
            await thread_progress.read(self.web, bob, self.cref)
        self.assertEqual(refused.exception.code, "not_allowed")


class TheAnswers(Progress):
    async def test_keep_keeps_the_ticked_lines_and_turns_down_the_rest_in_one_undoable_change(
        self,
    ) -> None:
        await self.offer(
            tp.Change(tp.PART, parts.COMPLICATION, "the cave is trapped"),
            tp.Change(tp.THREAD_STATE, state=GOING),
        )
        trap, going = await self.waiting_of(tp.PART), await self.waiting_of(tp.THREAD_STATE)
        done = await self.act("progress-keep", f"{trap} {going}")
        self.assertFalse(done.error)
        (batch,) = done.batches
        self.assertEqual(await self.listed(), [])
        self.assertIn("the cave is trapped", [p.words for p in await self.parts_now()])
        undone = await self.act("progress-undo", f"keep {batch}")
        self.assertFalse(undone.error)
        self.assertNotIn("the cave is trapped", [p.words for p in await self.parts_now()])
        self.assertEqual(len(await self.waiting_ids()), 2)  # (both wait again, as in Discord)

    async def test_none_turns_the_lines_down_and_undo_puts_them_back(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        ids = await self.waiting_ids()
        self.assertFalse((await self.act("progress-none", ",".join(ids))).error)
        self.assertEqual(await self.listed(), [])
        self.assertFalse((await self.act("progress-undo", f"none {','.join(ids)}")).error)
        self.assertEqual(await self.waiting_ids(), ids)

    async def test_nothing_ticked_turns_the_unticked_down_without_a_change(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        (line,) = await self.waiting_ids()
        done = await self.act("progress-keep", f"- {line}")
        self.assertFalse(done.error)
        self.assertEqual(done.batches, ())
        self.assertEqual(await self.waiting_ids(), [])

    async def test_a_line_answered_meanwhile_is_gone(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        ids = await self.waiting_ids()
        await self.progress.turn_down(self.g, self.one.id, ids)
        self.assertEqual((await self.act("progress-keep", f"{ids[0]} -")).error, wa.GONE)
        self.assertEqual((await self.act("progress-none", ids[0])).error, wa.GONE)

    async def test_lines_of_two_threads_are_not_one_answer(self) -> None:
        other = await self.memory.new_thread(
            self.g, self.one.id, "Free the Mill", "quest", OPEN, source=DM
        )
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        self.thread = other.value.id
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        ids = await self.waiting_ids()
        self.assertEqual(len(ids), 2)
        self.assertEqual((await self.act("progress-none", ",".join(ids))).error, wa.BAD_TARGET)
        self.assertEqual(len(await self.waiting_ids()), 2)

    async def test_an_undo_of_a_keep_that_is_not_this_dms_own_is_gone(self) -> None:
        other = await self.memory.add_thread_part(
            self.g, self.one.id, self.thread, parts.OBJECTIVE, "see the mill", source=DM
        )
        self.assertIsNotNone(other.batch)
        self.assertEqual((await self.act("progress-undo", f"keep {other.batch}")).error, wa.GONE)
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        (line,) = await self.waiting_ids()
        kept = await self.act("progress-keep", f"{line} -")
        (batch,) = kept.batches
        self.assertTrue(
            await self.actions.progress_batch_made(self.g, self.one.id, ALICE.id, batch)
        )
        self.assertFalse(await self.actions.progress_batch_made(self.g, self.one.id, 999, batch))

    async def test_the_door_refuses_a_bad_shape_and_a_player(self) -> None:
        def refused(got: Any) -> bool:
            return got is None or got["status"] != "ok"

        line = "a" * 32
        for action, text in (
            ("progress-keep", "-"), ("progress-keep", f"{line}"), ("progress-keep", "x -"),
            ("progress-none", "-"), ("progress-none", line + "0"), ("progress-undo", "keep x"),
            ("progress-undo", "something"),
        ):  # fmt: skip
            self.assertTrue(refused(await self.queue_one(action, text)), (action, text))
        bob = await self.signed(BOB, THURSDAY)
        self.assertTrue(refused(await self.queue_one("progress-none", line, who=bob)))
        ok = await self.queue_one("progress-keep", f"{line} -")
        self.assertEqual(ok["status"], "ok")

    async def test_the_page_queues_only_a_known_answer(self) -> None:
        line = "b" * 32
        for body in (
            {}, {"action": "nope"}, {"action": "keep", "on": [], "off": []},
            {"action": "keep", "on": [line], "off": [line]}, {"action": "none", "lines": []},
            {"action": "undo"}, {"action": "keep", "on": ["x"], "off": []},
        ):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await thread_progress.change(self.web, self.session, self.cref, body)
            self.assertEqual(bad.exception.code, "bad_action", body)
        queued = await thread_progress.change(
            self.web, self.session, self.cref, {"action": "keep", "on": [line], "off": []}
        )
        self.assertEqual(len(queued), 32)


class TheEdges(Progress):
    async def test_a_speaker_who_stopped_between_the_list_and_the_keep_keeps_nothing(self) -> None:
        await self.offer(tp.Change(tp.PART, parts.COMPLICATION, "the cave is trapped"),
                         speakers=(BEL, KIA))  # fmt: skip
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        trap, going = await self.waiting_of(tp.PART), await self.waiting_of(tp.THREAD_STATE)
        await self.sql("DELETE FROM consent WHERE guild_id = %s AND user_id = %s", self.g, KIA)
        done = await self.act("progress-keep", f"{trap} {going}")
        self.assertEqual(done.error, wa.CHANGED)
        self.assertNotIn("the cave is trapped", [p.words for p in await self.parts_now()])
        # (the unticked one is turned down, as Discord's card does when nothing is kept)
        self.assertNotIn(going, await self.waiting_ids())

    async def test_another_campaigns_lines_are_not_ours(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        (line,) = await self.waiting_ids()
        other_ref = f"{self.g}.{self.two.id}"
        with self.assertRaises(RecapError) as refused:
            await thread_progress.read(self.web, self.session, other_ref)
        self.assertEqual(refused.exception.code, "not_allowed")
        self.assertEqual(await self.progress.waiting(self.g, self.two.id), [])
        self.assertIn(line, await self.waiting_ids())

    async def test_a_thread_shows_at_most_25_lines_and_says_how_many_more(self) -> None:
        await self.offer(
            *(tp.Change(tp.PART, parts.OBJECTIVE, f"step number {n}") for n in range(30))
        )
        (one,) = await self.listed()
        self.assertEqual((len(one["lines"]), one["more"]), (25, 5))

    async def test_a_keep_of_25_lines_goes_through_the_door_with_its_long_text(self) -> None:
        await self.offer(
            *(tp.Change(tp.PART, parts.OBJECTIVE, f"step number {n}") for n in range(25))
        )
        ids = await self.waiting_ids()
        self.assertEqual(len(ids), 25)
        done = await self.act("progress-keep", f"{','.join(ids[:20])} {','.join(ids[20:])}")
        self.assertFalse(done.error)
        self.assertEqual(len(done.batches), 1)
        self.assertEqual(await self.waiting_ids(), [])

    async def test_the_answer_poll_knows_a_progress_keep_and_its_change(self) -> None:
        await self.offer(tp.Change(tp.THREAD_STATE, state=GOING))
        (line,) = await self.waiting_ids()
        queued = await thread_progress.change(
            self.web, self.session, self.cref, {"action": "keep", "on": [line], "off": []}
        )
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        got = await relationships.read_answer(self.web, self.session, self.cref, queued)
        self.assertEqual(got["state"], "done")
        self.assertIsInstance(got["batch"], int)
