"""The website's "Add all" for the names an upload found (#3207): the upload row keeps what the
reading proposed (ids by kind), the progress read says how many of each still wait, and the DM
queues `confirm-kinds` and `confirm-undo` for the bot. Invented names throughout."""

from __future__ import annotations

import json
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, lore
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class Confirming(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.upload = "a" * 32
        self.ids: dict[str, list[str]] = {"place": [], "character": [], "concept": []}
        for kind, names in {
            "place": ["Rivermouth", "The Grey Pier"],
            "character": ["Old Hob", "Sister Wren", "Captain Dace"],
            "concept": ["A debt unpaid"],
        }.items():
            for name in names:
                made = await self.memory.add_entity(  # proposed: still waiting for a yes
                    self.g, self.one.id, type=kind,
                    name=name, source="background", role="npc" if kind == "character" else None,
                )  # fmt: skip
                self.ids[kind].append(made.value.id)
        await self.put_upload("done")

    async def put_upload(self, state: str) -> None:
        await self.sql("DELETE FROM lore_uploads")
        await self.sql(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
            " rights_by, rights_at, state, stage, names_found, created_at, updated_at, found)"
            " VALUES (%s, %s, %s, %s, 'paste', 'world', %s, 1, %s, 'done', 6,"
            " floor(extract(epoch FROM now())), floor(extract(epoch FROM now())), %s::jsonb)",
            self.g, self.one.id, self.upload, self.session.user_id, self.session.user_id,
            state, json.dumps(self.ids),
        )  # fmt: skip

    async def waiting(self) -> dict[str, int]:
        got = await lore.read(self.web, self.session, self.cref, self.upload)
        return dict(got["upload"]["waiting"])

    async def queue(self, body: dict[str, Any], who: Any = None) -> str:
        return await all_names.queue_confirm(self.web, who or self.session, self.cref, body)

    async def refused_confirm(self, body: dict[str, Any], who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.queue(body, who)
        return caught.exception.code

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT action, target_id, entity_id, text FROM name_actions"
                " WHERE campaign_id = %s",
                (self.one.id,),
            )
            return list(await cur.fetchall())


class TheCounts(Confirming):
    async def test_the_read_says_how_many_of_each_kind_still_wait_and_never_ideas(self) -> None:
        self.assertEqual(await self.waiting(), {"place": 2, "character": 3})

    async def test_a_name_answered_since_is_not_counted(self) -> None:
        await self.memory.confirm_kinds(
            self.g, self.one.id, [self.ids["place"][0]], "place", source=DM_SOURCE
        )
        self.assertEqual(await self.waiting(), {"place": 1, "character": 3})

    async def test_a_player_never_gets_the_counts(self) -> None:
        with self.assertRaises(RecapError):
            await lore.read(self.web, self.player, self.cref, self.upload)


class TheDoor(Confirming):
    async def test_a_dm_queues_kinds_or_all(self) -> None:
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": ["place"]})
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": "all"})
        got = sorted((r["action"], r["target_id"], r["text"]) for r in await self.rows())
        self.assertEqual(
            got,
            [
                ("confirm-kinds", self.upload, "all"),
                ("confirm-kinds", self.upload, "place"),
            ],
        )

    async def test_ideas_bad_words_and_repeats_are_refused_before_the_door(self) -> None:
        for kinds in (
            ["idea"], ["concept"], ["all"], ["Place"], [], ["place", "place"], ["a"],
            "everything", None,
        ):  # fmt: skip
            with self.subTest(kinds=kinds):
                self.assertEqual(
                    await self.refused_confirm(
                        {"action": "confirm-kinds", "upload": self.upload, "kinds": kinds}
                    ),
                    "bad_target",
                )
        self.assertEqual(await self.rows(), [])

    async def test_an_upload_still_being_read_or_not_there_is_not_found(self) -> None:
        await self.put_upload("working")
        body = {"action": "confirm-kinds", "upload": self.upload, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body), "not_found")
        self.assertEqual(
            await self.refused_confirm({**body, "upload": "b" * 32}), "not_found"
        )  # another campaign's, or none
        self.assertEqual(await self.rows(), [])

    async def test_eight_long_kind_words_fit(self) -> None:
        kinds = [f"{chr(97 + n) * 20}" for n in range(8)]  # 167 characters
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": kinds})
        self.assertEqual(len((await self.rows())[0]["text"]), 8 * 20 + 7)

    async def test_another_campaigns_finished_upload_is_not_found(self) -> None:
        other = await self.campaigns.create(self.g, "Other", self.session.user_id)
        await self.sql(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
            " rights_by, rights_at, state, stage, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, 'paste', 'world', %s, 1, 'done', 'done', 1, 1)",
            self.g, other.id, "d" * 32, self.session.user_id, self.session.user_id,
        )  # fmt: skip
        body = {"action": "confirm-kinds", "upload": "d" * 32, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body), "not_found")

    async def test_a_badly_shaped_found_does_not_break_the_read(self) -> None:
        await self.sql(
            "UPDATE lore_uploads SET found = %s::jsonb WHERE id = %s",
            json.dumps({"place": "x", "character": None, "item": [self.ids["character"][0]]}),
            self.upload,
        )
        self.assertEqual(await self.waiting(), {"item": 1})

    async def test_a_player_cannot_queue_it(self) -> None:
        body = {"action": "confirm-kinds", "upload": self.upload, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body, self.player), "not_allowed")

    async def test_only_the_two_actions_come_through_here(self) -> None:
        self.assertEqual(await self.refused_confirm({"action": "remove"}), "bad_action")
        self.assertEqual(await self.refused_confirm({}), "bad_action")

    async def test_undo_needs_your_own_finished_press_that_made_something(self) -> None:
        first = await self.queue(
            {"action": "confirm-kinds", "upload": self.upload, "kinds": ["character"]}
        )
        undo = {"action": "confirm-undo", "answer": first}
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # still waiting
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_counts = 'character:3',"
            " result_batches = ARRAY[7, 8] WHERE id = %s",
            first,
        )
        again = await self.queue(undo)
        self.assertRegex(again, r"^[0-9a-f]{32}$")
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # one Undo for a press
        self.assertEqual(await self.refused_confirm({**undo, "answer": "c" * 32}), "not_found")
        await self.sql("UPDATE name_actions SET result_batches = '{}' WHERE id = %s", first)
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # it made nothing

    async def test_the_answer_reads_back_the_counts_and_offers_undo(self) -> None:
        first = await self.queue(
            {"action": "confirm-kinds", "upload": self.upload, "kinds": ["place", "character"]}
        )
        waiting = await all_names.read_action(self.web, self.session, self.cref, first)
        self.assertEqual(
            (waiting["state"], waiting["counts"], waiting["undo"]), ("waiting", {}, False)
        )
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_counts = 'place:2 character:3',"
            " result_batches = ARRAY[9], progress_done = 2, progress_total = 2 WHERE id = %s",
            first,
        )
        done = await all_names.read_action(self.web, self.session, self.cref, first)
        self.assertEqual(done["counts"], {"place": 2, "character": 3})
        self.assertTrue(done["undo"])
        self.assertEqual((done["progress"]["done"], done["progress"]["total"]), (2, 2))
