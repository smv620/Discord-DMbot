"""World notes on the website (#4285, slice 1c, core): a database door that answers a DM of the
campaign and nobody else, with the same lines as Discord's list (`world_notes.read`), and three
queue actions (✅ on the lines the page showed, 🚫 on one, ↩ Undo of one of those) that the bot
applies through the store, each one Undo. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import map_facts as mf
from dmbot.memory import web_actions as wa
from dmbot.memory import world_notes as wn
from dmbot.memory.map_fact_store import Read
from dmbot.web.recaps import _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting


class WorldNotesDoor(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.memory.set_place_level(self.g, self.one.id, self.keep, "region", source="dm")
        await self.memory.add_read_map_facts(
            self.g, self.one.id, [Read(self.keep, mf.TERRAIN, "hill")], source="background"
        )
        await self.memory.add_story_language(
            self.g, self.one.id, self.mira, "Giant", source="background"
        )

    async def door(self, who: Any = None, campaign: str = "", guild: int = 0) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild or self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_world_notes(%s, %s) AS r", (self.g, campaign or self.one.id)
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def lines(self) -> list[dict[str, Any]]:
        got = await self.door()
        return [line for group in got["groups"] for line in group["lines"]]

    async def add_action(self, action: str, text: str, who: Any = None) -> Any:
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
        got = await self.add_action(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome


class TheDoor(WorldNotesDoor):
    async def test_the_dm_gets_the_same_lines_as_discord(self) -> None:
        store = await self.memory.world_notes(self.g, self.one.id)
        got = await self.lines()
        self.assertEqual([line["id"] for line in got], [n.id for n in store])
        place, language = got
        self.assertEqual(
            (place["kind"], place["name"], place["layer"], place["value"], place["from"]),
            (wn.PLACE, "Greywater Keep", mf.TERRAIN, "hill", "lore"),
        )
        self.assertEqual(
            (language["kind"], language["name"], language["value"]),
            (wn.LANGUAGE, "Mira Voss", "Giant"),
        )

    async def test_a_player_or_another_campaigns_dm_gets_nothing(self) -> None:
        # (Bob is a player here and the DM of the server's other campaign, "Two")
        self.assertIsNone(await self.door(await self.signed(BOB, THURSDAY)))
        self.assertIsNotNone(await self.door(await self.signed(BOB, THURSDAY), self.two.id))
        self.assertIsNone(await self.door(campaign=self.two.id))  # (Alice is not Two's DM)
        self.assertIsNone(await self.door(guild=self.g + 1))  # (scoped to another server)
        self.assertIsNone(await self.add_action("world-not", "place:" + "a" * 32,
                                                await self.signed(BOB, THURSDAY)))  # fmt: skip

    async def test_a_ruler_is_its_group_and_only_while_the_group_is_kept(self) -> None:
        order = await self.add("Order of the Keep", "faction")
        await self.memory.add_read_map_facts(
            self.g, self.one.id, [Read(self.keep, mf.CONTROL, order, how="rules")],
            source="background",
        )  # fmt: skip
        ruler = next(line for line in await self.lines() if line["layer"] == mf.CONTROL)
        self.assertEqual((ruler["value"], ruler["ruler"]), (order, "Order of the Keep"))
        await self.memory.set_entity_status(self.g, self.one.id, order, "rejected", source="dm")
        self.assertEqual(
            [line["id"] for line in await self.lines()],
            [n.id for n in await self.memory.world_notes(self.g, self.one.id)],
        )
        self.assertNotIn(mf.CONTROL, [line.get("layer") for line in await self.lines()])

    async def same_as_discord(self) -> list[str]:
        ids = [line["id"] for line in await self.lines()]
        self.assertEqual(ids, [n.id for n in await self.memory.world_notes(self.g, self.one.id)])
        return ids

    async def test_a_language_question_waiting_or_answered_hides_it_as_in_discord(self) -> None:
        sword_smith = await self.add("Brann", "npc")
        await self.memory.add_story_language(
            self.g, self.one.id, sword_smith, "Dwarvish", source="background"
        )
        for who, name, state in (
            (self.mira, "Giant", "waiting"),
            (sword_smith, "Dwarvish", "never"),
        ):
            await self.sql(
                "INSERT INTO memory_trait_offers (guild_id, campaign_id, entity_id, kind, name,"
                " name_key, state, created_at) VALUES (%s, %s, %s, 'language', %s, %s, %s, 1)",
                self.g, self.one.id, who, name, name.casefold(), state,
            )  # fmt: skip
        self.assertEqual(len(await self.same_as_discord()), 1)
        self.assertEqual([line["kind"] for line in await self.lines()], [wn.PLACE])

    async def test_the_rest_of_the_rule_matches_discord(self) -> None:
        # a character with a sheet, the DM's own language, a place turned down, a checked reading,
        # a closed one, and one on a place merged away: none of them is a line, on either side
        sheeted = await self.add("Tamsin", "npc")
        await self.memory.add_story_language(
            self.g, self.one.id, sheeted, "Elvish", source="background"
        )
        await self.sql(
            "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
            " fetched_at, player_id) VALUES (%s, %s, %s, 'https://www.dndbeyond.com/characters/1',"
            " '{}'::jsonb, 'dndbeyond', 1, %s)",
            self.g, self.one.id, sheeted, ALICE.id,
        )  # fmt: skip
        await self.memory.add_story_language(self.g, self.one.id, self.mira, "Common", source="dm")
        gone_place = await self.add("Old Mill", "place")
        merged = await self.add("Mill Ruins", "place")
        for place in (gone_place, merged):
            await self.memory.set_place_level(self.g, self.one.id, place, "region", source="dm")
            await self.memory.add_read_map_facts(
                self.g, self.one.id, [Read(place, mf.TERRAIN, "hill")], source="background"
            )
        await self.memory.set_entity_status(
            self.g, self.one.id, gone_place, "rejected", source="dm"
        )
        await self.memory.merge(
            self.g, self.one.id, self.keep, merged, source="dm", dm_said_same=True
        )
        before = await self.same_as_discord()
        self.assertEqual(len(before), 2)  # (the keep's land and Mira's Giant only)
        climate = Read(self.keep, mf.CLIMATE, "cold")
        await self.memory.add_read_map_facts(self.g, self.one.id, [climate], source="background")
        new = [i for i in await self.same_as_discord() if i not in before]
        await self.memory.keep_world_notes(self.g, self.one.id, new, source="dm")  # (checked)
        await self.sql(
            "UPDATE memory_map_facts SET to_session_at = 5 WHERE guild_id = %s"
            " AND campaign_id = %s AND id = %s",
            self.g, self.one.id, before[0].partition(":")[2],
        )  # fmt: skip
        self.assertEqual(await self.same_as_discord(), before[1:])

    async def test_only_the_list_shapes_are_taken(self) -> None:
        line = "place:" + "a" * 32
        for action, text in (
            ("world-keep", ""),
            ("world-keep", "place:xyz"),
            ("world-keep", " ".join([line] * 26)),
            ("world-not", f"{line} {line}"),
            ("world-not", "map:" + "a" * 32),
            ("world-undo", "12x"),
            ("world-undo", "1" * 16),
        ):
            with self.subTest(action=action, text=text[:40]):
                got = await self.add_action(action, text)
                self.assertNotEqual(got["status"], "ok")
        self.assertEqual((await self.add_action("world-keep", " ".join([line] * 25)))["status"],
                         "ok")  # fmt: skip


class TheActions(WorldNotesDoor):
    async def test_keep_takes_the_lines_shown_as_one_undo(self) -> None:
        ids = [line["id"] for line in await self.lines()]
        outcome = await self.act("world-keep", " ".join(ids))
        self.assertEqual((outcome.error, outcome.counts), ("", "kept:2"))
        self.assertEqual(await self.lines(), [])
        (batch,) = outcome.batches
        undone = await self.act("world-undo", str(batch))
        self.assertEqual(undone.error, "")
        self.assertEqual([line["id"] for line in await self.lines()], ids)

    async def test_not_right_on_a_place_and_a_second_press_is_gone(self) -> None:
        place = (await self.lines())[0]["id"]
        self.assertEqual((await self.act("world-not", place)).error, "")
        self.assertNotIn(place, [line["id"] for line in await self.lines()])
        self.assertEqual((await self.act("world-not", place)).error, wa.GONE)
        self.assertEqual((await self.act("world-keep", place)).error, wa.GONE)

    async def test_a_second_undo_is_done_and_another_actions_batch_is_gone(self) -> None:
        place = (await self.lines())[0]["id"]
        (batch,) = (await self.act("world-not", place)).batches
        self.assertEqual((await self.act("world-undo", str(batch))).error, "")
        self.assertEqual((await self.act("world-undo", str(batch))).error, "")  # (a retry)
        await self.sql(  # (a batch another website action of this DM recorded)
            "UPDATE name_actions SET action = 'map-terrain' WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("world-undo", str(batch))).error, wa.GONE)

    async def test_another_dms_change_is_gone(self) -> None:
        await self.sql(
            "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
            self.g, self.one.id, BOB.id,
        )  # fmt: skip
        place = (await self.lines())[0]["id"]
        (batch,) = (await self.act("world-not", place)).batches
        await self.sql(
            "UPDATE name_actions SET user_id = %s WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            BOB.id, self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("world-undo", str(batch))).error, wa.GONE)

    async def test_undo_without_the_queue_store_fails(self) -> None:
        got = await self.add_action("world-undo", "1")
        self.assertEqual(got["status"], "ok")
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        self.assertEqual(
            (await wa.apply(self.memory, self.g, {ALICE.id}, claimed)).error, wa.FAILED
        )

    async def test_undo_takes_back_only_a_world_notes_change_of_this_dm(self) -> None:
        other = await self.memory.keep_world_notes(
            self.g, self.one.id, [(await self.lines())[0]["id"]], source="dm"
        )  # (made in Discord, not by a website action)
        assert other.batch is not None
        self.assertEqual((await self.act("world-undo", str(other.batch))).error, wa.GONE)
        self.assertEqual((await self.act("world-undo", "999999")).error, wa.GONE)
        self.assertEqual(len(await self.lines()), 1)

    async def test_a_non_dm_sender_is_refused_by_the_bot(self) -> None:
        got = await self.add_action("world-not", (await self.lines())[0]["id"])
        self.assertEqual(got["status"], "ok")
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {BOB.id}, claimed, actions=self.actions)
        self.assertEqual(outcome.error, wa.NOT_ALLOWED)
        self.assertEqual(len(await self.lines()), 2)
