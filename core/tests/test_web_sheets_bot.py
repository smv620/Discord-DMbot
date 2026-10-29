"""The bot's side of the website's character sheets page (#2817): a queued link, forget or
read-again is claimed in order, checked again, and applied with the store calls Discord's buttons
use. The queue's table and the page are #2816's; here the rows are written directly. Invented
names and numbers throughout."""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, PropertyMock, patch

import aiohttp

from dmbot import fetch, plan_rules
from dmbot.ai import AIError, Reply
from dmbot.bot import DMBot
from dmbot.campaigns import Campaign
from dmbot.memory import sheet_document, sheet_import, sheets
from dmbot.memory import web_sheets as ws
from dmbot.memory.sheet_store import SheetStore
from tests.test_memory_store import DM, GUILD_A, MemoryTest
from tests.test_sheet_import import answer as ai_answer
from tests.test_sheets import answer

PLAYER, OTHER = 9, 10
NUMBER = 12345678
LINK = sheets.sheet_url(NUMBER)
DOC = "https://docs.example.com/belleros-sheet"
SHEET_TEXT = "Belleros\nWood Elf Ranger 3 / Druid 3\nAC 18  HP 26\nSTR 10 DEX 16 CON 14 INT 10"
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
        self.too_soon_document = False
        self.plan_refusal: str | None = None
        self.sheet_ai: Any = SimpleNamespace(
            complete=AsyncMock(return_value=Reply(ai_answer(), False))
        )
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

    def document_too_soon(self, guild_id: int, user_id: int, campaign_id: str) -> bool:
        return self.too_soon_document

    async def plan_gate(
        self, action: plan_rules.Action, guild_id: int, campaign: Campaign, user_id: int
    ) -> str | None:
        return self.plan_refusal

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
        payload: str = "",
    ) -> str:  # fmt: skip
        """One queued row, as the website's door would have written it."""
        rid = f"{len(await self.rows()):032x}"
        stage = {"waiting": "waiting", "working": "reading"}[state]
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, entity_id,"
                " action, url, payload, state, stage, created_at, claimed_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    GUILD_A, self.c, rid, user,
                    (self.pc if entity is None else entity) if action != ws.READ_AGAIN else "",
                    action, url if action in (ws.LINK, ws.DOCUMENT) else "", payload,
                    state, stage, created,
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

    async def left_on_row(self, rid: str) -> tuple[str, str]:
        """The link and the typed form still on a row."""
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT url, payload FROM web_sheet_actions WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return row["url"], row["payload"]

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
        # The door put the row back to waiting (updated later than created): applied again. (It
        # only does that to a row still being worked on, which still holds its link.)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE web_sheet_actions SET state = 'waiting', stage = 'waiting', claimed_at = 0,"
                " updated_at = %s, url = %s WHERE id = %s",
                (NOW + 700, LINK, first),
            )
        await self.take_and_run()
        self.assertEqual(await self.state_of(first), ("done", ""))
        (sheet,) = await self.store.sheets(GUILD_A, self.c)
        self.assertEqual(sheet.url, LINK)


def form(**more: Any) -> str:
    """A typed sheet's JSON, as the website's door writes it."""
    data: dict[str, Any] = {"className": "Cleric", "level": 3, "species": "Hill Dwarf", "names": ""}
    return json.dumps({**data, **more}, separators=(",", ":"))


class Typing(Base):
    async def test_a_typed_sheet_is_saved_for_the_players_own_character_and_replaces_a_linked_one(
        self,
    ) -> None:
        await self.put(ws.LINK)
        await self.take_and_run()
        (linked,) = await self.store.sheets(GUILD_A, self.c)
        self.assertEqual(linked.url, LINK)
        self.host.too_soon_link = False
        rid = await self.put(ws.TYPED, payload=form(names="Bless, Cure Wounds"))
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        (kept,) = await self.store.sheets(GUILD_A, self.c)
        self.assertIsNone(kept.url)  # typed: no longer linked
        assert kept.sheet is not None
        self.assertEqual(kept.sheet["classes"][0]["name"], "Cleric")
        self.assertEqual(kept.sheet["features"], ["Bless", "Cure Wounds"])
        self.assertEqual(kept.sheet["species"], "Hill Dwarf")

    async def test_a_typed_sheet_for_another_players_character_is_not_allowed(self) -> None:
        rid = await self.put(ws.TYPED, user=OTHER, payload=form())
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])

    async def test_a_typed_sheet_whose_names_are_split_on_commas_and_semicolons_and_kept_to_20(
        self,
    ) -> None:
        names = "; ".join(f"Spell {i}" for i in range(30)) + ", Last one"
        await self.put(ws.TYPED, payload=form(names=names))
        await self.take_and_run()
        (kept,) = await self.store.sheets(GUILD_A, self.c)
        assert kept.sheet is not None
        self.assertEqual(len(kept.sheet["features"]), sheets.TYPED_NAMES_MAX)
        self.assertEqual(kept.sheet["features"][:2], ["Spell 0", "Spell 1"])

    async def test_a_form_of_the_wrong_shape_is_a_bad_typed_one(self) -> None:
        for payload in ("not json", "[]", form(level=0), form(level=21), form(level=True),
                        form(className=""), form(names="x" * 1501)):  # fmt: skip
            with self.subTest(payload[:30]):
                rid = await self.put(ws.TYPED, payload=payload)
                await self.take_and_run()
                self.assertEqual(await self.state_of(rid), ("failed", ws.BAD_TYPED))
                self.host.too_soon_link = False
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])

    async def test_a_typed_sheet_inside_the_link_cooldown_is_too_soon(self) -> None:
        self.host.too_soon_link = True
        rid = await self.put(ws.TYPED, payload=form())
        await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.TOO_SOON))


class Documents(Base):
    def setUp(self) -> None:
        sheet_document._busy.clear()
        sheet_document._reads.clear()

    def opened(self, data: bytes = SHEET_TEXT.encode(), name: str = "sheet.txt") -> Any:
        return patch.object(fetch, "fetch", AsyncMock(return_value=fetch.Fetched(data, name)))

    async def test_a_document_link_is_read_and_saved_as_a_typed_sheet(self) -> None:
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("done", ""))
        opened.assert_awaited_once_with(DOC, max_bytes=sheet_import.FILE_MAX_BYTES)
        (kept,) = await self.store.sheets(GUILD_A, self.c)
        assert kept.sheet is not None
        self.assertEqual(kept.sheet["name"], "Belleros")
        self.assertIsNone(kept.url)  # typed, not linked
        self.assertEqual(self.host.sheet_ai.complete.await_count, 1)
        self.assertEqual(self.host.changed, 1)

    async def test_the_row_is_touched_at_each_step(self) -> None:
        clock = [NOW]
        self.queue = ws.ActionStore(self.db, clock=lambda: clock[0])
        await self.put(ws.DOCUMENT, url=DOC)
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        seen: list[int] = []
        real = self.queue.touch

        async def touch(guild_id: int, it: ws.Action) -> bool:
            clock[0] += 1
            seen.append(clock[0])
            return await real(guild_id, it)

        with self.opened(), patch.object(self.queue, "touch", touch):
            self.assertEqual(await ws.apply(self.host, GUILD_A, item, self.queue), "")
        self.assertEqual(len(seen), 3)  # link opened, AI answered, saved

    async def test_a_link_that_is_not_public_is_refused_and_nothing_is_fetched(self) -> None:
        for address in ("https://127.0.0.1/sheet", "https://10.0.0.5/sheet",
                        "https://169.254.169.254/latest/meta-data",
                        "http://example.com/sheet",
                        "https://user:pw@example.com/sheet"):  # fmt: skip
            with self.subTest(address):
                rid = await self.put(ws.DOCUMENT, url=address)
                with patch.object(aiohttp.ClientSession, "get") as get:
                    await self.take_and_run()
                get.assert_not_called()  # not even one request was made
                self.assertEqual(await self.state_of(rid), ("failed", ws.BAD_DOCUMENT))
                self.host.too_soon_document = False
        self.assertEqual(self.host.sheet_ai.complete.await_count, 0)
        self.assertEqual(sum(sheet_document._reads.values()), 0)  # no read was spent

    async def test_a_name_that_resolves_to_this_machine_is_refused(self) -> None:
        rid = await self.put(ws.DOCUMENT, url="https://localhost/sheet")
        await self.take_and_run()  # the resolver refuses it; no connection is made
        self.assertEqual(await self.state_of(rid), ("failed", ws.BAD_DOCUMENT))

    async def test_a_redirect_to_a_private_address_is_refused(self) -> None:
        opened: list[str] = []

        class Redirect:
            status = 302
            headers: ClassVar[dict[str, str]] = {
                "Location": "https://169.254.169.254/latest/meta-data"
            }

            async def __aenter__(self) -> Redirect:
                return self

            async def __aexit__(self, *_: Any) -> None:
                return None

        def get(_session: Any, url: Any, **_: Any) -> Redirect:
            opened.append(str(url))
            return Redirect()

        rid = await self.put(ws.DOCUMENT, url=DOC)
        with patch.object(aiohttp.ClientSession, "get", get):
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.BAD_DOCUMENT))
        self.assertEqual(opened, [DOC])  # the second address was checked, never requested

    async def test_a_document_longer_than_the_cap_is_too_big(self) -> None:
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened(b"x" * (sheet_import.FILE_MAX_BYTES + 1)):
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.TOO_BIG))
        self.assertEqual(self.host.sheet_ai.complete.await_count, 0)
        self.assertEqual(sum(sheet_document._reads.values()), 0)

    async def test_a_download_over_the_cap_or_a_silent_site_is_told_apart(self) -> None:
        for error, word in ((fetch.TooBig(fetch.TOO_BIG), ws.TOO_BIG),
                            (fetch.Unreachable(fetch.UNREACHABLE), ws.SERVER)):  # fmt: skip
            with self.subTest(word):
                rid = await self.put(ws.DOCUMENT, url=DOC)
                with patch.object(fetch, "fetch", AsyncMock(side_effect=error)):
                    await self.take_and_run()
                self.assertEqual(await self.state_of(rid), ("failed", word))
                self.assertEqual(sum(sheet_document._reads.values()), 0)
                self.host.too_soon_document = False

    async def test_a_document_the_ai_cannot_read_is_cant_read_and_the_days_read_is_given_back(
        self,
    ) -> None:
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened(b"%PDF-1.4 nothing", "sheet.pdf"):  # unreadable before the AI is asked
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.CANT_READ))
        self.assertEqual(self.host.sheet_ai.complete.await_count, 0)
        self.assertEqual(sum(sheet_document._reads.values()), 0)  # given back
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])
        self.assertEqual(sheet_document._busy, set())  # and the server is free again

    async def test_an_answer_with_no_sheet_in_it_is_cant_read(self) -> None:
        self.host.sheet_ai.complete = AsyncMock(return_value=Reply("{}", False))
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened():
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.CANT_READ))
        self.assertEqual(await self.store.sheets(GUILD_A, self.c), [])

    async def test_a_server_already_reading_is_busy_and_shares_the_discord_state(self) -> None:
        from dmbot.ui import sheet_import as discord_side

        self.assertIs(discord_side._busy, sheet_document._busy)
        self.assertIs(discord_side._reads, sheet_document._reads)
        sheet_document._busy.add(GUILD_A)  # e.g. a Discord import is running
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.BUSY))
        opened.assert_not_awaited()
        self.assertEqual(sum(sheet_document._reads.values()), 0)
        self.assertEqual(sheet_document._busy, {GUILD_A})  # not freed by someone else's refusal

    async def test_a_bug_before_the_ai_gives_the_days_read_back(self) -> None:
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with patch.object(fetch, "fetch", AsyncMock(side_effect=RuntimeError("boom"))):
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.SERVER))
        self.assertEqual(sum(sheet_document._reads.values()), 0)
        self.assertEqual(sheet_document._busy, set())

    async def test_a_read_that_runs_out_of_time_is_server_and_stays_counted(self) -> None:
        async def hang(*_: Any, **__: Any) -> Reply:
            await asyncio.sleep(5)
            return Reply("", False)

        self.host.sheet_ai.complete = hang
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened(), patch.object(sheet_document, "TIME_LIMIT_S", 0.05):
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.SERVER))
        self.assertEqual(sum(sheet_document._reads.values()), 1)  # the AI was asked
        self.assertEqual(sheet_document._busy, set())

    async def test_the_fetch_is_capped_at_the_documents_limit(self) -> None:
        await self.put(ws.DOCUMENT, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        opened.assert_awaited_once_with(DOC, max_bytes=sheet_import.FILE_MAX_BYTES)

    async def test_the_days_reads_used_up_is_used_up(self) -> None:
        sheet_document._reads[(GUILD_A, sheet_document.today())] = sheet_document.READS_PER_DAY
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.USED_UP))
        opened.assert_not_awaited()

    async def test_without_an_ai_or_a_plan_that_allows_it_it_is_no_ai(self) -> None:
        self.host.sheet_ai = None
        first = await self.put(ws.DOCUMENT, url=DOC)
        await self.take_and_run()
        self.assertEqual(await self.state_of(first), ("failed", ws.NO_AI))
        self.host.sheet_ai = SimpleNamespace(complete=AsyncMock())
        self.host.plan_refusal = "Upgrade to read documents."
        second = await self.put(ws.DOCUMENT, url=DOC)
        await self.take_and_run()
        self.assertEqual(await self.state_of(second), ("failed", ws.NO_AI))

    async def test_a_second_read_inside_the_cooldown_is_too_soon_and_costs_nothing(self) -> None:
        self.host.too_soon_document = True
        rid = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.TOO_SOON))
        opened.assert_not_awaited()
        self.assertEqual(sum(sheet_document._reads.values()), 0)

    async def test_another_players_character_is_not_allowed_for_a_document(self) -> None:
        rid = await self.put(ws.DOCUMENT, user=OTHER, url=DOC)
        with self.opened() as opened:
            await self.take_and_run()
        self.assertEqual(await self.state_of(rid), ("failed", ws.NOT_ALLOWED))
        opened.assert_not_awaited()

    async def test_a_finished_row_has_no_link_and_no_payload_left(self) -> None:
        doc = await self.put(ws.DOCUMENT, url=DOC)
        typed = await self.put(ws.TYPED, payload=form())
        link = await self.put(ws.LINK)
        bad = await self.put(ws.DOCUMENT, url="https://127.0.0.1/x")
        with self.opened():
            await self.take_and_run()
        for rid in (doc, typed, link, bad):
            self.assertEqual(await self.left_on_row(rid), ("", ""))

    async def test_no_log_line_holds_a_link_or_text(self) -> None:
        await self.put(ws.DOCUMENT, url=DOC)
        await self.put(ws.TYPED, payload=form(names="Secret Spell Name"))
        await self.put(ws.DOCUMENT, url="https://127.0.0.1/private-path")
        with self.assertLogs(level=logging.DEBUG) as logs, self.opened():
            await self.take_and_run()
        self.assertTrue(logs.records)  # the check below means something
        text = "\n".join(r.getMessage() + str(r.exc_text) for r in logs.records)
        for forbidden in ("docs.example.com", "belleros-sheet", "127.0.0.1", "private-path",
                          "Belleros", "Secret Spell Name", "Wood Elf"):  # fmt: skip
            self.assertNotIn(forbidden, text)

    async def test_an_ai_failure_ends_as_no_ai_and_a_bug_as_server(self) -> None:
        self.host.sheet_ai.complete = AsyncMock(side_effect=AIError("The AI is busy."))
        first = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened():
            await self.take_and_run()
        self.assertEqual(await self.state_of(first), ("failed", ws.NO_AI))
        self.host.too_soon_document = False
        self.host.sheet_ai.complete = AsyncMock(side_effect=RuntimeError("boom"))
        second = await self.put(ws.DOCUMENT, url=DOC)
        with self.opened():
            await self.take_and_run()
        self.assertEqual(await self.state_of(second), ("failed", ws.SERVER))
        self.assertEqual(sheet_document._busy, set())  # even after a bug


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
        b._sheet_tasks = {}  # declared in DMBot.__init__
        b._closing = False
        b._asking = set()
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

    async def test_while_closing_no_choice_is_started(self) -> None:
        queue: list[Any] = [SimpleNamespace(campaign_id="c")]
        claimed: list[int] = []

        async def claim(guild_id: int) -> Any:
            claimed.append(guild_id)
            return queue.pop(0) if queue else None

        b: Any = DMBot.__new__(DMBot)
        b.web_sheets = SimpleNamespace(claim_next=claim)
        b.sheets = object()
        b._sheet_tasks = {}  # declared in DMBot.__init__
        b._closing = True
        b._asking = set()
        ran: list[int] = []

        async def never(self: Any, store: Any, guild_id: int, item: Any) -> None:
            ran.append(guild_id)

        guilds = PropertyMock(return_value=[SimpleNamespace(id=1)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_sheet_choice", never),
        ):
            await b.run_web_sheets()
        await asyncio.sleep(0)  # a started task would run now, so `ran` below means something
        self.assertEqual(b._sheet_tasks, {})
        self.assertEqual(ran, [])
        self.assertEqual(claimed, [])  # claim_next was never awaited: the row stays unclaimed

    async def test_closing_during_one_claim_stops_before_the_next_server(self) -> None:
        # The guard is checked again for each server: closing that starts while server 1's claim
        # is in flight must keep server 2's row unclaimed.
        queue: dict[int, list[Any]] = {
            1: [SimpleNamespace(campaign_id="c")],
            2: [SimpleNamespace(campaign_id="d")],
        }
        claimed: list[int] = []
        b: Any = DMBot.__new__(DMBot)

        async def claim(guild_id: int) -> Any:
            claimed.append(guild_id)
            if guild_id == 1:
                b._closing = True
            return queue[guild_id].pop(0) if queue[guild_id] else None

        b.web_sheets = SimpleNamespace(claim_next=claim)
        b.sheets = object()
        b._sheet_tasks = {}  # declared in DMBot.__init__
        b._closing = False
        b._asking = set()
        ran: list[int] = []

        async def never(self: Any, store: Any, guild_id: int, item: Any) -> None:
            ran.append(guild_id)

        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_sheet_choice", never),
        ):
            await b.run_web_sheets()
        await asyncio.sleep(0)
        self.assertEqual(claimed, [1])  # server 2 was never claimed
        self.assertEqual(len(queue[2]), 1)
        self.assertEqual(ran, [])

    async def test_the_poll_loop_does_not_wait_for_a_document_read(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        item = SimpleNamespace(campaign_id="c", action=ws.DOCUMENT)

        async def claim(guild_id: int) -> Any:
            return item if guild_id == 1 and not started.is_set() else None

        async def slow(*_: Any) -> bool:
            started.set()
            await release.wait()
            return True

        b: Any = DMBot.__new__(DMBot)
        b.web_sheets = SimpleNamespace(claim_next=claim)
        b.sheets = object()
        b._sheet_tasks = {}  # declared in DMBot.__init__
        b._closing = False
        b._asking = set()
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with patch.object(DMBot, "guilds", guilds), patch.object(ws, "run_one", slow):
            await asyncio.wait_for(b.run_web_sheets(), timeout=1)  # returns at once
            await started.wait()
            self.assertEqual(len(b._sheet_tasks), 1)
            release.set()
            await asyncio.gather(*b._sheet_tasks.values())

    async def test_without_a_store_nothing_happens(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.web_sheets = None
        b.sheets = None
        await b.run_web_sheets()
