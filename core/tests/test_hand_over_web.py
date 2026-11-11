"""The "Does Claude still have it?" questions on the website (#4122, the web half of #4115 slice 1):
the bot writes the list, only a DM of the campaign reads it, an answer is queued and the bot applies
it as the Discord card does (No ends the older tie, with Undo; Yes, both is remembered). Real
Postgres; invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import hand_over_answer
from dmbot.memory.models import CONFIRMED
from dmbot.web import hand_over
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web

S1, S2 = 1_700_000_000, 1_700_100_000


class Base(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.brand = await self.add("Frost Brand", "item")
        await self.memory.set_unique(self.g, self.one.id, self.brand, True, source="dm")
        self.older = await self.tie(self.mira, S1)
        self.newer = await self.tie(self.rook, S2)
        await self.refresh()

    async def tie(self, who: str, at: int) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, who, "owns", self.brand, source="dm", confidence=1.0,
            status=CONFIRMED, from_session_at=at,
        )  # fmt: skip
        return str(made.value[0].id)

    async def refresh(self) -> None:
        waiting = await self.memory.hand_over_waiting(self.g, self.one.id, source="dm")
        await hand_over_answer.refresh(self.db, self.g, self.one.id, waiting)

    async def listed(self) -> list[dict[str, Any]]:
        return list((await hand_over.waiting(self.web, self.session, self.cref))["questions"])

    async def press(self, how: str = "no", undoes: str | None = None, who: Any = None) -> str:
        return await hand_over.answer(
            self.web, who or self.session, self.cref, self.older, self.newer, how, undoes
        )

    def bot(self) -> Any:
        return SimpleNamespace(
            memory=self.memory,
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=self.one), db=self.db,
                list_campaigns=AsyncMock(return_value=[self.one]),
            ),
        )  # fmt: skip

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT * FROM hand_over_answers WHERE id = %s", (rid,))
            got = await cur.fetchone()
        assert got is not None
        return dict(got)

    async def owns(self, relation_id: str) -> Any:
        rels = await self.memory.relations(self.g, self.one.id, include_secret=True)
        return next(r for r in rels if r.id == relation_id)


class TheList(Base):
    async def test_a_dm_reads_the_question_with_the_names(self) -> None:
        (q,) = await self.listed()
        self.assertEqual(
            (q["itemName"], q["olderName"], q["newerName"], q["predicate"]),
            ("Frost Brand", "Mira Voss", "Rook", "owns"),
        )
        self.assertEqual((q["olderTie"], q["newerTie"]), (self.older, self.newer))

    async def test_a_player_and_a_dm_of_another_campaign_are_told_nothing(self) -> None:
        with self.assertRaises(RecapError) as player:
            await hand_over.waiting(self.web, self.player, self.cref)
        self.assertEqual(player.exception.code, "not_found")
        with self.assertRaises(RecapError) as other:
            await hand_over.waiting(self.web, self.session, f"{self.g}.{self.two.id}")
        self.assertEqual(other.exception.code, "not_found")

    async def test_a_question_whose_tie_ended_meanwhile_is_not_shown(self) -> None:
        await self.memory.update_relation(
            self.g, self.one.id, self.older, source="dm", to_session_at=S2
        )
        self.assertEqual(await self.listed(), [])  # (before the bot writes the list again)

    async def test_the_bot_writes_the_list_for_every_campaign(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute("DELETE FROM hand_over_questions")
        self.assertEqual(await hand_over_answer.refresh_guild(self.bot(), self.g), 1)
        self.assertEqual(len(await self.listed()), 1)


class TheAnswers(Base):
    async def test_an_answer_is_queued_once_and_read_back(self) -> None:
        made = await self.press("no")
        self.assertEqual(await self.press("no"), made)  # (a second press: the same answer)
        got = await hand_over.read_answer(self.web, self.session, self.cref, made)
        self.assertEqual((got["state"], got["action"], got["undoable"]), ("waiting", "no", False))

    async def test_a_wrong_shape_or_an_unknown_question_is_refused(self) -> None:
        for kw in ({"how": "maybe"}, {"how": "undo"}, {"how": "no", "undoes": "a" * 32}):
            with self.subTest(kw), self.assertRaises(RecapError) as bad:
                await self.press(**kw)
            self.assertEqual(bad.exception.code, "bad_value")
        with self.assertRaises(RecapError) as gone:
            await hand_over.answer(self.web, self.session, self.cref, "b" * 32, self.newer, "no")
        self.assertEqual(gone.exception.code, "not_found")
        with self.assertRaises(RecapError) as player:
            await self.press("no", who=self.player)
        self.assertEqual(player.exception.code, "not_found")

    async def test_no_ends_the_older_tie_and_undo_reopens_it(self) -> None:
        made = await self.press("no")
        self.assertEqual(await hand_over_answer.run_round(self.bot(), self.g), 1)
        row = await self.row(made)
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        self.assertIsNotNone((await self.owns(self.older)).to_session_at)
        self.assertEqual(await self.listed(), [])  # (the list follows at once)
        read = await hand_over.read_answer(self.web, self.session, self.cref, made)
        self.assertTrue(read["undoable"])
        undo = await self.press("undo", undoes=made)
        await hand_over_answer.run_round(self.bot(), self.g)
        self.assertEqual((await self.row(undo))["state"], "done")
        self.assertIsNone((await self.owns(self.older)).to_session_at)

    async def test_yes_both_is_remembered_and_ends_nothing(self) -> None:
        made = await self.press("both")
        await hand_over_answer.run_round(self.bot(), self.g)
        self.assertEqual((await self.row(made))["state"], "done")
        self.assertIsNone((await self.owns(self.older)).to_session_at)
        self.assertEqual(await self.memory.hand_over_waiting(self.g, self.one.id, source="dm"), [])

    async def test_a_question_answered_in_discord_meanwhile_is_gone(self) -> None:
        made = await self.press("no")
        (q,) = await self.memory.hand_over_waiting(self.g, self.one.id, source="dm")
        await self.memory.answer_hand_over(self.g, self.one.id, q, still_has=True, source="dm")
        await hand_over_answer.run_round(self.bot(), self.g)
        row = await self.row(made)
        self.assertEqual((row["state"], row["error"]), ("failed", hand_over_answer.GONE))

    async def test_someone_no_longer_a_dm_is_not_allowed(self) -> None:
        made = await self.press("no")
        me = self.bot()
        me.campaigns.get = AsyncMock(return_value=SimpleNamespace(is_dm=lambda _uid: False))
        await hand_over_answer.run_round(me, self.g)
        row = await self.row(made)
        self.assertEqual((row["state"], row["error"]), ("failed", hand_over_answer.NOT_ALLOWED))
        self.assertIsNone((await self.owns(self.older)).to_session_at)


class TheEdges(Base):
    async def test_after_undo_it_offers_no_undo_and_a_second_is_refused(self) -> None:
        made = await self.press("no")
        await hand_over_answer.run_round(self.bot(), self.g)
        await self.press("undo", undoes=made)
        await hand_over_answer.run_round(self.bot(), self.g)
        read = await hand_over.read_answer(self.web, self.session, self.cref, made)
        self.assertFalse(read["undoable"])
        with self.assertRaises(RecapError) as again:
            await self.press("undo", undoes=made)
        self.assertEqual(again.exception.code, "not_found")

    async def test_undo_of_a_no_not_done_is_refused(self) -> None:
        made = await self.press("no")  # (still waiting: nothing to take back)
        async with self.db.guild(self.g) as conn:  # (and it no longer holds the question open)
            await conn.execute("UPDATE hand_over_answers SET state = 'failed' WHERE id = %s",
                               (made,))  # fmt: skip
        with self.assertRaises(RecapError) as caught:
            await self.press("undo", undoes=made)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_an_answer_is_claimed_once_retaken_when_lost_and_never_when_old(self) -> None:
        made = await self.press("no")
        now = [1_800_000_000.0]
        answers = hand_over_answer.HandOverAnswers(self.db, clock=lambda: now[0])
        async with self.db.guild(self.g) as conn:
            await conn.execute("UPDATE hand_over_answers SET created_at = %s", (int(now[0]),))
        got = await answers.claim_next(self.g)
        assert got is not None and (got.id, got.tries) == (made, 1)
        self.assertIsNone(await answers.claim_next(self.g))
        now[0] += hand_over_answer.RETAKE_AFTER_S + 5
        again = await answers.claim_next(self.g)
        assert again is not None and again.tries == 2
        self.assertFalse(await answers.end(self.g, got, ok=True))  # (the lost claim can't close it)
        now[0] += hand_over_answer.GIVE_UP_S + 5
        self.assertIsNone(await answers.claim_next(self.g))

    async def test_a_retaken_no_whose_write_landed_ends_done(self) -> None:
        made = await self.press("no")
        (q,) = await self.memory.hand_over_waiting(self.g, self.one.id, source="dm")
        await self.memory.answer_hand_over(self.g, self.one.id, q, still_has=False, source="dm")
        async with self.db.guild(self.g) as conn:  # (as if the bot stopped before closing it)
            await conn.execute("UPDATE hand_over_answers SET tries = 1 WHERE id = %s", (made,))
        await hand_over_answer.run_round(self.bot(), self.g)
        row = await self.row(made)
        self.assertEqual((row["state"], row["error"]), ("done", ""))

    async def test_without_campaign_memory_nothing_is_listed_or_taken(self) -> None:
        me = self.bot()
        me.memory = None
        self.assertEqual(await hand_over_answer.refresh_guild(me, self.g), 0)
        await self.press("no")
        self.assertEqual(await hand_over_answer.run_round(me, self.g), 0)

    async def test_a_question_still_waiting_keeps_when_it_was_first_listed(self) -> None:
        (first,) = await self.listed()
        async with self.db.guild(self.g) as conn:
            await conn.execute("UPDATE hand_over_questions SET made_at = 5")
        await self.refresh()
        (again,) = await self.listed()
        self.assertEqual((first["olderTie"], again["madeAt"]), (again["olderTie"], 5))
