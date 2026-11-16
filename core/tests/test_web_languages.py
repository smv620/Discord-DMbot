"""A character's languages on the website (#4271, migration 0519): a database door for a DM of the
campaign and nobody else, and four queue actions (add, remove, "these are right", Undo) that the
bot applies through the same store calls as Discord's 🗣️ Languages screen, each one change with one
Undo. A character with a sheet shows its sheet's languages and refuses a change. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import sheets
from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM
from dmbot.memory.sheet_store import SheetStore
from dmbot.web import languages
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_sheets import answer
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

PLAYER = 9


class Languages(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)

    async def queue_lang(self, action: str, text: str) -> Any:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.queue_lang(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome

    async def shown(self, entity: str | None = None) -> dict[str, Any]:
        return await languages.read(self.web, self.session, self.cref, entity or self.mira)

    async def names(self, entity: str | None = None) -> list[tuple[str, str]]:
        return [(r["name"], r["source"]) for r in (await self.shown(entity))["languages"]]

    async def sheeted(self, *known: str) -> str:
        pc = await self.memory.add_entity(
            self.g, self.one.id, name="Testa", type="player_character", played_by=PLAYER,
            source=DM, status="confirmed",
        )  # fmt: skip
        data = sheets.parse(answer())
        data["languages"] = list(known)
        await SheetStore(self.db).save(self.g, self.one.id, pc.value.id, data, 5, player=PLAYER)
        return str(pc.value.id)


class TheDoor(Languages):
    async def test_a_character_with_no_languages_shows_none(self) -> None:
        self.assertEqual(await self.shown(), {"hasSheet": False, "languages": []})

    async def test_the_story_languages_come_in_order_with_their_source_and_ids(self) -> None:
        await self.act("lang-add", f"{self.mira} Giant")
        await self.memory.add_story_language(
            self.g, self.one.id, self.mira, "Elvish", source="read"
        )
        shown = await self.shown()
        self.assertEqual([(r["name"], r["source"]) for r in shown["languages"]],
                         [("Giant", "dm"), ("Elvish", "read")])  # fmt: skip
        self.assertTrue(all(len(r["id"]) == 32 for r in shown["languages"]))

    async def test_a_character_with_a_sheet_shows_the_sheets_languages_and_no_ids(self) -> None:
        pc = await self.sheeted("Common", "Dwarvish")
        shown = await self.shown(pc)
        self.assertTrue(shown["hasSheet"])
        self.assertEqual(
            [(r["name"], r["source"], "id" in r) for r in shown["languages"]],
            [("Common", "sheet", False), ("Dwarvish", "sheet", False)],
        )

    async def test_the_dms_no_is_never_shown(self) -> None:
        await self.act("lang-add", f"{self.mira} Giant")
        (row,) = (await self.shown())["languages"]
        self.assertEqual((await self.act("lang-remove", row["id"])).error, "")
        self.assertEqual(await self.names(), [])

    async def test_a_player_or_a_stranger_gets_nothing_and_an_unknown_entry_is_not_found(
        self,
    ) -> None:
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as refused:
            await languages.read(self.web, bob, self.cref, self.mira)
        self.assertEqual(refused.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as gone:
            await languages.read(self.web, self.session, self.cref, "a" * 32)
        self.assertEqual(gone.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await languages.read(self.web, self.session, self.cref, "not-an-id")


class TheActions(Languages):
    async def test_add_check_and_remove_are_each_one_change_with_one_undo(self) -> None:
        added = await self.act("lang-add", f"{self.mira} Giant")
        self.assertEqual(added.error, "")
        (batch,) = added.batches
        self.assertEqual(await self.names(), [("Giant", "dm")])
        self.assertEqual((await self.act("lang-undo", str(batch))).error, "")
        self.assertEqual(await self.names(), [])
        self.assertEqual((await self.act("lang-undo", str(batch))).error, "")  # (a retry is done)

    async def test_these_are_right_checks_what_dmbot_found_in_one_change(self) -> None:
        for name in ("Giant", "Elvish"):
            await self.memory.add_story_language(
                self.g, self.one.id, self.mira, name, source="read"
            )
        self.assertEqual({s for _, s in await self.names()}, {"read"})
        checked = await self.act("lang-check", self.mira)
        self.assertEqual(checked.error, "")
        self.assertEqual({s for _, s in await self.names()}, {"dm"})
        (batch,) = checked.batches
        self.assertEqual((await self.act("lang-undo", str(batch))).error, "")
        self.assertEqual({s for _, s in await self.names()}, {"read"})

    async def test_a_character_with_a_sheet_is_refused_and_a_place_is_refused(self) -> None:
        pc = await self.sheeted("Common")
        self.assertEqual((await self.act("lang-add", f"{pc} Giant")).error, wa.BAD_TARGET)
        self.assertEqual((await self.act("lang-add", f"{self.keep} Giant")).error, wa.BAD_TARGET)
        self.assertEqual(await self.names(pc), [("Common", "sheet")])

    async def test_an_entry_or_a_language_gone_is_gone(self) -> None:
        self.assertEqual((await self.act("lang-add", f"{'a' * 32} Giant")).error, wa.GONE)
        self.assertEqual((await self.act("lang-remove", "b" * 32)).error, wa.GONE)

    async def test_undo_takes_back_only_this_dms_own_language_change(self) -> None:
        added = await self.act("lang-add", f"{self.mira} Giant")
        (batch,) = added.batches
        await self.sql(  # (a batch another website action of this DM recorded)
            "UPDATE name_actions SET action = 'map-terrain' WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("lang-undo", str(batch))).error, wa.GONE)
        self.assertEqual(await self.names(), [("Giant", "dm")])

    async def test_the_doors_shapes_are_checked(self) -> None:
        for action, text in (
            ("lang-add", self.mira),  # (no name)
            ("lang-add", f"{self.mira} " + "x" * 61),
            ("lang-add", f"{self.mira}  Giant"),  # (a name starting with a space)
            ("lang-remove", "nope"),
            ("lang-check", self.mira + " extra"),
            ("lang-undo", "x"),
        ):
            got = await self.queue_lang(action, text)
            self.assertNotEqual(got["status"], "ok", (action, text))

    async def test_a_player_cannot_queue_a_language_change(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, "lang-check", "", "", self.mira, False),
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])  # (the door answers NULL: a DM only)


class TheWebModule(Languages):
    async def test_the_pages_words_are_checked_before_anything_is_queued(self) -> None:
        for body in (
            {},
            {"action": "add"},
            {"action": "add", "name": ""},
            {"action": "add", "name": "   "},
            {"action": "add", "name": "x" * 61},
            {"action": "add", "name": 7},
            {"action": "add", "name": "Gi\nant\x00"},
            {"action": "remove", "language": "nope"},
            {"action": "undo", "batch": "-1"},
            {"action": "undo", "batch": 3},
            {"action": "melt"},
        ):
            with self.assertRaises(RecapError, msg=str(body)):
                await languages.change(self.web, self.session, self.cref, self.mira, body)
        with self.assertRaises(RecapError):
            await languages.change(self.web, self.session, self.cref, "nope", {"action": "check"})

    async def test_a_change_is_queued_and_the_answer_gives_its_undo_batch(self) -> None:
        from dmbot.web import relationships

        answer_id = await languages.change(
            self.web, self.session, self.cref, self.mira, {"action": "add", "name": "  Giant  "}
        )
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        done = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(self.g, claimed, error=done.error, result_batches=done.batches)
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual(
            (read["state"], read["error"], read["batch"]), ("done", "", done.batches[0])
        )
        self.assertEqual(await self.names(), [("Giant", "dm")])  # (the name's spaces tidied)
