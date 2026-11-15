"""Which of a person's done Maps actions their Undo may take back (#4347): a DM may undo each of
their own done Maps actions, and anyone who is not a DM only an add or a take-back. Invented names.
Needs Postgres."""

from __future__ import annotations

from tests.test_web_api import ALICE
from tests.test_web_maps import MapsDoor


class TheBatchesOfDoneActions(MapsDoor):
    async def done(self, action: str, text: str, batch: int, *, state_done: bool = True) -> None:
        """Queue `action` as Alice, then finish it: done, or failed with its batch still
        written, when `state_done` is False (only the state filter can refuse that batch)."""
        got = await self.add_action(action, text)  # Alice queues it; she is the DM
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        self.assertIsNotNone(claimed, f"nothing claimed after queuing {action}")
        assert claimed is not None  # for mypy; the line above reports the failure
        self.assertEqual(claimed.action, action)  # the oldest waiting row, so ours here
        if state_done:
            await self.actions.finish(self.g, claimed, error="", result_batches=(batch,))
        else:
            await self.actions.finish(self.g, claimed, error="not applied", result_batches=(batch,))

    async def five_done(self) -> None:
        """One done action of each Maps kind, in batches 11 to 15."""
        await self.done("maps-use", f"{self.bobs} yes", 11)
        await self.done("maps-show", f"{self.dm_map} shown", 12)
        await self.done("maps-remove", self.dm_map, 13)
        await self.done("maps-takeback", self.bobs, 14)
        await self.done("maps-add", "a" * 32 + " png " + "b" * 64 + " 10 5 4 3 Title", 15)

    async def test_a_dm_may_undo_each_of_their_own_done_maps_actions(self) -> None:
        await self.five_done()
        for batch in range(11, 16):
            self.assertTrue(await self.actions.map_batch_made(self.g, self.one.id, ALICE.id, batch))

    async def test_without_being_a_dm_only_an_add_and_a_take_back_count(self) -> None:
        await self.five_done()
        allowed = {14, 15}  # maps-takeback and maps-add
        for batch in range(11, 16):
            self.assertEqual(
                await self.actions.map_batch_made(
                    self.g, self.one.id, ALICE.id, batch, own_only=True
                ),
                batch in allowed,
            )

    async def test_another_persons_batch_is_never_theirs(self) -> None:
        await self.done("maps-takeback", self.bobs, 11)
        for own_only in (False, True):
            self.assertFalse(
                await self.actions.map_batch_made(
                    self.g, self.one.id, ALICE.id + 1, 11, own_only=own_only
                )
            )

    async def test_an_action_that_is_not_done_has_no_batch_to_undo(self) -> None:
        await self.done("maps-takeback", self.bobs, 11, state_done=False)
        # The batch is written on the failed row, so only the done state keeps it from Undo.
        self.assertFalse(await self.actions.map_batch_made(self.g, self.one.id, ALICE.id, 11))
        self.assertFalse(await self.actions.map_batch_made(self.g, self.one.id, ALICE.id, 99999))

    async def test_a_batch_of_another_campaign_is_not_theirs(self) -> None:
        await self.done("maps-takeback", self.bobs, 11)
        self.assertFalse(await self.actions.map_batch_made(self.g, "0" * 32, ALICE.id, 11))
