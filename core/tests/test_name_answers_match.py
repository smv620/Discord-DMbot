"""The page's word on a name DMbot matched on its own (#3602, slice 4b): ✅ and ↩️ applied by the bot
through the store's own calls. Real Postgres; invented names. The queue's table does not yet allow
these action words (the website's door adds them), so the answer is built directly."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED, DM
from dmbot.recaps import name_answers as na
from tests.test_web_api import ALICE
from tests.test_web_name_checks import GUILD, WithChecks


class Matches(WithChecks):
    async def matched(self) -> tuple[str, str, int]:
        """DMbot matched a waiting name to a confirmed entry: (kept id, merged-away id, batch)."""
        keep = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="npc", name="Old Tam", source=DM, status=CONFIRMED
            )
        ).value.id
        gone = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="npc", name="The Ferry Lady", source="scan"
            )
        ).value.id
        done = await self.memory.join_by_ai(GUILD, self.one.id, keep, gone)
        assert done.batch is not None
        return keep, gone, done.batch

    async def apply(
        self, action: str, entity: str, target: str, *, dms: Any = None, campaign: str | None = None
    ) -> str:
        answer = na.Answer(
            "a1", campaign or self.one.id, entity, ALICE.id, action, target_id=target
        )
        return await na.apply(self.memory, GUILD, dms if dms is not None else {ALICE.id}, answer)

    async def waiting(self) -> int:
        return len(await self.memory.waiting_joins(GUILD, self.one.id))

    async def test_confirm_makes_the_name_a_name_of_the_entry_and_it_stops_waiting(self) -> None:
        keep, _, batch = await self.matched()
        self.assertEqual(await self.apply(na.CONFIRM_MATCH, keep, str(batch)), "")
        self.assertEqual(await self.waiting(), 0)

    async def test_undo_takes_the_match_back_and_the_name_waits_again(self) -> None:
        keep, gone, batch = await self.matched()
        self.assertEqual(await self.apply(na.UNDO_MATCH, keep, str(batch)), "")
        self.assertEqual(await self.waiting(), 0)
        back = await self.memory.entity(GUILD, self.one.id, gone)
        assert back is not None
        self.assertEqual((back.status, back.name), ("proposed", "The Ferry Lady"))

    async def test_a_second_press_and_a_made_up_batch_are_not_found(self) -> None:
        keep, _, batch = await self.matched()
        await self.apply(na.CONFIRM_MATCH, keep, str(batch))
        self.assertEqual(await self.apply(na.CONFIRM_MATCH, keep, str(batch)), na.NOT_FOUND_WORD)
        self.assertEqual(await self.apply(na.UNDO_MATCH, keep, str(batch)), na.NOT_FOUND_WORD)
        self.assertEqual(await self.apply(na.UNDO_MATCH, keep, "12"), na.NOT_FOUND_WORD)
        self.assertEqual(await self.apply(na.UNDO_MATCH, keep, "x"), na.NOT_FOUND_WORD)

    async def test_the_undo_cannot_take_back_any_other_change(self) -> None:
        keep, _, _ = await self.matched()
        other = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="place", name="Greywater", source=DM, status=CONFIRMED
            )
        ).value
        self.assertIsNotNone(other)
        # (the batch that made "Greywater" is not a match, whatever entry the answer names)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT batch FROM memory_changes WHERE campaign_id = %s AND row_id = %s",
                (self.one.id, other.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            await self.apply(na.UNDO_MATCH, keep, str(row["batch"])), na.NOT_FOUND_WORD
        )
        self.assertIsNotNone(await self.memory.entity(GUILD, self.one.id, other.id))

    async def test_the_answer_must_name_the_entry_the_match_was_made_to(self) -> None:
        _, gone, batch = await self.matched()
        self.assertEqual(await self.apply(na.CONFIRM_MATCH, gone, str(batch)), na.NOT_FOUND_WORD)
        self.assertEqual(await self.waiting(), 1)

    async def test_a_sender_who_is_no_longer_a_dm_is_refused_and_nothing_changes(self) -> None:
        keep, _, batch = await self.matched()
        for action in na.MATCH_ACTIONS:
            self.assertEqual(await self.apply(action, keep, str(batch), dms=set()), na.NOT_DM)
        self.assertEqual(await self.waiting(), 1)

    async def test_another_campaigns_answer_never_reaches_this_ones_match(self) -> None:
        keep, _, batch = await self.matched()
        self.assertEqual(
            await self.apply(na.CONFIRM_MATCH, keep, str(batch), campaign=self.two.id),
            na.NOT_FOUND_WORD,
        )
        self.assertEqual(await self.waiting(), 1)
