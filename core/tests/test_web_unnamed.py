"""The people the story never named, on the website's Names page (#2803): a DM reads them in their
own group, "Not named yet", with the label built from the parts; nobody else does (a manager who is
not a DM included, even for a confirmed one); and a DM gives them a name through the queue the
other name actions use: `name`, `name-join` ("yes, the same person") and `name-undo`. The bot's
side is not here; these tests play it with plain updates. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_all_names import MEMBER
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

MANAGER = THURSDAY  # manage rights, but not a DM of campaign one (BOB DMs campaign two)


class Unnamed(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ken = await self.add("Ken", "npc")
        made = await self.memory.add_unnamed(self.g, self.one.id, what="sister", anchor_id=self.ken)
        self.sister = made.entity.id
        figure = await self.memory.add_unnamed(self.g, self.one.id, what="a hooded figure")
        self.figure = figure.entity.id

    async def not_named(self, who: Any = None) -> dict[str, Any]:
        return await self.page(who, kind="not_named")


class Reading(Unnamed):
    async def test_a_dm_sees_them_in_their_own_group_first_with_the_label_built_from_the_parts(
        self,
    ) -> None:
        got = await self.not_named()
        rows = {r["id"]: r for r in got["names"]}
        self.assertEqual(
            rows[self.sister]["name"], "Ken's sister (name unknown)",
        )  # fmt: skip
        self.assertEqual(
            (
                rows[self.sister]["unnamed"],
                rows[self.sister]["unnamedWhat"],
                rows[self.sister]["unnamedAnchor"],
            ),
            (True, "sister", "Ken"),
        )
        self.assertEqual(rows[self.figure]["name"], "a hooded figure (name unknown)")
        self.assertIsNone(rows[self.figure]["unnamedAnchor"])
        self.assertEqual(got["total"], 2)
        first = (await self.page())["kinds"][0]
        self.assertEqual(
            (first["key"], first["label"], first["count"]), ("not_named", "Not named yet", 2)
        )

    async def test_the_label_follows_the_anchor_when_the_anchor_is_renamed(self) -> None:
        await self.memory.rename_entity(self.g, self.one.id, self.ken, "Kenneth", source=DM_SOURCE)
        rows = {r["id"]: r for r in (await self.not_named())["names"]}
        self.assertEqual(rows[self.sister]["name"], "Kenneth's sister (name unknown)")

    async def test_one_hanging_off_another_unnamed_person_is_built_from_that_ones_label(
        self,
    ) -> None:
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="cousin", anchor_id=self.sister
        )
        rows = {r["id"]: r for r in (await self.not_named())["names"]}
        self.assertEqual(
            rows[made.entity.id]["name"], "Ken's sister (name unknown)'s cousin (name unknown)"
        )

    async def test_named_people_are_not_unnamed_and_have_no_such_fields_set(self) -> None:
        mira = next(n for n in (await self.page())["names"] if n["id"] == self.mira)
        self.assertEqual((mira["unnamed"], mira["kind"]), (False, "npc"))
        self.assertNotIn("unnamedWhat", mira)
        self.assertNotIn(mira["id"], [n["id"] for n in (await self.not_named())["names"]])

    async def test_a_manager_who_is_not_a_dm_sees_none_of_them_even_a_confirmed_one(self) -> None:
        await self.sql("UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", self.sister)
        manager = await self.signed(BOB, MANAGER)
        got = await self.page(manager)
        self.assertNotIn(self.sister, [n["id"] for n in got["names"]])
        self.assertNotIn("not_named", [k["key"] for k in got["kinds"]])
        only = await self.page(manager, kind="not_named")
        self.assertEqual((only["names"], only["total"]), ([], 0))

    async def test_a_player_gets_nothing(self) -> None:
        member = await self.signed(BOB, MEMBER)
        self.assertEqual(await self.refused(member, self.cref), "not_found")

    async def test_the_group_is_gone_once_nobody_is_unnamed(self) -> None:
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE unnamed")
        kinds = [k["key"] for k in (await self.page())["kinds"]]
        self.assertNotIn("not_named", kinds)

    async def test_the_group_is_not_in_the_search_by_the_stored_label_alone(self) -> None:
        got = await self.page(q="sister")
        self.assertIn(self.sister, [n["id"] for n in got["names"]])  # the DM may search for them


class Naming(Unnamed):
    async def refused_queue(self, entity: str, who: Any = None, **body: Any) -> str:
        return await self.refused_action(entity, who, **body)

    async def test_a_name_is_queued_for_an_unnamed_person_in_the_actions_own_order(self) -> None:
        answer = await self.queue(self.sister, action="name", name="  Mara   Voss ")
        self.assertEqual((await self.poll(answer))["state"], "waiting")
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT action, text, entity_id, target_id FROM name_actions WHERE id = %s",
                (answer,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            (row["action"], row["text"], row["entity_id"], row["target_id"]),
            ("name", "Mara Voss", self.sister, ""),
        )

    async def test_a_name_needs_words_and_a_named_person_is_not_renamed_this_way(self) -> None:
        self.assertEqual(
            await self.refused_queue(self.sister, action="name", name="  "), "bad_name"
        )
        self.assertEqual(
            await self.refused_queue(self.sister, action="name", name="x" * 101), "too_long"
        )
        self.assertEqual(
            await self.refused_queue(self.mira, action="name", name="Mara"), "not_found"
        )
        self.assertEqual(
            await self.refused_queue("0" * 32, action="name", name="Mara"), "not_found"
        )

    async def test_the_other_actions_do_not_reach_an_unnamed_person(self) -> None:
        await self.sql("UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", self.sister)
        for body in (
            {"action": "spell", "name": "Mara"},
            {"action": "kind", "kind": "place"},
            {"action": "hide", "hidden": True},
            {"action": "merge", "into": self.keep},
        ):
            self.assertEqual(await self.refused_queue(self.sister, **body), "not_found", body)

    async def test_a_confirmed_unnamed_person_is_not_a_target_of_a_merge_either(self) -> None:
        # Merging a named entry into a placeholder would skip "give them a name first".
        await self.sql("UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", self.sister)
        self.assertEqual(
            await self.refused_queue(self.keep, action="merge", into=self.sister), "bad_target"
        )
        self.assertEqual(
            await self.refused_queue(self.keep, action="merge-many", others=[self.sister]),
            "bad_target",
        )

    async def test_a_player_and_another_campaigns_dm_cannot_queue_a_name(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(
            await self.refused_queue(self.sister, bob, action="name", name="Mara"), "not_allowed"
        )

    async def test_joining_needs_a_named_entry_that_is_not_itself(self) -> None:
        ok = await self.queue(self.sister, action="name-join", into=self.mira)
        self.assertEqual((await self.poll(ok))["state"], "waiting")
        for into in (self.sister, self.figure):
            self.assertEqual(
                await self.refused_queue(self.sister, action="name-join", into=into),
                "bad_target",
                into,
            )
        self.assertEqual(
            await self.refused_queue(self.sister, action="name-join", into="nope"), "bad_target"
        )
        self.assertEqual(
            await self.refused_queue(self.mira, action="name-join", into=self.keep), "not_found"
        )

    async def test_a_double_press_on_a_name_is_two_rows_the_bot_applies_in_order(self) -> None:
        first = await self.queue(self.sister, action="name", name="Mara")
        second = await self.queue(self.sister, action="name", name="Mara")
        self.assertNotEqual(
            first, second
        )  # (the second finds them named, or gone, when the bot gets there)

    async def test_undo_is_for_a_done_name_or_join_of_the_caller_with_a_kept_record(self) -> None:
        answer = await self.queue(self.sister, action="name", name="Mara")
        self.assertEqual(
            await self.refused_queue(self.sister, action="name-undo", answer=answer), "not_found"
        )
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_id = %s, result_other = %s"
            " WHERE id = %s",
            self.sister, "b" * 32, answer,
        )  # fmt: skip
        undo = await self.queue(self.sister, action="name-undo", answer=answer)
        self.assertEqual((await self.poll(undo))["state"], "waiting")
        # not another DM's, not a bad id, and not an action that is no name
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(
            await self.refused_queue(self.sister, bob, action="name-undo", answer=answer),
            "not_allowed",
        )
        self.assertEqual(
            await self.refused_queue(self.sister, action="name-undo", answer="nope"), "bad_target"
        )
        spelled = await self.queue(self.keep, action="spell", name="Greywater Hold")
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_other = %s WHERE id = %s",
            "c" * 32,
            spelled,
        )
        self.assertEqual(
            await self.refused_queue(self.sister, action="name-undo", answer=spelled), "not_found"
        )

    async def test_the_answer_of_a_done_name_carries_the_row_and_undo(self) -> None:
        answer = await self.queue(self.sister, action="name", name="Mara")
        await self.sql(
            "UPDATE memory_entities SET name = 'Mara', unnamed = false, unnamed_what = NULL,"
            " unnamed_anchor = NULL, unnamed_role = NULL, status = 'confirmed' WHERE id = %s",
            self.sister,
        )
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_id = %s, result_other = %s"
            " WHERE id = %s",
            self.sister, "b" * 32, answer,
        )  # fmt: skip
        done = await self.poll(answer)
        self.assertEqual(
            (done["state"], done["row"]["name"], done["undo"], done["other"]),
            ("done", "Mara", True, None),
        )

    async def test_a_known_name_is_answered_with_the_other_entry_and_writes_nothing(self) -> None:
        answer = await self.queue(self.sister, action="name", name="Mira Voss")
        await self.sql(
            "UPDATE name_actions SET state = 'failed', error = 'known_name', result_id = %s"
            " WHERE id = %s",
            self.mira, answer,
        )  # fmt: skip
        failed = await self.poll(answer)
        self.assertEqual(
            (failed["state"], failed["error"], failed["other"]),
            ("failed", "known_name", {"id": self.mira, "name": "Mira Voss"}),
        )
        self.assertFalse(failed["undo"])

    async def test_the_queue_door_refuses_a_stranger_even_by_sql(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        async with self.db.user(bob.user_id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, 'name', '', '', 'Mara', false) AS r",
                (self.g, self.one.id, self.sister),
            )
            self.assertIsNone(((await cur.fetchone()) or {}).get("r"))


class TheRoute(Unnamed):
    async def test_the_names_list_has_the_fields_over_the_api(self) -> None:
        got = await self.page(kind="not_named")
        self.assertTrue(all(r["unnamed"] for r in got["names"]))

    async def test_a_name_action_is_known_to_the_python_side(self) -> None:
        for body in (
            {"action": "name"},
            {"action": "name", "name": 5},
            {"action": "name-join"},
            {"action": "name-join", "into": "x"},
            {"action": "name-undo", "answer": ""},
        ):
            with self.assertRaises(RecapError):
                await all_names.queue_action(self.web, self.session, self.cref, self.sister, body)
        _ = NOW, ALICE
