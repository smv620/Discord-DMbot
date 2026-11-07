"""The website's half of DMbot's own matches (#3602): a DM-only list of the matches DMbot made by
itself that nobody has answered, and the two queued answers ("yes, that's right" and Undo). The
list is the store's own rule (`waiting_joins`), so the two never differ; the bot's half applies
the answers and is tested with it. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM
from dmbot.web import all_names
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class Matches(Web):
    async def waiting_name(self, name: str, kind: str = "npc") -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, type=kind, name=name, source="scan"
        )
        return str(made.value.id)

    async def make_match(self, keep: str = "Old Tam", spelling: str = "The Ferry Lady") -> Any:
        tam = await self.add(keep, "npc")
        gone = await self.waiting_name(spelling)
        return await self.memory.join_by_ai(self.g, self.one.id, tam, gone)

    async def listed(self, who: Any = None) -> list[dict[str, Any]]:
        got = await all_names.read_matches(self.web, who or self.session, self.cref)
        return list(got["matches"])

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_nothing_is_listed_before_dmbot_matches_anything(self) -> None:
        self.assertEqual(await self.listed(), [])

    async def test_a_match_is_listed_with_names_the_batch_and_the_time_only(self) -> None:
        made = await self.make_match()
        got = await self.listed()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["spelling"], "The Ferry Lady")
        self.assertEqual(got[0]["keepName"], "Old Tam")
        self.assertEqual(got[0]["keepId"], str(made.value.id))
        self.assertEqual(got[0]["batch"], str(made.batch))
        self.assertEqual(set(got[0]), {"batch", "keepId", "keepName", "spelling", "at"})

    async def test_it_is_the_same_list_the_stores_confirm_reads(self) -> None:
        await self.make_match()
        stored = await self.memory.waiting_joins(self.g, self.one.id)
        self.assertEqual([str(j.batch) for j in stored], [m["batch"] for m in await self.listed()])

    async def test_an_undone_match_leaves_the_list(self) -> None:
        made = await self.make_match()
        await self.memory.undo(self.g, self.one.id, made.batch)
        self.assertEqual(await self.listed(), [])

    async def test_a_confirmed_match_leaves_the_list(self) -> None:
        made = await self.make_match()
        await self.memory.confirm_join(self.g, self.one.id, made.batch)
        self.assertEqual(await self.listed(), [])

    async def test_a_match_the_dm_made_themselves_is_not_listed(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        await self.memory.merge(self.g, self.one.id, tam, gone, source=DM, dm_said_same=True)
        self.assertEqual(await self.listed(), [])

    async def test_a_player_is_told_nothing(self) -> None:
        await self.make_match()
        self.assertEqual(
            await self.refusal(all_names.read_matches(self.web, self.player, self.cref)),
            "not_allowed",
        )

    async def test_a_campaign_that_is_not_there_is_not_allowed(self) -> None:
        await self.make_match()
        other = f"{self.g}.{'0' * 31}1"  # no such campaign: nothing is said about it
        self.assertEqual(
            await self.refusal(all_names.read_matches(self.web, self.session, other)),
            "not_allowed",
        )

    async def test_a_dm_queues_the_answer_for_the_batch_dmbot_made(self) -> None:
        made = await self.make_match()
        for answer in ("confirm", "undo"):
            queued = await all_names.queue_match(
                self.web, self.session, self.cref, str(made.batch), answer
            )
            self.assertTrue(queued)
            async with self.db.guild(self.g) as conn:
                cur = await conn.execute(
                    "SELECT action, target_id, state FROM name_actions WHERE id = %s", (queued,)
                )
                row = await cur.fetchone()
            assert row is not None
            self.assertEqual(
                (row["action"], row["target_id"], row["state"]),
                (f"match-{answer}", str(made.batch), "waiting"),
            )

    async def test_a_batch_dmbot_did_not_make_is_not_found(self) -> None:
        await self.make_match()
        dm_batch = (
            await self.memory.add_entity(self.g, self.one.id, type="npc", name="Zed", source="dm")
        ).batch
        for batch in (str(dm_batch), "999999"):
            self.assertEqual(
                await self.refusal(
                    all_names.queue_match(self.web, self.session, self.cref, batch, "confirm")
                ),
                "not_found",
                batch,
            )

    async def test_the_batch_must_be_digits_and_the_answer_one_of_two(self) -> None:
        for batch in ("", "12x", "-1", "1" * 13, "../1"):
            self.assertEqual(
                await self.refusal(
                    all_names.queue_match(self.web, self.session, self.cref, batch, "undo")
                ),
                "not_found",
                batch,
            )
        self.assertEqual(
            await self.refusal(all_names.queue_match(self.web, self.session, self.cref, "1", "x")),
            "bad_action",
        )

    async def test_a_player_cannot_queue_an_answer(self) -> None:
        made = await self.make_match()
        self.assertEqual(
            await self.refusal(
                all_names.queue_match(self.web, self.player, self.cref, str(made.batch), "confirm")
            ),
            "not_allowed",
        )
