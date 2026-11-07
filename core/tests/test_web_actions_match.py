"""The page's word on a name DMbot matched on its own (#3602, slice 4b): ✅ and ↩️ applied by the bot
through the store's own calls (`match-confirm`, `match-undo`, filed under no name). Real Postgres;
invented names. The queue table's action words are the website door's migration, so the action is
built directly."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import CONFIRMED, DM
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
        self, action: str, target: str, *, dms: Any = None, campaign: str | None = None
    ) -> str:
        """The error word, "" when it worked."""
        a = wa.Action("a1", campaign or self.one.id, "", ALICE.id, action, target_id=target)
        done = await wa.apply(self.memory, GUILD, dms if dms is not None else {ALICE.id}, a)
        return done.error

    async def waiting(self) -> int:
        return len(await self.memory.waiting_joins(GUILD, self.one.id))

    async def test_confirm_makes_the_name_a_name_of_the_entry_and_it_stops_waiting(self) -> None:
        _, _, batch = await self.matched()
        self.assertEqual(await self.apply("match-confirm", str(batch)), "")
        self.assertEqual(await self.waiting(), 0)

    async def test_undo_takes_the_match_back_and_the_name_waits_again(self) -> None:
        _, gone, batch = await self.matched()
        self.assertEqual(await self.apply("match-undo", str(batch)), "")
        self.assertEqual(await self.waiting(), 0)
        back = await self.memory.entity(GUILD, self.one.id, gone)
        assert back is not None
        self.assertEqual((back.status, back.name), ("proposed", "The Ferry Lady"))

    async def test_a_second_press_and_a_made_up_batch_are_not_found(self) -> None:
        _, _, batch = await self.matched()
        await self.apply("match-confirm", str(batch))
        for action in ("match-confirm", "match-undo"):
            self.assertEqual(await self.apply(action, str(batch)), wa.NOT_FOUND)
        for bad in ("12", "x", "", "²", "9" * 40):
            self.assertEqual(await self.apply("match-undo", bad), wa.NOT_FOUND)

    async def test_the_undo_cannot_take_back_any_other_change(self) -> None:
        await self.matched()
        other = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="place", name="Greywater", source=DM, status=CONFIRMED
            )
        ).value
        # (the batch that made "Greywater" is not a match)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT batch FROM memory_changes WHERE campaign_id = %s AND row_id = %s",
                (self.one.id, other.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(await self.apply("match-undo", str(row["batch"])), wa.NOT_FOUND)
        self.assertIsNotNone(await self.memory.entity(GUILD, self.one.id, other.id))

    async def test_a_sender_who_is_no_longer_a_dm_is_refused_and_nothing_changes(self) -> None:
        _, _, batch = await self.matched()
        for action in wa.MATCH_ACTIONS:
            self.assertEqual(await self.apply(action, str(batch), dms=set()), wa.NOT_ALLOWED)
        self.assertEqual(await self.waiting(), 1)

    async def test_another_campaigns_action_never_reaches_this_ones_match(self) -> None:
        _, _, batch = await self.matched()
        for action in wa.MATCH_ACTIONS:
            self.assertEqual(
                await self.apply(action, str(batch), campaign=self.two.id), wa.NOT_FOUND
            )
        self.assertEqual(await self.waiting(), 1)

    async def test_a_match_already_answered_in_discord_is_not_found_here(self) -> None:
        keep, _, batch = await self.matched()
        await self.memory.confirm_join(GUILD, self.one.id, batch)  # (answered in Discord)
        self.assertEqual(await self.apply("match-undo", str(batch)), wa.NOT_FOUND)
        self.assertEqual(await self.waiting(), 0)
        self.assertIsNotNone(await self.memory.entity(GUILD, self.one.id, keep))
