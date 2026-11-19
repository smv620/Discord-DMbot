"""The website's party record (#4778 part a2, migration 0557): only a DM reads or changes it, the
read lists each player character's stretches with the session each began at, "Has left the party"
is one change with an Undo the DM's own done answer alone may take back, "Back in the party" the
same, an undo of a leave after the character came back is refused, and nothing outside the four
reasons reaches the store. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.web import party_record, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web


class PartyRecordOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.tavin = await self.add("Tavin", "character", role="player_character")

    async def bot_loop(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, action, actions=self.actions)
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def leave(self, why: str = "left") -> wa.Outcome:
        await party_record.queue_leave(
            self.web, self.session, self.cref, {"character": self.tavin, "why": why}
        )
        (done,) = await self.bot_loop()
        return done

    async def back(self) -> wa.Outcome:
        await party_record.queue_back(self.web, self.session, self.cref, {"character": self.tavin})
        (done,) = await self.bot_loop()
        return done

    async def in_party(self) -> bool:
        got = await party_record.read(self.web, self.session, self.cref)
        (tavin,) = [c for c in got["characters"] if c["id"] == self.tavin]
        return bool(tavin["inParty"])

    async def test_a_dm_reads_each_characters_stretches(self) -> None:
        got = await party_record.read(self.web, self.session, self.cref)
        mine = [c for c in got["characters"] if c["id"] == self.tavin]
        self.assertEqual([c["name"] for c in mine], ["Tavin"])
        self.assertTrue(mine[0]["inParty"])
        for c in got["characters"]:
            self.assertEqual(set(c), {"id", "name", "inParty", "spans"})

    async def test_only_a_dm_of_the_campaign_reads_or_changes_it(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as read:
            await party_record.read(self.web, bob, self.cref)
        self.assertEqual(read.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as left:
            await party_record.queue_leave(
                self.web, bob, self.cref, {"character": self.tavin, "why": "left"}
            )
        self.assertEqual(left.exception.code, "not_allowed")

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        for body in (
            {"character": "nope", "why": "left"},
            {"character": self.tavin, "why": "x"},
            5,
        ):
            with self.subTest(body=body):
                with self.assertRaises(RecapError) as err:
                    await party_record.queue_leave(self.web, self.session, self.cref, body)
                self.assertEqual(err.exception.code, "bad_action")
        with self.assertRaises(RecapError) as err2:
            await party_record.queue_back(self.web, self.session, self.cref, {"character": "x"})
        self.assertEqual(err2.exception.code, "bad_action")
        with self.assertRaises(RecapError) as err3:
            await party_record.queue_undo(self.web, self.session, self.cref, "x1")
        self.assertEqual(err3.exception.code, "not_found")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_leave_is_one_change_shows_why_and_a_second_leave_changes_nothing(self) -> None:
        done = await self.leave("died")
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        self.assertFalse(await self.in_party())
        got = await party_record.read(self.web, self.session, self.cref)
        (tavin,) = [c for c in got["characters"] if c["id"] == self.tavin]
        self.assertEqual([s["ended"] for s in tavin["spans"]][-1], "died")
        again = await self.leave("died")
        self.assertEqual(again.error, wa.NOT_IN_PARTY)

    async def test_back_in_the_party_and_its_undo(self) -> None:
        await self.leave()
        done = await self.back()
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        self.assertTrue(done.counts.startswith("asked:"))
        self.assertTrue(await self.in_party())
        again = await self.back()
        self.assertEqual(again.error, wa.ALREADY_BACK)
        await party_record.queue_undo(self.web, self.session, self.cref, str(done.batches[0]))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        self.assertFalse(await self.in_party())

    async def test_the_undo_of_a_leave_puts_the_character_back_once(self) -> None:
        done = await self.leave()
        (batch,) = done.batches
        await party_record.queue_undo(self.web, self.session, self.cref, str(batch + 1000))
        (bad,) = await self.bot_loop()
        self.assertEqual(bad.error, wa.GONE)  # (a change number this DM never made)
        await party_record.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        self.assertTrue(await self.in_party())
        await party_record.queue_undo(self.web, self.session, self.cref, str(batch))
        (retried,) = await self.bot_loop()
        self.assertEqual(retried.error, wa.ALREADY_BACK)  # (in the party again: nothing changed)

    async def test_another_dm_cannot_undo_this_dms_change(self) -> None:
        done = await self.leave()
        (batch,) = done.batches
        self.assertIsNone(await self.actions.party_batch_made(self.g, self.one.id, 12345, batch))
        made = await self.actions.party_batch_made(self.g, self.one.id, ALICE.id, batch)
        assert made is not None
        self.assertEqual(made[0], "party-leave")

    async def test_the_answer_the_page_polls_carries_the_change_number(self) -> None:
        answer_id = await party_record.queue_leave(
            self.web, self.session, self.cref, {"character": self.tavin, "why": "away"}
        )
        (done,) = await self.bot_loop()
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual((read["state"], read["batch"]), ("done", done.batches[0]))

    async def test_the_answer_to_a_return_says_which_question_waits(self) -> None:
        await self.leave()
        answer_id = await party_record.queue_back(
            self.web, self.session, self.cref, {"character": self.tavin}
        )
        await self.bot_loop()
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual(read["state"], "done")
        self.assertIn(read["asked"], {"back", "join", "none"})

    async def test_the_undo_of_a_leave_is_refused_once_the_character_came_back(self) -> None:
        done = await self.leave()
        (batch,) = done.batches
        await self.back()
        await party_record.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, wa.ALREADY_BACK)
