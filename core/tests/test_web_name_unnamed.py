"""The bot half of "Give them a name" on the website (#2808): the three actions `name`,
`name-join` and `name-undo`, applied with the calls of Discord's own flow so the change log and
Undo agree, checked again when applied, and answered with the words the page expects. The
actions are handed to `apply` directly (the queue's check constraint comes with the website's
migration). Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import CONFIRMED, MERGED, PROPOSED, REJECTED
from tests.test_memory_store import GUILD_A, MemoryTest

DM = 7


def action(kind: str, entity: str, **kw: Any) -> wa.Action:
    return wa.Action(
        id=kw.pop("id", "a" * 32),
        campaign_id=kw.pop("campaign_id", ""),
        entity_id=entity,
        user_id=kw.pop("user_id", DM),
        action=kind,
        **kw,
    )


class Naming(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ken = await self.add("Ken", status=CONFIRMED)
        added = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=self.ken, role="sibling"
        )
        self.sis = added.entity.id
        self.batches: dict[str, int] = {}

    async def batch_of(self, campaign_id: str, user_id: int, action_id: str) -> int | None:
        return self.batches.get(action_id)

    async def go(self, kind: str, entity: str | None = None, **kw: Any) -> wa.Outcome:
        kw.setdefault("campaign_id", self.c)
        act = action(kind, entity or self.sis, **kw)
        out = await wa.apply(self.memory, GUILD_A, {DM}, act, None, self.batch_of)
        if not out.error and out.result_other:
            self.batches[act.id] = int(out.result_other)
        return out

    async def status(self, entity_id: str) -> Any:
        return await self.memory.entity(GUILD_A, self.c, entity_id)

    # ---- name -------------------------------------------------------------------------------

    async def test_a_new_name_renames_and_gives_the_batch_for_undo(self) -> None:
        out = await self.go("name", text="  Ilse  Voss ")
        self.assertEqual((out.error, out.result_id), ("", self.sis))
        self.assertRegex(out.result_other, r"^[0-9]{1,32}$")
        now = await self.status(self.sis)
        self.assertEqual((now.name, now.unnamed), ("Ilse Voss", False))  # a name of its own now
        self.assertNotIn("name unknown", now.name)

    async def test_a_name_dmbot_knows_writes_nothing_and_names_the_entry(self) -> None:
        ilse = await self.add("Ilse", status=CONFIRMED)
        out = await self.go("name", text="Ilse")
        self.assertEqual((out.error, out.result_id), (wa.KNOWN_NAME, ilse))
        self.assertEqual(out.result_other, "")
        self.assertIn("name unknown", (await self.status(self.sis)).name)

    async def test_more_than_one_entry_with_that_name_is_ambiguous_and_nothing_is_written(
        self,
    ) -> None:
        await self.add("Ilse", status=CONFIRMED)
        other = await self.add("Ilse the Younger", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, other, "Ilse", kind="nickname", source="dm", status=CONFIRMED
        )
        out = await self.go("name", text="Ilse")
        self.assertEqual((out.error, out.result_id), (wa.AMBIGUOUS, ""))
        self.assertIn("name unknown", (await self.status(self.sis)).name)

    async def test_gone_or_already_named_or_missing_is_gone(self) -> None:
        out = await self.go("name", "f" * 32, text="Ilse")
        self.assertEqual(out.error, wa.GONE)  # no such entry
        await self.memory.set_entity_status(GUILD_A, self.c, self.sis, REJECTED, source="dm")
        self.assertEqual((await self.go("name", text="Ilse")).error, wa.GONE)  # rejected
        out = await self.go("name", self.ken, text="Ilse")
        self.assertEqual(out.error, wa.GONE)  # an entry with a name already

    async def test_a_second_apply_of_a_row_already_done_is_done_not_an_error(self) -> None:
        await self.go("name", text="Ilse")
        again = await self.go("name", text="Ilse")
        self.assertEqual((again.error, again.result_id), ("", self.sis))
        self.assertEqual(again.result_other, "")  # (no new change: nothing to undo twice)

    async def test_a_bad_name_is_refused(self) -> None:
        self.assertEqual((await self.go("name", text="   ")).error, wa.BAD_NAME)

    async def test_another_dm_who_is_not_one_is_not_allowed(self) -> None:
        act = action("name", self.sis, campaign_id=self.c, user_id=99, text="Ilse")
        out = await wa.apply(self.memory, GUILD_A, {DM}, act)
        self.assertEqual(out.error, wa.NOT_ALLOWED)

    async def test_an_entry_of_another_campaign_is_never_touched(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        out = await self.go("name", campaign_id=other, text="Ilse")
        self.assertEqual(out.error, wa.GONE)  # (the id is not in that campaign)
        self.assertIn("name unknown", (await self.status(self.sis)).name)

    # ---- name-join --------------------------------------------------------------------------

    async def test_a_join_keeps_the_named_entry_and_gives_the_batch(self) -> None:
        ilse = await self.add("Ilse", status=CONFIRMED)
        out = await self.go("name-join", target_id=ilse)
        self.assertEqual((out.error, out.result_id), ("", ilse))
        self.assertRegex(out.result_other, r"^[0-9]{1,32}$")
        gone = await self.status(self.sis)
        self.assertEqual((gone.status, gone.merged_into), (MERGED, ilse))

    async def test_a_retried_join_is_done(self) -> None:
        ilse = await self.add("Ilse", status=CONFIRMED)
        await self.go("name-join", target_id=ilse)
        again = await self.go("name-join", target_id=ilse)
        self.assertEqual((again.error, again.result_id), ("", ilse))

    async def test_a_join_into_an_unnamed_target_or_itself_or_a_missing_one_is_gone(self) -> None:
        twin = (await self.memory.add_unnamed(GUILD_A, self.c, what="king")).entity.id
        for target in (twin, self.sis, "f" * 32):
            with self.subTest(target=target[:4]):
                self.assertEqual((await self.go("name-join", target_id=target)).error, wa.GONE)
        self.assertEqual((await self.status(self.sis)).status, PROPOSED)

    async def test_a_join_into_a_name_the_dm_has_not_confirmed_is_gone(self) -> None:
        suggested = await self.add("Ilse", status=PROPOSED)
        out = await self.go("name-join", target_id=suggested)
        self.assertEqual(out.error, wa.GONE)
        self.assertEqual((await self.status(self.sis)).status, PROPOSED)  # nothing merged

    async def test_a_join_of_a_person_gone_or_already_named_is_gone(self) -> None:
        ilse = await self.add("Ilse", status=CONFIRMED)
        out = await self.go("name-join", self.ken, target_id=ilse)
        self.assertEqual(out.error, wa.GONE)  # Ken is not an unnamed person

    # ---- name-undo --------------------------------------------------------------------------

    async def test_undo_of_a_rename_makes_them_not_named_again(self) -> None:
        named = action("name", self.sis, campaign_id=self.c, text="Ilse", id="b" * 32)
        done = await wa.apply(self.memory, GUILD_A, {DM}, named, None, self.batch_of)
        self.batches[named.id] = int(done.result_other)
        out = await self.go("name-undo", target_id=named.id)
        self.assertEqual(out.error, "")
        self.assertIn("name unknown", (await self.status(self.sis)).name)

    async def test_undo_of_a_join_brings_the_person_back(self) -> None:
        ilse = await self.add("Ilse", status=CONFIRMED)
        joined = action("name-join", self.sis, campaign_id=self.c, target_id=ilse, id="c" * 32)
        done = await wa.apply(self.memory, GUILD_A, {DM}, joined, None, self.batch_of)
        self.batches[joined.id] = int(done.result_other)
        out = await self.go("name-undo", target_id=joined.id)
        self.assertEqual(out.error, "")
        back = await self.status(self.sis)
        self.assertEqual((back.status, back.merged_into), (PROPOSED, None))

    async def test_undo_after_a_later_change_or_with_no_batch_is_gone(self) -> None:
        named = action("name", self.sis, campaign_id=self.c, text="Ilse", id="d" * 32)
        done = await wa.apply(self.memory, GUILD_A, {DM}, named, None, self.batch_of)
        self.batches[named.id] = int(done.result_other)
        await self.memory.rename_entity(GUILD_A, self.c, self.sis, "Ilse Voss", source="dm")
        late = await self.go("name-undo", target_id=named.id)
        self.assertEqual(late.error, wa.GONE)  # a later change touched it
        self.assertEqual((await self.status(self.sis)).name, "Ilse Voss")
        self.assertEqual((await self.go("name-undo", target_id="e" * 32)).error, wa.GONE)

    async def test_a_retried_undo_whose_first_run_worked_is_done_not_gone(self) -> None:
        named = action("name", self.sis, campaign_id=self.c, text="Ilse", id="9" * 32)
        done = await wa.apply(self.memory, GUILD_A, {DM}, named, None, self.batch_of)
        self.batches[named.id] = int(done.result_other)
        self.assertEqual((await self.go("name-undo", target_id=named.id)).error, "")
        again = await self.go("name-undo", target_id=named.id)  # the same batch, undone already
        self.assertEqual((again.error, again.result_id), ("", self.sis))
        self.assertIn("name unknown", (await self.status(self.sis)).name)

    async def test_an_undo_without_the_batch_reader_is_gone(self) -> None:
        act = action("name-undo", self.sis, campaign_id=self.c, target_id="d" * 32)
        out = await wa.apply(self.memory, GUILD_A, {DM}, act)
        self.assertEqual(out.error, wa.GONE)


class TheStore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = wa.ActionStore(self.db)

    async def queue(self, rid: str, *, user: int = DM, action_name: str = "spell") -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, 1)",
                (GUILD_A, self.c, rid, "e" * 32, user, action_name),
            )

    async def test_a_known_name_answer_keeps_the_entry_it_names_others_do_not(self) -> None:
        await self.queue("1" * 32)
        act = await self.store.claim_next(GUILD_A)
        assert act is not None
        await self.store.finish(
            GUILD_A, act, error=wa.KNOWN_NAME, result_id="f" * 32, result_other="9"
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT state, error, result_id, result_other FROM name_actions WHERE id = %s",
                ("1" * 32,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            dict(row),
            {"state": "failed", "error": "known_name", "result_id": "f" * 32, "result_other": ""},
        )
        await self.queue("2" * 32)
        other = await self.store.claim_next(GUILD_A)
        assert other is not None
        await self.store.finish(GUILD_A, other, error=wa.GONE, result_id="f" * 32)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT result_id FROM name_actions WHERE id = %s", ("2" * 32,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["result_id"], "")

    async def test_the_batch_of_a_done_name_is_only_the_senders_own(self) -> None:
        await self.queue("3" * 32, action_name="spell")
        async with self.db.guild(GUILD_A) as conn:  # (a spell is not a name: never offered)
            await conn.execute(
                "UPDATE name_actions SET state = 'done', result_other = '12' WHERE id = %s",
                ("3" * 32,),
            )
        self.assertIsNone(await self.store.named_batch(GUILD_A, self.c, DM, "3" * 32))
