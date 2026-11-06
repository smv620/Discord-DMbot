"""The website's line for the players (#3568, the website half of #2967): a DM writes, changes and
clears it on the Names page, the read sends it to a DM and to nobody else, an over-long line is
refused and not cut, and another campaign's entry is untouched. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting


class PlayersLine(Acting):
    async def line_of(self, entity: str) -> str | None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT players_text FROM memory_entities WHERE campaign_id = %s AND id = %s",
                (self.one.id, entity),
            )
            row = await cur.fetchone()
        assert row is not None
        text: str | None = row["players_text"]
        return text

    async def listed(self, entity: str, who: Any = None) -> Any:
        got = await self.page(who)
        return next(n for n in got["names"] if n["id"] == entity)


class TheWrite(PlayersLine):
    async def test_a_dm_saves_changes_and_clears_it(self) -> None:
        await self.queue(self.keep, action="players-line", line="  The keep   is cold ")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.line_of(self.keep), "The keep is cold")
        self.assertEqual((await self.listed(self.keep))["playersText"], "The keep is cold")
        await self.queue(self.keep, action="players-line", line="The keep is warm now")
        await self.apply_all()
        self.assertEqual(await self.line_of(self.keep), "The keep is warm now")
        await self.queue(self.keep, action="players-line", line="   ")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertIsNone(await self.line_of(self.keep))
        self.assertIsNone((await self.listed(self.keep))["playersText"])

    async def test_it_is_one_change_so_one_undo(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        await self.queue(self.keep, action="players-line", line="The keep is cold")
        await self.apply_all()
        changes = await self.log_since(before)
        self.assertEqual({c["batch"] for c in changes}, {changes[0]["batch"]})

    async def test_over_the_limit_is_refused_and_nothing_changes(self) -> None:
        refused = await self.refused_action(self.keep, action="players-line", line="x" * 121)
        self.assertEqual(refused, "too_long")
        self.assertIsNone(await self.line_of(self.keep))
        exactly = await self.queue(self.keep, action="players-line", line="y" * 120)
        await self.apply_all()
        self.assertEqual((await self.poll(exactly))["state"], "done")
        self.assertEqual(await self.line_of(self.keep), "y" * 120)

    async def test_a_line_that_is_not_text_is_refused(self) -> None:
        for bad in (None, 7, ["a"], "a\x00b"):
            refused = await self.refused_action(self.keep, action="players-line", line=bad)
            self.assertEqual(refused, "bad_name", bad)

    async def test_a_dm_of_another_campaign_cannot_write_it(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        refused = await self.refused_action(
            self.keep, who=bob, action="players-line", line="Not yours"
        )
        self.assertEqual(refused, "not_allowed")
        self.assertIsNone(await self.line_of(self.keep))

    async def test_a_person_a_rejected_or_a_gone_entry_is_not_found(self) -> None:
        await self.sql(
            "UPDATE memory_entities SET status = 'rejected' WHERE campaign_id = %s AND id = %s",
            self.one.id,
            self.sword,
        )
        refused = await self.refused_action(self.sword, action="players-line", line="Gone")
        self.assertEqual(refused, "not_found")

    async def test_the_bot_refuses_a_non_dm_sender(self) -> None:
        await self.queue(self.keep, action="players-line", line="The keep is cold")
        done = await self.apply_all(dms={999})
        self.assertEqual([o.error for o in done], ["not_allowed"])
        self.assertIsNone(await self.line_of(self.keep))

    async def test_another_campaigns_entry_is_untouched(self) -> None:
        other = await self.memory.add_entity(
            self.g,
            self.two.id,
            type="place",
            name="Elsewhere",
            source=DM_SOURCE,
            status="confirmed",
        )
        await self.queue(self.keep, action="players-line", line="The keep is cold")
        await self.apply_all()
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT players_text FROM memory_entities WHERE campaign_id = %s AND id = %s",
                (self.two.id, other.value.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["players_text"])


class TheRead(PlayersLine):
    async def test_someone_who_is_not_a_dm_of_it_gets_null_never_the_value(self) -> None:
        await self.queue(self.keep, action="players-line", line="The keep is cold")
        await self.apply_all()
        # Bob DMs "Two" in the same server; the list of "One" is the plain one for him.
        bob = await self.signed(BOB, THURSDAY)
        seen = await self.page(bob)
        self.assertFalse(seen["canEdit"])
        row = next(n for n in seen["names"] if n["id"] == self.keep)
        self.assertIsNone(row["playersText"])
        self.assertNotIn("The keep is cold", str(seen))
