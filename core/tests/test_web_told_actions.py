"""The website's "Tell a character" (#3542): a DM queues `told-add` and `told-remove` for a secret
other name, the bot applies them through the store's own calls (so the log and Undo agree), and the
door and the bot both turn away what the rules refuse. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.web import all_names, told_panel
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_my_characters_byfact import ByFactBase


class Telling(ByFactBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        await self.join()
        self.guard = (await self.guard_alias(self.ken)).id

    async def queue(self, by: Any = None, **body: Any) -> str:
        entity = body.pop("entity", self.ken)
        return await all_names.queue_action(self.web, by or self.session, self.cref, entity, body)

    async def turned_away(self, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.queue(**body)
        return caught.exception.code

    async def apply_all(self, dms: Any = None) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, {ALICE.id} if dms is None else dms, action
            )
            await self.actions.finish(
                self.g,
                action,
                error=outcome.error,
                result_id=outcome.result_id,
                result_other=outcome.result_other,
            )
            done.append(outcome)
        return done

    async def told_rows(self) -> list[tuple[str, str]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT alias_id, character_id FROM memory_told WHERE campaign_id = %s",
                (self.one.id,),
            )
            return [(str(r["alias_id"]), str(r["character_id"])) for r in await cur.fetchall()]

    async def tell(self, character: str | None = None) -> str:
        return await self.queue(
            action="told-add", alias=self.guard, character=character or self.okra
        )


class TheAnswers(Telling):
    async def test_telling_a_character_records_it_and_is_done(self) -> None:
        answer = await self.tell()
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.told_rows(), [(self.guard, self.okra)])
        found = await all_names.read_action(self.web, self.session, self.cref, answer)
        self.assertEqual(found["state"], "done")

    async def test_taking_it_back_removes_the_telling(self) -> None:
        await self.tell()
        await self.queue(action="told-remove", alias=self.guard, character=self.okra)
        self.assertEqual([o.error for o in await self.apply_all()], ["", ""])
        self.assertEqual(await self.told_rows(), [])

    async def test_telling_twice_is_done_both_times(self) -> None:
        await self.tell()
        await self.tell()
        self.assertEqual([o.error for o in await self.apply_all()], ["", ""])
        self.assertEqual(await self.told_rows(), [(self.guard, self.okra)])

    async def test_taking_back_twice_is_done_both_times(self) -> None:
        await self.tell()
        for _ in range(2):
            await self.queue(action="told-remove", alias=self.guard, character=self.okra)
        self.assertEqual([o.error for o in await self.apply_all()], ["", "", ""])
        self.assertEqual(await self.told_rows(), [])

    async def test_a_character_who_does_not_hold_the_name_is_a_bad_target(self) -> None:
        await self.tell(self.wen)  # (Wen was never given the guard's name)
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])
        self.assertEqual(await self.told_rows(), [])

    async def test_it_is_one_change_that_undo_takes_back(self) -> None:
        await self.tell()
        await self.apply_all()
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE campaign_id = %s",
                (self.one.id,),
            )
            row = await cur.fetchone()
        assert row is not None
        await self.memory.undo(self.g, self.one.id, row["b"], source="dm")
        self.assertEqual(await self.told_rows(), [])

    async def test_a_name_that_is_no_longer_a_secret_is_turned_away_by_the_bot(self) -> None:
        await self.tell()
        await self.memory.update_alias(self.g, self.one.id, self.guard, source="dm", secret=False)
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])

    async def test_a_telling_can_be_taken_back_after_the_name_stops_being_secret(self) -> None:
        await self.tell()
        await self.apply_all()
        await self.memory.update_alias(self.g, self.one.id, self.guard, source="dm", secret=False)
        await self.queue(action="told-remove", alias=self.guard, character=self.okra)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.told_rows(), [])

    async def test_someone_who_is_no_longer_a_dm_is_not_allowed(self) -> None:
        await self.tell()
        self.assertEqual([o.error for o in await self.apply_all(dms=set())], ["not_allowed"])
        self.assertEqual(await self.told_rows(), [])


class TheDoor(Telling):
    async def test_a_player_cannot_queue_it(self) -> None:
        player = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as caught:
            await self.queue(player, action="told-add", alias=self.guard, character=self.okra)
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_the_other_name_must_be_a_secret_one_of_this_entry(self) -> None:
        public = await self.memory.add_alias(
            self.g, self.one.id, self.ken, "Kenny", kind="short", source="dm", status="confirmed"
        )
        for alias in (public.value.id, "f" * 32):
            code = await self.turned_away(action="told-add", alias=alias, character=self.okra)
            self.assertEqual(code, "not_found", alias)
        # (the guard's name filed under another entry)
        code = await self.turned_away(
            action="told-add", alias=self.guard, character=self.okra, entity=self.mira
        )
        self.assertEqual(code, "not_found")

    async def test_the_character_must_be_a_confirmed_character(self) -> None:
        for other in (self.keep, "f" * 32):
            code = await self.turned_away(action="told-add", alias=self.guard, character=other)
            self.assertEqual(code, "bad_target", other)

    async def test_the_words_are_checked_before_the_door(self) -> None:
        self.assertEqual(
            await self.turned_away(action="told-add", alias="nope", character=self.okra),
            "not_found",
        )
        self.assertEqual(
            await self.turned_away(action="told-add", alias=self.guard, character="Okra"),
            "bad_target",
        )
        self.assertEqual(
            await self.turned_away(action="told-add", character=self.okra), "not_found"
        )

    async def test_a_flag_or_a_second_id_in_the_text_is_refused(self) -> None:
        with self.assertRaises(RecapError):  # (the page never sends a flag: a stray one is ignored)
            await self.queue(
                action="told-add", alias=self.guard, character=f"{self.okra} {self.wen}"
            )
        # a raw call with the flag set is turned away by the door itself
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            from dmbot.web.recaps import _scoped

            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, 'told-add', '', %s, %s, true) AS r",
                (self.g, self.one.id, self.ken, self.guard, self.okra),
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["r"]["status"], "bad_text")

    async def test_the_older_actions_still_pass_the_door(self) -> None:
        answer = await self.queue(action="alias-who", alias=self.guard, who="dm")
        self.assertTrue(answer)


class ThePanel(Telling):
    async def panel(self, by: Any = None, ref: str | None = None) -> list[dict[str, Any]]:
        got = await told_panel.read_told_panel(self.web, by or self.session, ref or self.cref)
        names: list[dict[str, Any]] = got["names"]
        return names

    async def test_a_dm_sees_who_holds_the_secret_name_and_who_was_told(self) -> None:
        rows = [r for r in await self.panel() if r["id"] == self.guard]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entryId"], self.ken)
        self.assertIn(self.okra, [h["id"] for h in rows[0]["holders"]])
        self.assertEqual(rows[0]["told"], [])
        await self.tell()
        await self.apply_all()
        row = next(r for r in await self.panel() if r["id"] == self.guard)
        self.assertEqual([t["id"] for t in row["told"]], [self.okra])
        self.assertNotIn(self.okra, [h["id"] for h in row["holders"]])

    async def test_a_character_who_does_not_hold_the_name_is_not_offered(self) -> None:
        row = next(r for r in await self.panel() if r["id"] == self.guard)
        self.assertNotIn(self.wen, [h["id"] for h in row["holders"]])

    async def test_a_player_gets_nothing_and_a_name_that_is_not_secret_is_not_listed(self) -> None:
        player = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as caught:
            await self.panel(player)
        self.assertEqual(caught.exception.code, "not_allowed")
        await self.memory.update_alias(self.g, self.one.id, self.guard, source="dm", secret=False)
        self.assertEqual([r for r in await self.panel() if r["id"] == self.guard], [])
