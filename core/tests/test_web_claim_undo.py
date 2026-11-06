"""The website's "Undo all" for an "All true" / "All secret" press (#3645): a kept fact answers with
its change batch, and `claim-undo` takes it back and asks the question again, the calls of
Discord's Undo. Only the DM's own answer can be undone; a retried job is done; a later change is
refused. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_memory_claims import said
from tests.test_web_api import ALICE
from tests.test_web_link_actions import Editing


class ClaimUndo(Editing):
    async def claim(self, subject: str = "Mira Voss", obj: str = "Far Hall") -> str:
        await self.agree(7)
        await self.claims.propose(
            self.g, self.one.id, [said(subject, "knows", obj)], {"1": 7},
            opted_in={7}, prompt_version="p1", model="m1", session_started_at=500,
        )  # fmt: skip
        found = await self.claims.waiting(self.g, self.one.id, opted_in={7})
        return found[-1].id

    async def queue(self, **body: Any) -> str:
        return await relationships.queue_waiting(self.web, self.session, self.cref, body)

    async def refused_undo(self, who: Any = None, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_waiting(self.web, who or self.session, self.cref, body)
        return caught.exception.code

    async def work(self) -> list[wa.Outcome]:
        async def now() -> set[int]:
            return {7}

        waiting = wa.Waiting(self.claims, BackstoryLinks(self.db), now)
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            out = await wa.apply(
                self.memory, self.g, {ALICE.id}, action, waiting, actions=self.actions
            )
            await self.actions.finish(
                self.g, action, error=out.error, result_id=out.result_id,
                result_other=out.result_other,
            )  # fmt: skip
            done.append(out)
        return done

    async def confirmed(self, claim: str) -> int:
        """Press the website's ✅ on the claim and return the batch the answer gave back."""
        answer = await self.queue(kind="claim", action="confirm", id=claim)
        self.assertEqual([o.error for o in await self.work()], [""])
        got = await self.poll(answer)
        self.assertEqual(got["state"], "done")
        self.assertIsInstance(got["batch"], int)
        return int(got["batch"])

    async def status(self, claim: str) -> str:
        found = await self.claims.get(self.g, self.one.id, claim)
        assert found is not None
        return found.status

    async def test_a_kept_fact_answers_with_its_batch(self) -> None:
        claim = await self.claim()
        await self.confirmed(claim)
        self.assertEqual(await self.status(claim), "confirmed")

    async def test_undo_takes_the_fact_back_and_asks_the_question_again(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        answer = await self.queue(kind="claim", action="undo", id=claim, batch=batch)
        self.assertEqual([o.error for o in await self.work()], [""])
        self.assertEqual((await self.poll(answer))["state"], "done")
        self.assertEqual(await self.status(claim), "proposed")
        mine = [
            r
            for r in await self.memory.relations(self.g, self.one.id)
            if r.predicate == "knows"
            and r.source == "dm"
            and r.object_id == self.hall
            and r.subject_id == self.mira
        ]
        self.assertEqual(mine, [])  # (the fact the press made is gone)

    async def test_a_retried_undo_is_done_not_an_error(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        await self.queue(kind="claim", action="undo", id=claim, batch=batch)
        action = await self.actions.claim_next(self.g)
        assert action is not None

        async def now() -> set[int]:
            return {7}

        waiting = wa.Waiting(self.claims, BackstoryLinks(self.db), now)
        first = await wa.apply(
            self.memory, self.g, {ALICE.id}, action, waiting, actions=self.actions
        )
        again = await wa.apply(
            self.memory, self.g, {ALICE.id}, action, waiting, actions=self.actions
        )
        self.assertEqual(
            (first.error, again.error), ("", "")
        )  # (the first try died before it said so)

    async def test_a_later_change_to_what_the_fact_made_is_refused_as_gone(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        (rel,) = [
            r
            for r in await self.memory.relations(self.g, self.one.id)
            if r.predicate == "knows"
            and r.object_id == self.hall
            and r.subject_id == self.mira
            and r.source == "dm"
        ]
        await self.edit(rel.id, action="secret", secret=True)  # a later change to the same fact
        await self.queue(kind="claim", action="undo", id=claim, batch=batch)
        self.assertEqual([o.error for o in await self.work()], ["", wa.GONE])
        self.assertEqual(await self.status(claim), "confirmed")  # nothing was put back

    async def test_only_the_dms_own_answer_can_be_undone(self) -> None:
        claim, other = await self.claim(), await self.claim("Rook", "Greywater Keep")
        mine = await self.confirmed(claim)
        # a batch that is not the one this claim's own answer made, and a claim never answered here
        self.assertEqual(
            await self.refused_undo(kind="claim", action="undo", id=claim, batch=mine + 99),
            "not_found",
        )
        self.assertEqual(
            await self.refused_undo(kind="claim", action="undo", id=other, batch=mine), "not_found"
        )
        for bad in (0, -1, True, "7", 10**13):
            self.assertEqual(
                await self.refused_undo(kind="claim", action="undo", id=claim, batch=bad),
                "not_found",
            )

    async def test_an_answer_pruned_after_its_hour_can_no_longer_be_undone(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        async with self.db.guild(self.g) as conn:  # (rows are deleted an hour after they were made)
            await conn.execute("DELETE FROM name_actions WHERE action = 'claim-confirm'")
        self.assertEqual(
            await self.refused_undo(kind="claim", action="undo", id=claim, batch=batch), "not_found"
        )

    async def test_a_batch_from_a_rejection_is_not_an_undo_of_a_confirm(self) -> None:
        claim, other = await self.claim(), await self.claim("Rook", "Greywater Keep")
        kept = await self.confirmed(claim)
        await self.queue(kind="claim", action="reject", id=other)
        await self.work()
        self.assertEqual(
            await self.refused_undo(kind="claim", action="undo", id=other, batch=kept), "not_found"
        )

    async def test_a_batch_already_undone_while_the_claim_was_kept_again_is_gone(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        await self.queue(kind="claim", action="undo", id=claim, batch=batch)
        await self.work()  # (undone: waiting again)
        await self.claims.confirm(
            self.g, self.one.id, claim, opted_in={7}
        )  # kept again, from Discord
        await self.queue(kind="claim", action="undo", id=claim, batch=batch)
        self.assertEqual(
            [o.error for o in await self.work()], [wa.GONE]
        )  # (not the old batch's to take)
        self.assertEqual(await self.status(claim), "confirmed")

    async def test_a_player_is_refused(self) -> None:
        claim = await self.claim()
        batch = await self.confirmed(claim)
        self.assertEqual(
            await self.refused_undo(
                self.player, kind="claim", action="undo", id=claim, batch=batch
            ),
            "not_allowed",
        )
