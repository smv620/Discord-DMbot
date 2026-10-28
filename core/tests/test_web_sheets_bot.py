"""The bot's side of the website's character sheets page (#2817): a queued link, forget or
read-again is claimed in order, checked again, and applied with the store calls Discord's buttons
use. The queue's table and the page are #2816's; here the rows are written directly. Invented
names and numbers throughout."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import PropertyMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign
from dmbot.memory import sheets
from dmbot.memory import web_sheets as ws
from dmbot.memory.sheet_store import SheetStore
from tests.test_memory_store import DM, GUILD_A, MemoryTest
from tests.test_sheets import answer

PLAYER, OTHER = 9, 10
NUMBER = 12345678
LINK = sheets.sheet_url(NUMBER)
NOW = 1_700_000_000


class FakeHost:
    """The running bot as `apply` sees it. The sheet calls go through the real store; D&D Beyond
    and Discord are stand-ins."""

    def __init__(self, store: SheetStore, campaign: Campaign) -> None:
        self.sheets = store
        self.campaign = campaign
        self.members = {PLAYER, OTHER, DM}
        self.read: list[int] = []
        self.refreshed = 0
        self.sheets_read = 0
        self.too_soon_link = False
        self.too_soon_read = False
        self.changed = 0
        self.fail: sheets.SheetError | None = None

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return self.campaign if campaign_id == self.campaign.id else None

    async def in_server(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.members

    async def fetch_sheet(self, character: int) -> dict[str, Any]:
        self.read.append(character)
        if self.fail is not None:
            raise self.fail
        return sheets.parse(answer())

    async def read_all(
        self, guild_id: int, campaign_id: str, progress: Callable[[], Awaitable[None]]
    ) -> None:
        self.refreshed += 1
        for _ in range(self.sheets_read):  # one tick per sheet, as the real refresh makes
            await progress()

    def link_too_soon(self, guild_id: int, user_id: int) -> bool:
        return self.too_soon_link

    def read_too_soon(self, guild_id: int, campaign_id: str) -> bool:
        return self.too_soon_read

    def sheets_changed(self, guild_id: int, campaign_id: str) -> None:
        self.changed += 1


class Base(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = SheetStore(self.db)
        self.queue = ws.ActionStore(self.db, clock=lambda: NOW)
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        self.host = FakeHost(self.store, campaign)
        self.pc = await self.add("Testa", type="player_character", played_by=PLAYER)

    async def put(
        self, action: str, *, user: int = PLAYER, entity: str | None = None, url: str = LINK,
        created: int = NOW, updated: int | None = None, state: str = "waiting",
    ) -> str:  # fmt: skip
        """One queued row, as the website's door would have written it."""
        rid = f"{len(await self.rows()):032x}"
        stage = {"waiting": "waiting", "working": "reading"}[state]
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, entity_id,"
                " action, url, state, stage, created_at, claimed_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    GUILD_A, self.c, rid, user,
                    (self.pc if entity is None else entity) if action != ws.READ_AGAIN else "",
                    action, url if action == ws.LINK else "", state, stage, created,
                    0, created if updated is None else updated,
                ),
            )  # fmt: skip
        return rid

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id, state, stage, error FROM web_sheet_actions ORDER BY seq"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def take_and_run(self) -> list[dict[str, Any]]:
        """The bot's round: every open row, in order."""
        while (item := await self.queue.claim_next(GUILD_A)) is not None:
            self.assertTrue(await ws.run_one(self.host, self.queue, GUILD_A, item))
        return await self.rows()

    async def state_of(self, rid: str) -> tuple[str, str]:
        (row,) = [r for r in await self.rows() if r["id"] == rid]
        return row["state"], row["error"]


class Linking(Base):
    async def test_a_players_own_link_is_done_and_the_sheet_is_kept(self) -> None:
        rid = await self.put(ws.LINK)
        self.assertEqual(await self.state_of(rid), ("waiting", ""))
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        (sheet,) = await self.store.sheets(GUILD_A, self.c)
        self.assertEqual((sheet.url, sheet.character), (LINK, NUMBER))
        self.assertIsNotNone(sheet.sheet)
        self.assertEqual(self.host.read, [NUMBER])

    async def test_another_players_character_is_not_allowed(self) -> None:
        rid = await self.put(ws.LINK, user=OTHER)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])
        self.assertEqual(self.host.read, [])  # D&D Beyond was never asked

    async def test_a_dm_cannot_link_for_a_player(self) -> None:
        rid = await self.put(ws.LINK, user=DM)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))

    async def test_someone_who_left_the_server_is_not_allowed(self) -> None:
        rid = await self.put(ws.LINK)
        self.host.members.discard(PLAYER)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))

    async def test_a_character_given_to_someone_else_since_is_not_allowed(self) -> None:
        rid = await self.put(ws.LINK)
        async with self.db.guild(GUILD_A) as conn:  # (a DM gave the character to someone else)
            await conn.execute(
                "UPDATE memory_entities SET played_by = %s WHERE id = %s", (OTHER, self.pc)
            )
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))

    async def test_a_link_of_another_shape_is_a_bad_link(self) -> None:
        rid = await self.put(ws.LINK, url="https://www.dndbeyond.com/characters/12345/extra")
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.BAD_LINK))

    async def test_a_link_after_a_link_inside_the_cooldown_is_too_soon(self) -> None:
        first = await self.put(ws.LINK)
        await self.take_and_run()
        self.host.too_soon_link = True
        second = await self.put(ws.LINK, url=sheets.sheet_url(999))
        await self.take_and_run()
        self.assertEqual(await self.state_of(first), ("done", ""))
        self.assertEqual(await self.state_of(second), ("failed", ws.TOO_SOON))

    async def test_a_sheet_that_is_not_public_says_so_and_nothing_is_kept(self) -> None:
        self.host.fail = sheets.SheetError("private", refused=True)
        rid = await self.put(ws.LINK)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ws.NOT_PUBLIC))  # linked, with a note
        (sheet,) = await self.store.sheets(GUILD_A, self.c)  # linked, as Discord does
        self.assertIsNone(sheet.sheet)

    async def test_a_site_that_did_not_answer_is_done_with_a_note_and_the_link_stays(self) -> None:
        self.host.fail = sheets.SheetError("busy")
        rid = await self.put(ws.LINK)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ws.UNREACHABLE))
        (sheet,) = await self.store.sheets(GUILD_A, self.c)
        self.assertEqual(sheet.url, LINK)

    async def test_a_link_taken_twice_after_a_slow_try_is_one_link(self) -> None:
        first = await self.put(ws.LINK)
        await self.take_and_run()
        # The door put the row back to waiting (updated later than created): applied again.
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE web_sheet_actions SET state = 'waiting', stage = 'waiting', claimed_at = 0,"
                " updated_at = %s WHERE id = %s",
                (NOW + 700, first),
            )
        await self.take_and_run()
        self.assertEqual(await self.state_of(first), ("done", ""))
        (sheet,) = await self.store.sheets(GUILD_A, self.c)
        self.assertEqual(sheet.url, LINK)


class Forgetting(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.link(GUILD_A, self.c, self.pc, NUMBER)

    async def test_forgetting_removes_the_link(self) -> None:
        rid = await self.put(ws.FORGET)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])

    async def test_forgetting_with_no_sheet_is_not_found(self) -> None:
        await self.store.unlink(GUILD_A, self.c, self.pc)
        rid = await self.put(ws.FORGET)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_FOUND))

    async def test_a_forget_taken_twice_after_a_slow_try_is_done_both_times(self) -> None:
        rid = await self.put(ws.FORGET)
        await self.take_and_run()
        async with self.db.guild(GUILD_A) as conn:  # the door put it back
            await conn.execute(
                "UPDATE web_sheet_actions SET state = 'waiting', stage = 'waiting', claimed_at = 0,"
                " updated_at = %s WHERE id = %s",
                (NOW + 700, rid),
            )
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))

    async def test_only_the_player_forgets(self) -> None:
        rid = await self.put(ws.FORGET, user=OTHER)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))
        self.assertEqual(len(await self.store.sheets(GUILD_A, self.c)), 1)


class ReadingAgain(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.link(GUILD_A, self.c, self.pc, NUMBER)

    async def test_a_dm_reads_the_campaigns_sheets_again(self) -> None:
        rid = await self.put(ws.READ_AGAIN, user=DM)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        self.assertEqual(self.host.refreshed, 1)

    async def test_with_nothing_linked_it_is_done_and_spends_no_cooldown(self) -> None:
        await self.store.unlink(GUILD_A, self.c, self.pc)
        self.host.too_soon_read = True  # (would refuse if it were counted)
        rid = await self.put(ws.READ_AGAIN, user=DM)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        self.assertEqual(self.host.refreshed, 0)

    async def test_each_sheet_read_moves_the_row(self) -> None:
        clock = [NOW]
        queue = ws.ActionStore(self.db, clock=lambda: clock[0])
        rid = await self.put(ws.READ_AGAIN, user=DM)
        item = await queue.claim_next(GUILD_A)
        assert item is not None

        async def progress() -> None:
            clock[0] += 5
            await queue.touch(GUILD_A, item)

        self.host.sheets_read = 3
        await self.host.read_all(GUILD_A, self.c, progress)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT updated_at FROM web_sheet_actions WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["updated_at"], NOW + 15)

    async def test_a_player_is_refused(self) -> None:
        rid = await self.put(ws.READ_AGAIN, user=PLAYER)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))
        self.assertEqual(self.host.refreshed, 0)

    async def test_inside_the_cooldown_it_is_too_soon(self) -> None:
        self.host.too_soon_read = True
        rid = await self.put(ws.READ_AGAIN, user=DM)
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.READ_TOO_SOON))
        self.assertEqual(self.host.refreshed, 0)


class TheQueue(Base):
    async def test_a_claim_moves_the_row_to_working_and_reading(self) -> None:
        rid = await self.put(ws.LINK)
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.assertEqual((item.id, item.retried), (rid, False))
        (row,) = await self.rows()
        self.assertEqual((row["state"], row["stage"]), ("working", "reading"))
        self.assertIsNone(await self.queue.claim_next(GUILD_A))  # nothing else, and not twice

    async def test_rows_are_applied_in_the_order_made(self) -> None:
        a = await self.put(ws.FORGET)
        b = await self.put(ws.LINK)
        first = await self.queue.claim_next(GUILD_A)
        assert first is not None and first.id == a
        self.assertIsNone(await self.queue.claim_next(GUILD_A))  # b waits for a (same campaign)
        await self.queue.finish(GUILD_A, first, "")
        second = await self.queue.claim_next(GUILD_A)
        assert second is not None and second.id == b

    async def test_only_the_claiming_worker_can_finish_a_row(self) -> None:
        await self.put(ws.LINK)
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.assertTrue(await self.queue.finish(GUILD_A, item, ""))
        self.assertFalse(await self.queue.finish(GUILD_A, item, ws.SERVER))  # already over

    async def test_a_bug_ends_as_server_not_as_working(self) -> None:
        rid = await self.put(ws.LINK)

        async def boom(*_: Any) -> Any:
            raise RuntimeError("a database error quoting a link")

        self.host.get_campaign = boom  # type: ignore[method-assign,assignment]
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.SERVER))

    async def test_a_stalled_claim_is_taken_again_and_marked_retried(self) -> None:
        await self.put(ws.LINK, state="working")
        async with self.db.guild(GUILD_A) as conn:  # claimed long ago by a bot that stopped
            await conn.execute("UPDATE web_sheet_actions SET claimed_at = 1000, updated_at = %s",
                               (NOW + 1,))  # fmt: skip
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.assertTrue(item.retried)

    async def test_a_retake_of_a_forget_that_already_worked_is_done(self) -> None:
        await self.store.link(GUILD_A, self.c, self.pc, NUMBER)
        rid = await self.put(ws.FORGET)
        item = await self.queue.claim_next(GUILD_A)  # same second as made: updated_at == created_at
        assert item is not None and not item.retried
        await self.store.unlink(GUILD_A, self.c, self.pc)  # it worked, then the bot died
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE web_sheet_actions SET claimed_at = 1000")
        again = await self.queue.claim_next(GUILD_A)
        assert again is not None
        self.assertTrue(again.retried)
        self.assertTrue(await ws.run_one(self.host, self.queue, GUILD_A, again))
        self.assertEqual(await self.state_of(rid), ("done", ""))

    async def test_old_rows_are_swept(self) -> None:
        await self.put(ws.LINK, created=NOW - ws.KEEP_FOR_S - 5, state="waiting")
        await self.put(ws.READ_AGAIN, user=DM)
        self.assertEqual(await self.queue.sweep(GUILD_A), 1)
        self.assertEqual(len(await self.rows()), 1)


class TheSweep(unittest.IsolatedAsyncioTestCase):
    """The poll loop does not wait for a sheet read (#2817): a choice is a background task, one at
    a time for a server, so the other website actions in the loop are not held up."""

    async def test_the_sweep_returns_while_a_choice_is_still_being_applied(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        queue: dict[int, list[Any]] = {1: [SimpleNamespace(campaign_id="c")] * 2, 2: []}

        async def claim(guild_id: int) -> Any:
            return queue[guild_id].pop(0) if queue[guild_id] else None

        async def slow(self: Any, store: Any, guild_id: int, item: Any) -> None:
            started.set()
            await release.wait()

        b: Any = DMBot.__new__(DMBot)
        b.web_sheets = SimpleNamespace(claim_next=claim)
        b.sheets = object()
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_sheet_choice", slow),
        ):
            await asyncio.wait_for(b.run_web_sheets(), timeout=1)  # returns at once
            await started.wait()
            await asyncio.wait_for(b.run_web_sheets(), timeout=1)  # and again
            self.assertEqual(len(b._sheet_tasks), 1)  # one at a time for the server
            self.assertEqual(len(queue[1]), 1)  # the second was not even claimed
            release.set()
            await asyncio.gather(*b._sheet_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(b._sheet_tasks, {})  # forgotten when done

    async def test_without_a_store_nothing_happens(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.web_sheets = None
        b.sheets = None
        await b.run_web_sheets()
