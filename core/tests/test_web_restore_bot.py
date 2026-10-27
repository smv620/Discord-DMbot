"""The bot's side of restoring a backup from the website (#2746, #2745): a staged copy is applied
with the same code and checks as Discord's `/dmbot restore`, the shared progress shape is kept up,
a refusal is one plain word, the bytes never outlive the end, and a row goes after 24 hours.
Invented names throughout."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns import web_restore
from dmbot.campaigns.store import MAX_BACKUP_BYTES, NO_ROOM_TO_RESTORE, CampaignError, encode_backup
from dmbot.campaigns.web_restore import Outcome, WebRestores
from tests.test_recap_web import Base
from tests.test_web_api import ALICE, THURSDAY

GUILD = THURSDAY.id
RESTORE_ID = "a" * 32


class Restoring(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.restores = WebRestores(self.db)
        self.after: list[tuple[int, str, bool]] = []
        self.imported: list[dict[str, Any]] = []

    async def copy_of(self, campaign: Any, **extra: Any) -> bytes:
        main = await self.campaigns.export(GUILD, campaign.id)
        main.update(extra)
        return encode_backup(main)

    async def stage(
        self,
        main: bytes | None,
        *,
        user: int = ALICE.id,
        replace: str | None = None,
        wanted: int = 0,
        parts: tuple[bytes, ...] = (),
        without_parts: bool = False,
        state: str = "waiting",
        restore_id: str = RESTORE_ID,
        created: int | None = None,
        updated: int | None = None,
    ) -> str:
        now = int(time.time())
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO web_restores (guild_id, id, user_id, replace_id, name, parts_wanted,"
                " main, state, stage, without_parts, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, 'Copy', %s, %s, %s, %s, %s, %s, %s)",
                (
                    GUILD, restore_id, user, replace, wanted, main, state,
                    "restore_waiting" if state == "waiting" else "restore_reading",
                    without_parts, created if created is not None else now,
                    updated if updated is not None else now,
                ),
            )  # fmt: skip
            for n, data in enumerate(parts, 1):
                await conn.execute(
                    "INSERT INTO web_restore_parts (guild_id, restore_id, n, data)"
                    " VALUES (%s, %s, %s, %s)",
                    (GUILD, restore_id, n, data),
                )
        return restore_id

    async def row(self, restore_id: str = RESTORE_ID) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM web_restores WHERE guild_id = %s AND id = %s", (GUILD, restore_id)
            )
            found = await cur.fetchone()
        assert found is not None
        return dict(found)

    async def parts_left(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_restore_parts")
            counted = await cur.fetchone()
        assert counted is not None
        return int(counted["n"])

    def host(
        self,
        *,
        in_server: bool = True,
        gate: str | None = None,
        playing: bool = False,
        fail: Exception | None = None,
    ) -> Any:
        async def get_campaign(g: int, c: str) -> Any:
            return await self.campaigns.get(g, c)

        async def restore_gate(g: int, u: int, replacing: Any = None) -> str | None:
            return gate

        async def is_campaign_playing(g: int, c: str) -> bool:
            return playing

        async def member(g: int, u: int) -> bool:
            return in_server

        async def import_backup(g: int, data: object, importer: int, **kw: Any) -> Any:
            if fail is not None:
                raise fail
            self.imported.append(kw)
            return await self.campaigns.import_backup(g, data, importer, **kw)

        async def after_restore(g: int, c: str, replaced: bool) -> None:
            self.after.append((g, c, replaced))

        return SimpleNamespace(
            get_campaign=get_campaign,
            restore_gate=restore_gate,
            is_campaign_playing=is_campaign_playing,
            in_server=member,
            session_lock=lambda g: asyncio.Lock(),
            import_backup=import_backup,
            after_restore=after_restore,
        )

    async def run_marked(self, host: Any = None) -> dict[str, Any]:
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        self.assertTrue(await web_restore.run_one(host or self.host(), self.restores, GUILD, item))
        return await self.row()


class TheRestore(Restoring):
    async def test_a_copy_becomes_a_new_campaign_the_person_is_the_dm_of(self) -> None:
        await self.stage(await self.copy_of(self.one))
        got = await self.run_marked()
        self.assertEqual((got["state"], got["stage"], got["error"]), ("done", "done", ""))
        made = await self.campaigns.get(GUILD, got["result_id"])
        assert made is not None
        self.assertNotEqual(made.id, self.one.id)
        self.assertIn(ALICE.id, made.dm_user_ids)
        self.assertEqual(self.after, [(GUILD, made.id, False)])
        self.assertIsNone(got["main"])  # (emptied the moment it ended)

    async def test_a_copy_replaces_a_campaign_the_person_is_a_dm_of(self) -> None:
        await self.stage(await self.copy_of(self.two), replace=self.one.id)
        got = await self.run_marked()
        self.assertEqual((got["state"], got["result_id"]), ("done", self.one.id))
        self.assertEqual(self.after, [(GUILD, self.one.id, True)])
        self.assertEqual(self.imported[0]["replace_campaign_id"], self.one.id)

    async def test_the_lines_left_out_for_people_who_deleted_them_are_counted(self) -> None:
        async def importing(g: int, data: object, importer: int, **kw: Any) -> Any:
            kw["left_out"].lines = 3
            return await self.campaigns.import_backup(g, data, importer)

        host = self.host()
        host.import_backup = importing
        await self.stage(await self.copy_of(self.one))
        got = await self.run_marked(host)
        self.assertEqual((got["state"], got["left_out"]), ("done", 3))

    async def test_progress_moves_the_stage_the_parts_and_the_clock(self) -> None:
        seen: list[tuple[str, int]] = []

        async def importing(g: int, data: object, importer: int, **kw: Any) -> Any:
            row = await self.row()
            seen.append((row["stage"], row["done_parts"]))
            await kw["progress"](2, 3)
            seen.append(((await self.row())["stage"], (await self.row())["done_parts"]))
            return await self.campaigns.import_backup(g, data, importer)

        host = self.host()
        host.import_backup = importing
        await self.stage(await self.copy_of(self.one), wanted=3, without_parts=True)
        got = await self.run_marked(host)
        self.assertEqual(seen, [("restore_writing", 0), ("restore_writing", 2)])
        self.assertEqual(got["state"], "done")

    async def test_a_finished_restore_shows_all_its_parts_done(self) -> None:
        await self.stage(await self.copy_of(self.one), wanted=2, parts=(b"a", b"b"))
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        await self.restores.finish(GUILD, item, Outcome(result_id=self.one.id))
        self.assertEqual((await self.row())["done_parts"], 2)
        self.assertEqual(await self.parts_left(), 0)  # (the files go with the end)


class TheRefusals(Restoring):
    async def refused(self, word: str, *, host: Any = None, **stage: Any) -> None:
        main = stage.pop("main", None)
        restore_id = stage.get("restore_id", RESTORE_ID)
        await self.stage(main if main is not None else await self.copy_of(self.one), **stage)
        got = await self.run_marked(host)
        self.assertEqual((got["state"], got["stage"], got["error"]), ("failed", "failed", word))
        self.assertIsNone(got["main"])  # (emptied)
        self.assertEqual(got["id"], restore_id)
        self.assertEqual(self.after, [])

    async def test_a_person_who_left_the_server_is_not_allowed(self) -> None:
        await self.refused("not_allowed", host=self.host(in_server=False))

    async def test_a_campaign_that_is_gone_is_not_found(self) -> None:
        await self.refused("not_found", replace="0" * 32)

    async def test_only_a_dm_of_the_campaign_may_replace_it(self) -> None:
        await self.refused("not_allowed", replace=self.two.id)  # (Bob DMs "Two", not Alice)

    async def test_the_plan_is_asked_again_at_the_moment_of_use(self) -> None:
        await self.refused("plan", host=self.host(gate="Your plan has no copies."))

    async def test_a_campaign_being_played_is_never_replaced(self) -> None:
        await self.refused("playing", host=self.host(playing=True), replace=self.one.id)

    async def test_something_that_is_not_a_backup_is_refused(self) -> None:
        await self.refused("not_a_backup", main=b"just some words")

    async def test_a_backup_with_no_campaign_name_is_refused(self) -> None:
        await self.refused("not_a_backup", main=encode_backup({"campaign": {"name": ""}}))

    async def test_a_file_too_big_to_be_a_backup_is_refused(self) -> None:
        await self.refused("too_big", main=b"x" * (MAX_BACKUP_BYTES + 1))

    async def test_transcripts_files_that_are_not_the_set_are_refused(self) -> None:
        main = await self.copy_of(self.one, parts={"id": "p" * 8, "count": 2})
        await self.refused("parts", main=main, wanted=2, parts=(b"only one",))

    async def test_a_copy_with_transcripts_files_and_none_given_is_refused_unless_chosen(
        self,
    ) -> None:
        main = await self.copy_of(self.one, parts={"id": "p" * 8, "count": 2})
        await self.refused("parts", main=main, wanted=2)

    async def test_a_copy_chosen_without_its_transcripts_files_restores_without_them(self) -> None:
        main = await self.copy_of(self.one, parts={"id": "p" * 8, "count": 2})
        await self.stage(main, wanted=2, without_parts=True)
        got = await self.run_marked()
        self.assertEqual(got["state"], "done")
        self.assertEqual(self.imported[0]["parts"], [])

    async def test_no_room_in_the_plan_is_the_plan_word(self) -> None:
        await self.refused("plan", host=self.host(fail=CampaignError(NO_ROOM_TO_RESTORE)))

    async def test_a_bug_ends_the_row_as_failed_not_working(self) -> None:
        await self.refused("failed", host=self.host(fail=RuntimeError("boom")))

    def test_the_words_the_store_uses_become_the_pages_words(self) -> None:
        for message, word in (
            (NO_ROOM_TO_RESTORE, "plan"),
            ("Only this campaign's DM can replace it with a backup.", "not_allowed"),
            ("This copy has 2 transcripts files, and you gave 1. Give all.", "parts"),
            ("something unexpected", "failed"),
        ):
            self.assertEqual(web_restore.refusal_word(message), word)


class TheQueue(Restoring):
    async def test_only_a_waiting_row_is_claimed_and_the_oldest_first(self) -> None:
        now = int(time.time())
        await self.stage(b"1", restore_id="1" * 32, user=11, created=now - 10)
        await self.stage(b"2", restore_id="2" * 32, user=12, created=now - 20)
        await self.stage(b"3", restore_id="3" * 32, user=13, state="receiving")
        first = await self.restores.claim_next(GUILD)
        second = await self.restores.claim_next(GUILD)
        assert first is not None and second is not None
        self.assertEqual((first.id, second.id), ("2" * 32, "1" * 32))
        self.assertIsNone(await self.restores.claim_next(GUILD))
        self.assertEqual((await self.row("2" * 32))["stage"], "restore_reading")

    async def test_a_row_whose_worker_stopped_is_taken_again_a_working_one_is_not(self) -> None:
        now = int(time.time())
        await self.stage(b"1", restore_id="1" * 32, user=11, state="working", updated=now - 10)
        await self.stage(
            b"2", restore_id="2" * 32, user=12, state="working",
            updated=now - web_restore.RETAKE_AFTER_S - 5,
        )  # fmt: skip
        taken = await self.restores.claim_next(GUILD)
        assert taken is not None
        self.assertEqual(taken.id, "2" * 32)
        self.assertIsNone(await self.restores.claim_next(GUILD))

    async def test_only_the_worker_holding_the_claim_can_write_or_read_the_copy(self) -> None:
        await self.stage(b"secret copy", parts=(b"a", b"b"))
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        stale = web_restore.Restore(item.id, item.user_id, None, 0, False, item.claimed_at - 1)
        self.assertEqual(await self.restores.read(GUILD, stale), (None, []))
        self.assertFalse(await self.restores.finish(GUILD, stale, Outcome("failed")))
        self.assertEqual(await self.restores.read(GUILD, item), (b"secret copy", [b"a", b"b"]))
        self.assertTrue(await self.restores.finish(GUILD, item, Outcome("failed")))

    async def test_rows_go_a_day_after_they_began_and_their_files_with_them(self) -> None:
        now = int(time.time())
        await self.stage(
            b"old", restore_id="1" * 32, user=11, created=now - 86400 - 5, parts=(b"p",)
        )
        await self.stage(b"new", restore_id="2" * 32, user=12, created=now - 60)
        self.assertEqual(await self.restores.sweep(GUILD), 1)
        self.assertEqual(await self.parts_left(), 0)
        self.assertEqual((await self.row("2" * 32))["state"], "waiting")

    async def test_nothing_of_the_copy_is_logged(self) -> None:
        await self.stage(await self.copy_of(self.one))
        with self.assertLogs("dmbot.campaigns.web_restore", level="INFO") as logged:
            await self.run_marked()
        self.assertEqual(logged.output, ["INFO:dmbot.campaigns.web_restore:Website restore: done"])


class TheBot(Restoring):
    async def test_the_bots_round_applies_a_waiting_copy_in_each_server_it_is_in(self) -> None:
        from dmbot.bot import DMBot

        await self.stage(await self.copy_of(self.one))
        me = self.host()
        me.guilds = [SimpleNamespace(id=GUILD), SimpleNamespace(id=999)]
        me.web_restores = self.restores
        await DMBot.run_web_restores(me)
        self.assertEqual((await self.row())["state"], "done")

    async def test_a_person_is_in_the_server_from_the_cache_or_one_lookup(self) -> None:
        import discord

        from dmbot.bot import DMBot

        class Guild:
            def __init__(self, cached: set[int], found: set[int]) -> None:
                self.cached, self.found = cached, found

            def get_member(self, uid: int) -> object | None:
                return object() if uid in self.cached else None

            async def fetch_member(self, uid: int) -> object:
                if uid not in self.found:
                    raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
                return object()

        me = SimpleNamespace(get_guild=lambda g: Guild({1}, {2}) if g == GUILD else None)
        self.assertTrue(await DMBot.in_server(me, GUILD, 1))  # type: ignore[arg-type]
        self.assertTrue(await DMBot.in_server(me, GUILD, 2))  # type: ignore[arg-type]
        self.assertFalse(await DMBot.in_server(me, GUILD, 3))  # type: ignore[arg-type]
        self.assertFalse(await DMBot.in_server(me, 999, 1))  # type: ignore[arg-type]
