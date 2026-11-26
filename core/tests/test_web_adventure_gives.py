"""The bot's side of giving a Shared story file from the website (#3694 slice D): the queue is
claimed in order, the file is read and kept with Discord's own store call (which checks the person,
the switch and the confirmation again), the staged object always goes, and a failure says its
reason. The website's own checks of a file are plain functions, tested here without a database.
Real Postgres, no website role; invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen.adventure_card import FILE_TYPES as card_types
from dmbot.memory import web_adventure_gives as bot
from dmbot.memory.adventure import AdventureStore
from dmbot.memory.name_documents import MAX_DOCUMENT_BYTES, MAX_PDF_BYTES, DocumentError
from dmbot.pictures.bucket import BucketError
from dmbot.web import adventure as web
from tests.pg import DatabaseTest

GUILD, DM, OTHER = 5001, 7001, 7002
KEY = "a/" + "1" * 32 + ".txt"
BOOK = "Chapter one. The harbour town of Saltmere wakes under a grey sky. " * 40


class FakeStaging:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.deleted: list[str] = []

    async def get(self, key: str) -> bytes:
        if key not in self.files:
            raise BucketError("gone")
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def read_text(name: str, raw: bytes) -> str:
    return raw.decode()


async def read_nothing(name: str, raw: bytes) -> str:
    raise DocumentError("DMbot couldn't open that file.")


class TheChecks(unittest.TestCase):
    def test_a_file_is_what_its_own_first_bytes_say(self) -> None:
        self.assertTrue(web.sniff("a.pdf", b"%PDF-1.7 ..."))
        self.assertFalse(web.sniff("a.pdf", b"PK\x03\x04"))
        self.assertTrue(web.sniff("a.docx", b"PK\x03\x04..."))
        self.assertFalse(web.sniff("a.docx", b"%PDF-"))
        self.assertTrue(web.sniff("a.txt", b"plain words"))
        self.assertFalse(web.sniff("a.txt", b"abc\x00def"))

    def test_a_pdf_may_be_bigger_than_the_others(self) -> None:
        self.assertGreater(web.max_bytes("a.pdf"), web.max_bytes("a.docx"))

    def test_a_name_loses_its_folders_and_control_characters(self) -> None:
        self.assertEqual(web.clean_name("C:\\books\\The Lost Light.pdf"), "The Lost Light.pdf")
        self.assertEqual(web.clean_name("../../etc/pass\nwd.txt"), "passwd.txt")
        self.assertEqual(len(web.clean_name("x" * 300 + ".pdf")), 120)

    def test_only_the_file_types_discord_takes_are_taken(self) -> None:
        self.assertEqual(web.FILE_TYPES, card_types)

    def test_a_pdf_limit_is_the_website_cap_and_the_others_are_the_cores(self) -> None:
        self.assertEqual(web.max_bytes("a.pdf"), web.PDF_MAX_BYTES)
        self.assertEqual(web.max_bytes("a.docx"), MAX_DOCUMENT_BYTES)
        self.assertEqual(web.max_bytes("a.txt"), MAX_DOCUMENT_BYTES)
        self.assertLessEqual(web.PDF_MAX_BYTES, MAX_PDF_BYTES)

    def test_a_name_with_no_printable_characters_is_empty(self) -> None:
        self.assertEqual(web.clean_name("\n\t\x00"), "")

    def test_an_upper_case_ending_is_still_checked_by_its_bytes(self) -> None:
        self.assertFalse(web.sniff("BOOK.PDF", b"not a pdf"))
        self.assertTrue(web.sniff("BOOK.PDF", b"%PDF-1.4"))
        self.assertEqual(web.max_bytes("BOOK.PDF"), web.PDF_MAX_BYTES)


class Gives(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", DM)).id
        self.store = bot.GiveStore(self.db, clock=lambda: 1000.0)
        self.adventure = AdventureStore(self.db)
        await self.sql("UPDATE campaigns SET story_mode = 'watch' WHERE id = %s", self.campaign)
        await self.sql(
            "INSERT INTO shared_confirmations (guild_id, campaign_id, id, user_id, purpose,"
            " fingerprint, confirmed_at, restored)"
            " VALUES (%s, %s, 'c1', %s, 'shared_story', %s, 1, false)",
            GUILD, self.campaign, DM, "0" * 64,
        )  # fmt: skip

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def queue(self, rid: str, *, user: int = DM, key: str = KEY) -> None:
        await self.sql(
            "INSERT INTO web_adventure_gives (guild_id, campaign_id, id, user_id, object_key,"
            " file_name, size_bytes, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, 'Book.txt', 100, 1, 1)",
            GUILD, self.campaign, rid, user, key,
        )  # fmt: skip

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, stage, error, object_key, characters, chunks"
                " FROM web_adventure_gives WHERE id = %s",
                (rid,),
            )
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def give(
        self, rid: str, staging: FakeStaging, read: bot.ReadFn = read_text
    ) -> bot.Outcome:
        await self.queue(rid)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        return await bot.run_one(self.adventure, self.store, staging, read, GUILD, item)

    async def test_a_given_file_is_read_kept_and_its_object_deleted(self) -> None:
        staging = FakeStaging({KEY: BOOK.encode()})
        out = await self.give("1" * 32, staging)
        self.assertEqual(out.error, "")
        got = await self.row("1" * 32)
        self.assertEqual((got["state"], got["stage"], got["object_key"]), ("done", "done", ""))
        self.assertEqual((got["characters"], got["chunks"] > 0), (len(BOOK), True))
        [doc] = await self.adventure.documents(GUILD, self.campaign)
        self.assertEqual(doc.file_name, "Book.txt")
        self.assertEqual(staging.deleted, [KEY])

    async def test_a_file_that_expired_says_so(self) -> None:
        staging = FakeStaging({})
        self.assertEqual((await self.give("2" * 32, staging)).error, "expired")
        self.assertEqual((await self.row("2" * 32))["state"], "failed")
        self.assertEqual(staging.deleted, [KEY])

    async def test_a_file_the_reader_cannot_open_says_so(self) -> None:
        out = await self.give("3" * 32, FakeStaging({KEY: b"x"}), read_nothing)
        self.assertEqual(out.error, "unreadable")
        self.assertEqual(await self.adventure.documents(GUILD, self.campaign), [])

    async def test_the_store_checks_again_the_switch_the_person_and_a_repeat(self) -> None:
        await self.queue("4" * 32, user=OTHER)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        staging = FakeStaging({KEY: BOOK.encode()})
        out = await bot.run_one(self.adventure, self.store, staging, read_text, GUILD, item)
        self.assertEqual(out.error, "not_yours")
        await self.sql("UPDATE campaigns SET story_mode = 'off' WHERE id = %s", self.campaign)
        self.assertEqual((await self.give("5" * 32, staging)).error, "not_on")

    async def test_the_same_file_twice_is_a_repeat_and_nothing_else_changes(self) -> None:
        staging = FakeStaging({KEY: BOOK.encode()})
        self.assertEqual((await self.give("6" * 32, staging)).error, "")
        self.assertEqual((await self.give("7" * 32, staging)).error, "already")
        self.assertEqual(len(await self.adventure.documents(GUILD, self.campaign)), 1)

    async def test_a_text_with_nothing_in_it_is_refused_with_its_reason(self) -> None:
        out = await self.give("8" * 32, FakeStaging({KEY: b" "}), read_text)
        self.assertEqual(out.error, "no_text")

    async def test_a_taken_row_is_not_taken_twice_and_only_the_holder_can_finish(self) -> None:
        await self.queue("a" * 32)
        first = await self.store.claim_next(GUILD)
        assert first is not None
        self.assertIsNone(await self.store.claim_next(GUILD))
        stale = bot.Item(first.id, first.campaign_id, first.user_id, first.object_key, "x", 1)
        self.assertFalse(await self.store.finish(GUILD, stale, bot.Outcome("failed")))
        self.assertEqual((await self.row(first.id))["stage"], "reading")
        self.assertTrue(await self.store.saving(GUILD, first))
        self.assertEqual((await self.row(first.id))["stage"], "saving")
        self.assertTrue(await self.store.finish(GUILD, first, bot.Outcome("failed")))

    async def test_the_sweep_gives_back_the_objects_of_old_rows(self) -> None:
        await self.queue("c" * 32)
        late = bot.GiveStore(self.db, clock=lambda: 10.0**9)
        self.assertEqual(await late.sweep(GUILD), [KEY])
