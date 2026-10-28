"""The bot's side of restoring a backup from the website (#2746, #2745): a staged copy is applied
with the same code and checks as Discord's `/dmbot restore`, the shared progress shape is kept up,
a refusal is one plain word, the bytes never outlive the end, and a row goes after 24 hours.
Invented names throughout."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns import CampaignError, web_restore
from dmbot.campaigns import parts as campaign_parts
from dmbot.campaigns import store as campaign_store
from dmbot.campaigns.store import MAX_BACKUP_BYTES, NO_ROOM_TO_RESTORE, PartsError, encode_backup
from dmbot.campaigns.web_restore import Outcome, Restore, WebRestores
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
        self.gate_calls: list[tuple[int, int, str | None]] = []

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
            self.gate_calls.append((g, u, None if replacing is None else replacing.id))
            return gate

        async def is_campaign_playing(g: int, c: str) -> bool:
            return playing

        async def member(g: int, u: int) -> bool:
            return in_server

        async def restore_copy(g: int, data: object, importer: int, **kw: Any) -> Any:
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
            restorer_in_server=member,
            session_lock=lambda g: asyncio.Lock(),
            restore_copy=restore_copy,
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
            return await self.campaigns.import_backup(
                g, data, importer, left_out=kw["left_out"], in_transaction=kw["in_transaction"]
            )

        host = self.host()
        host.restore_copy = importing
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
            return await self.campaigns.import_backup(
                g, data, importer, in_transaction=kw["in_transaction"]
            )

        host = self.host()
        host.restore_copy = importing
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


class TheAftermath(Restoring):
    async def test_tidying_that_fails_after_the_copy_is_in_is_still_a_done_restore(self) -> None:
        host = self.host()

        async def after_restore(g: int, c: str, replaced: bool) -> None:
            raise RuntimeError("analyze failed")

        host.after_restore = after_restore
        await self.stage(await self.copy_of(self.one))
        got = await self.run_marked(host)
        self.assertEqual((got["state"], got["error"]), ("done", ""))
        self.assertIsNotNone(await self.campaigns.get(GUILD, got["result_id"]))

    async def test_a_blip_writing_the_outcome_is_tried_again(self) -> None:
        from unittest.mock import patch

        real = self.restores.finish
        calls = 0

        async def flaky(*args: Any) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("blip")
            return await real(*args)

        await self.stage(await self.copy_of(self.one))
        with (
            patch.object(web_restore, "FINISH_PAUSE_S", 0),
            patch.object(self.restores, "finish", flaky),
        ):
            got = await self.run_marked()
        self.assertEqual((calls, got["state"]), (2, "done"))

    async def test_an_outcome_that_cannot_be_written_stops_the_round(self) -> None:
        from unittest.mock import patch

        async def broken(*args: Any) -> bool:
            raise RuntimeError("down")

        await self.stage(await self.copy_of(self.one))
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        with (
            patch.object(web_restore, "FINISH_PAUSE_S", 0),
            patch.object(self.restores, "finish", broken),
        ):
            self.assertFalse(await web_restore.run_one(self.host(), self.restores, GUILD, item))

    async def test_the_session_lock_is_held_for_the_playing_check_and_the_import(self) -> None:
        lock = asyncio.Lock()
        held: list[bool] = []
        host = self.host()
        host.session_lock = lambda g: lock

        async def playing(g: int, c: str) -> bool:
            held.append(lock.locked())
            return False

        real = host.restore_copy

        async def importing(g: int, data: object, importer: int, **kw: Any) -> Any:
            held.append(lock.locked())
            return await real(g, data, importer, **kw)

        host.is_campaign_playing = playing
        host.restore_copy = importing
        await self.stage(await self.copy_of(self.two), replace=self.one.id)
        await self.run_marked(host)
        self.assertEqual(held, [True, True])
        self.assertFalse(lock.locked())


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

    def test_the_stores_refusals_become_the_pages_words_by_their_constants(self) -> None:
        for error, word in (
            (CampaignError(NO_ROOM_TO_RESTORE), "plan"),
            (CampaignError(campaign_store.NOT_HERE), "not_found"),
            (CampaignError(campaign_store.NOT_THIS_DM), "not_allowed"),
            (CampaignError(campaign_store.TOO_BIG), "too_big"),
            (CampaignError(campaign_parts.TOO_BIG_PART), "too_big"),
            (CampaignError(campaign_store.NOT_A_BACKUP), "not_a_backup"),
            (CampaignError(campaign_store.DAMAGED), "not_a_backup"),
            (CampaignError(campaign_store.DAMAGED_DM), "not_a_backup"),
            (CampaignError(campaign_store.DAMAGED_CONFIRMATION), "not_a_backup"),
            (CampaignError(campaign_store.DAMAGED_OPTIONAL_RULE), "not_a_backup"),
            (CampaignError(campaign_parts.DAMAGED), "not_a_backup"),
            (PartsError(campaign_store.PARTS_NOT_THIS_COPY), "parts"),
            (PartsError("This copy has 2 transcripts files, and you gave 1."), "parts"),
            (CampaignError("something unexpected"), "failed"),
        ):
            self.assertEqual(web_restore.refusal_word(error), word, str(error))


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

    async def test_a_row_a_stopped_bot_left_working_is_failed_never_applied_again(self) -> None:
        now = int(time.time())
        await self.stage(b"1", restore_id="1" * 32, user=11, state="working", updated=now - 10)
        await self.stage(
            b"2", restore_id="2" * 32, user=12, state="working",
            updated=now - web_restore.STALLED_AFTER_S - 5,
        )  # fmt: skip
        self.assertEqual(await self.restores.fail_stalled(GUILD), 1)
        stalled = await self.row("2" * 32)
        self.assertEqual(
            (stalled["state"], stalled["error"], stalled["main"]), ("failed", "failed", None)
        )
        self.assertEqual((await self.row("1" * 32))["state"], "working")  # (still moving)
        self.assertIsNone(await self.restores.claim_next(GUILD))  # (nothing is retaken)

    async def test_a_waiting_row_of_another_server_is_never_claimed_or_read(self) -> None:
        await self.stage(b"mine")
        self.assertIsNone(await self.restores.claim_next(GUILD + 1))
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        self.assertEqual(await self.restores.read(GUILD + 1, item), (None, []))

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


class TheUpkeep(Restoring):
    async def stage_in(self, guild: int, restore_id: str, state: str, created: int) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO web_restores (guild_id, id, user_id, name, parts_wanted, main,"
                " state, stage, created_at, updated_at)"
                " VALUES (%s, %s, %s, 'Copy', 0, %s, %s, %s, %s, %s)",
                (
                    guild, restore_id, int(restore_id[:4], 16) + 1, b"secret copy", state,
                    "restore_waiting" if state == "waiting" else "restore_reading",
                    created, created,
                ),
            )  # fmt: skip

    async def state_in(self, guild: int, restore_id: str) -> dict[str, Any] | None:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT state, error, main, updated_at FROM web_restores"
                " WHERE guild_id = %s AND id = %s",
                (guild, restore_id),
            )
            found = await cur.fetchone()
        return None if found is None else dict(found)

    async def test_the_servers_with_rows_are_listed_the_bot_left_ones_too(self) -> None:
        now = int(time.time())
        await self.stage_in(GUILD, "1" * 32, "waiting", now)
        await self.stage_in(GUILD + 7, "2" * 32, "waiting", now)
        self.assertEqual(await self.restores.guilds(), sorted([GUILD, GUILD + 7]))

    async def install(self, guild: int, *, left: bool) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via, left_at)"
                " VALUES (%s, NULL, 1, 'link', %s)",
                (guild, 5 if left else None),
            )

    async def test_a_server_dmbot_has_left_ends_its_rows_and_sweeps_them_after_a_day(self) -> None:
        old = int(time.time()) - 86400 - 60
        gone = GUILD + 7
        await self.install(gone, left=True)
        await self.stage_in(gone, "2" * 32, "waiting", int(time.time()))
        await self.stage_in(gone, "3" * 32, "receiving", int(time.time()))
        await self.stage_in(gone, "4" * 32, "waiting", old)
        await web_restore.tidy(self.restores)
        for restore_id in ("2" * 32, "3" * 32):
            row = await self.state_in(gone, restore_id)
            assert row is not None
            self.assertEqual(
                (row["state"], row["error"], row["main"]), ("failed", "not_allowed", None)
            )
        self.assertIsNone(await self.state_in(gone, "4" * 32))  # (a day old: gone, bytes and all)

    async def test_the_left_decision_is_the_install_record_and_never_the_bots_cache(self) -> None:
        # a server DMbot is in (installed, not left) keeps its rows however the cache looks
        here = GUILD + 8
        await self.install(here, left=False)
        await self.stage_in(here, "5" * 32, "waiting", int(time.time()))
        # the test server has a campaign and no install record: DMbot's, as the website decides
        await self.stage_in(GUILD, "6" * 32, "waiting", int(time.time()))
        await web_restore.tidy(self.restores)  # (there is no cache to ask: it takes none)
        for guild, restore_id in ((here, "5" * 32), (GUILD, "6" * 32)):
            row = await self.state_in(guild, restore_id)
            assert row is not None
            self.assertEqual((row["state"], row["error"]), ("waiting", ""))
        self.assertTrue(await self.restores.has_left(GUILD + 99))  # (no record, no campaign)
        self.assertFalse(await self.restores.has_left(here))
        self.assertFalse(await self.restores.has_left(GUILD))

    async def test_a_server_dmbot_is_in_fails_its_stalled_row(self) -> None:
        now = int(time.time())
        await self.stage(
            b"w", restore_id="2" * 32, user=12, state="working",
            updated=now - web_restore.STALLED_AFTER_S - 5,
        )  # fmt: skip
        await web_restore.tidy(self.restores)
        row = await self.state_in(GUILD, "2" * 32)
        assert row is not None
        self.assertEqual((row["state"], row["error"]), ("failed", "failed"))

    async def test_the_queue_is_kept_alive_by_a_task_of_its_own_while_a_restore_runs(self) -> None:
        start = int(time.time())
        clock = [float(start)]
        store = WebRestores(self.db, clock=lambda: clock[0])
        await self.stage(await self.copy_of(self.one), restore_id="1" * 32, created=start - 900)
        await self.stage_in(GUILD, "2" * 32, "waiting", start - 600)  # (queued behind it)
        item = await store.claim_next(GUILD)
        assert item is not None and item.id == "1" * 32
        seen: list[tuple[int, int]] = []
        host = self.host()
        real = host.restore_copy

        async def importing(g: int, data: object, importer: int, **kw: Any) -> Any:
            before = (await self.row("2" * 32))["updated_at"]
            clock[0] += 120  # two minutes pass inside the restore...
            await store.keep_waiting_alive()  # ...and the other task touches the queue
            seen.append((before, (await self.row("2" * 32))["updated_at"]))
            return await real(g, data, importer, **kw)

        host.restore_copy = importing
        done = await web_restore.apply(host, store, GUILD, item)
        self.assertEqual(done.error, "")
        self.assertEqual(seen, [(start - 600, start + 120)])
        self.assertEqual((await self.row("2" * 32))["state"], "waiting")

    async def test_a_hung_worker_lets_the_queue_show_as_stalled(self) -> None:
        start = int(time.time())
        clock = [float(start)]
        store = WebRestores(self.db, clock=lambda: clock[0])
        await self.stage_in(GUILD, "2" * 32, "waiting", start - 600)
        await self.stage_in(GUILD, "4" * 32, "working", start - 600)
        gone = GUILD + 7
        await self.install(gone, left=True)
        await self.stage_in(gone, "5" * 32, "waiting", start - 600)
        await store.keep_waiting_alive()  # (the worker has shown no sign of life yet)
        self.assertEqual((await self.row("2" * 32))["updated_at"], start - 600)
        store.beat()
        clock[0] += web_restore.STALLED_AFTER_S - 1
        await store.keep_waiting_alive()  # (a fresh beat: kept alive)
        self.assertEqual((await self.row("2" * 32))["updated_at"], start + 299)
        self.assertEqual(
            (await self.row("4" * 32))["updated_at"], start - 600
        )  # (working: its own)
        theirs = await self.state_in(gone, "5" * 32)  # (a server DMbot left: upkeep's)
        assert theirs is not None
        self.assertEqual(theirs["updated_at"], start - 600)
        clock[0] += 2  # no round and no progress for the whole stall time: it hung
        await store.keep_waiting_alive()
        self.assertEqual((await self.row("2" * 32))["updated_at"], start + 299)

    async def test_a_round_and_each_progress_call_are_signs_of_life(self) -> None:
        clock = [1000.0]
        store = WebRestores(self.db, clock=lambda: clock[0])
        self.assertIsNone(store._beat)
        await store.claim_next(GUILD)
        self.assertEqual(store._beat, 1000.0)
        clock[0] = 1100.0
        item = Restore("1" * 32, 1, None, 0, False, 0)  # (nothing to move: only the beat)
        await store.move(GUILD, item, done_parts=1)
        self.assertEqual(store._beat, 1100.0)

    async def test_a_server_with_rows_stays_listed_and_an_empty_one_goes(self) -> None:
        await self.stage_in(GUILD, "3" * 32, "waiting", int(time.time()))
        await self.restores.forget_if_empty(GUILD)
        self.assertIn(GUILD, await self.restores.guilds())
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM web_restores WHERE guild_id = %s", (GUILD,))
        await self.restores.forget_if_empty(GUILD)
        self.assertNotIn(GUILD, await self.restores.guilds())
        await self.restores.forget_if_empty(GUILD)  # (nothing listed: nothing to do)

    async def test_the_sweep_never_deletes_a_row_being_worked(self) -> None:
        old = int(time.time()) - 86400 - 60
        await self.stage(b"w", state="working", created=old, updated=int(time.time()))
        self.assertEqual(await self.restores.sweep(GUILD), 0)
        self.assertEqual((await self.row())["state"], "working")

    async def test_one_failing_step_does_not_skip_the_rest(self) -> None:
        from unittest.mock import patch

        old = int(time.time()) - 86400 - 60
        await self.stage_in(GUILD, "1" * 32, "waiting", old)

        async def broken(guild_id: int) -> int:
            raise RuntimeError("down")

        with patch.object(self.restores, "fail_stalled", broken):
            await web_restore.tidy(self.restores)
        self.assertIsNone(await self.state_in(GUILD, "1" * 32))  # (the sweep still ran)

    async def test_the_restore_loop_waits_until_the_bot_is_ready_before_anything(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from dmbot.recaps.discord import RecapFeatures

        class Stop(Exception):
            pass

        order: list[str] = []
        ready = asyncio.Event()

        async def wait_until_ready() -> None:
            order.append("wait")
            await ready.wait()
            order.append("ready")

        async def tidy() -> None:
            order.append("tidy")

        async def run() -> None:
            order.append("run")

        me = SimpleNamespace(
            wait_until_ready=wait_until_ready, tidy_web_restores=tidy, run_web_restores=run
        )
        task = asyncio.ensure_future(RecapFeatures._web_restore_loop(me))  # type: ignore[arg-type]
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(order, ["wait"])  # (nothing is judged before the bot is ready)
        with patch("dmbot.recaps.discord.asyncio.sleep", AsyncMock(side_effect=Stop)):
            ready.set()
            with self.assertRaises(Stop):
                await task
        self.assertEqual(order, ["wait", "ready", "tidy", "run"])

    async def test_the_queue_task_waits_until_ready_then_touches(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot.recaps.discord import RecapFeatures

        class Stop(Exception):
            pass

        order: list[str] = []

        async def wait_until_ready() -> None:
            order.append("ready")

        async def alive() -> None:
            order.append("alive")

        me = SimpleNamespace(wait_until_ready=wait_until_ready, keep_web_restores_alive=alive)
        sleeping = patch("dmbot.recaps.discord.asyncio.sleep", AsyncMock(side_effect=Stop))
        with sleeping, self.assertRaises(Stop):
            await RecapFeatures._web_restore_alive_loop(me)  # type: ignore[arg-type]
        self.assertEqual(order, ["ready", "alive"])

    async def test_the_shared_loop_for_recap_requests_never_runs_a_restore(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot.recaps.discord import RecapFeatures

        class Stop(Exception):
            pass

        shared = SimpleNamespace(
            **{
                name: AsyncMock()
                for name in (
                    "run_recap_requests",
                    "run_uploaded_checks",
                    "run_name_answers",
                    "run_name_actions",
                    "run_web_settings",
                    "run_peek_notices",
                    "run_web_delete_notices",
                    "run_web_backups",
                    "run_web_restores",
                )
            },
            recap_requests=None,
            guilds=[],
            web_delete_notices=None,
            web_backups=None,
        )
        sleeping = patch("dmbot.recaps.discord.asyncio.sleep", AsyncMock(side_effect=Stop))
        with sleeping, self.assertRaises(Stop):
            await RecapFeatures._recap_request_loop(shared)  # type: ignore[arg-type]
        shared.run_web_backups.assert_awaited()  # (the others ran)
        shared.run_web_restores.assert_not_awaited()


class TheCopyThatIsIn(Restoring):
    async def test_the_row_is_done_in_the_same_transaction_as_the_copy(self) -> None:
        """No `finish` at all: the copy marks its own row done before it commits."""
        await self.stage(await self.copy_of(self.one))
        item = await self.restores.claim_next(GUILD)
        assert item is not None
        done = await web_restore.apply(self.host(), self.restores, GUILD, item)
        got = await self.row()
        self.assertEqual((got["state"], got["result_id"]), ("done", done.result_id))
        self.assertIsNone(got["main"])

    async def test_a_restore_that_fails_leaves_no_done_row_and_no_campaign_behind(self) -> None:
        before = len(await self.campaigns.list_campaigns(GUILD))
        await self.stage(await self.copy_of(self.one))  # (a new campaign: a leak would show)

        async def broken(conn: Any, campaign_id: str) -> None:
            raise RuntimeError("the copy rolled back")

        host = self.host()
        real = host.restore_copy

        async def failing(g: int, data: object, importer: int, **kw: Any) -> Any:
            kw["in_transaction"] = broken
            return await real(g, data, importer, **kw)

        host.restore_copy = failing
        got = await self.run_marked(host)
        self.assertEqual((got["state"], got["error"]), ("failed", "failed"))
        self.assertIsNone(got["result_id"])
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), before)  # (rolled back)

    async def test_the_plan_is_asked_with_the_campaign_being_replaced_or_none(self) -> None:
        await self.stage(await self.copy_of(self.two), replace=self.one.id)
        await self.run_marked()
        await self.stage(await self.copy_of(self.one), restore_id="b" * 32, user=ALICE.id)
        await self.run_marked()
        self.assertEqual(self.gate_calls, [(GUILD, ALICE.id, self.one.id), (GUILD, ALICE.id, None)])

    async def test_the_lines_of_people_who_deleted_their_transcripts_are_left_out(self) -> None:
        from dmbot.transcript.store import TranscriptStore

        speaker = 4141
        sid = "5" * 32
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
                " line_count, speakers) VALUES (%s, %s, %s, 1000, 4600, 3, %s)",
                (sid, GUILD, self.one.id, [speaker]),
            )
            for n in range(3):
                await conn.execute(
                    "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                    " heard, text) VALUES (%s, %s, %s, %s, 'raw', 'clean')",
                    (GUILD, sid, n * 1000, speaker),
                )
        main, files = await self.campaigns.export_set(GUILD, self.one.id)
        await TranscriptStore(self.db).delete_person(GUILD, speaker)
        await self.stage(encode_backup(main), wanted=len(files), parts=tuple(files))
        got = await self.run_marked()
        self.assertEqual((got["state"], got["left_out"]), ("done", 3))


class TheBotsOwnSteps(Restoring):
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
        self.assertTrue(await DMBot.restorer_in_server(me, GUILD, 1))  # type: ignore[arg-type]
        self.assertTrue(await DMBot.restorer_in_server(me, GUILD, 2))  # type: ignore[arg-type]
        self.assertFalse(await DMBot.restorer_in_server(me, GUILD, 3))  # type: ignore[arg-type]
        self.assertFalse(await DMBot.restorer_in_server(me, 999, 1))  # type: ignore[arg-type]

    async def test_the_planner_refresh_and_the_rules_channel_are_done_for_a_replace_only(
        self,
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from dmbot.bot import DMBot

        me = SimpleNamespace(
            memory=SimpleNamespace(analyze=AsyncMock()), _rules_archive_changed=MagicMock()
        )
        await DMBot.after_restore(me, GUILD, "c1", True)  # type: ignore[arg-type]
        me.memory.analyze.assert_awaited_once_with(GUILD)
        me._rules_archive_changed.assert_called_once_with(GUILD, "c1")
        me2 = SimpleNamespace(
            memory=SimpleNamespace(analyze=AsyncMock()), _rules_archive_changed=MagicMock()
        )
        await DMBot.after_restore(me2, GUILD, "c2", False)  # type: ignore[arg-type]
        me2.memory.analyze.assert_awaited_once_with(GUILD)
        me2._rules_archive_changed.assert_not_called()

    async def test_a_discord_error_is_not_not_in_the_server(self) -> None:
        import discord

        from dmbot.bot import DMBot

        class Guild:
            def get_member(self, uid: int) -> None:
                return None

            async def fetch_member(self, uid: int) -> None:
                raise discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")

        me = SimpleNamespace(get_guild=lambda g: Guild())
        with self.assertRaises(discord.HTTPException):
            await DMBot.restorer_in_server(me, GUILD, 1)  # type: ignore[arg-type]

    async def test_an_error_from_discord_ends_the_restore_as_failed_to_try_again(self) -> None:
        import discord

        async def raising(g: int, u: int) -> bool:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")

        host = self.host()
        host.restorer_in_server = raising
        await self.stage(await self.copy_of(self.one))
        got = await self.run_marked(host)
        self.assertEqual((got["state"], got["error"]), ("failed", "failed"))
