"""What DMbot holds of the DM's adventure, on the website (#3694, slice A): a DM-only read through
one door, counts and file names and never a word of the book, one campaign's documents never
another's, and the route behind its switch. Real Postgres and the web role; invented names."""

from __future__ import annotations

from typing import Any, cast

import httpx

from dmbot.memory import web_adventure_removals as bot
from dmbot.memory.adventure import AdventureStore
from dmbot.web import adventure as web
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_name_checks import GUILD, WithChecks

SECRET = "The silver key is hidden under the third step of the lighthouse."


class Holding(WithChecks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{GUILD}.{self.one.id}"

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def give(
        self, doc: str, name: str, *, campaign: str | None = None, chunks: int = 3
    ) -> None:
        cid = campaign or self.one.id
        await self.sql(
            "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
            " characters, chunks, added_by, added_at)"
            " VALUES (%s, %s, %s, %s, %s, 5000, %s, %s, 10)",
            GUILD, cid, doc, name, (doc * 2)[:64].ljust(64, "0"), chunks, ALICE.id,
        )  # fmt: skip

    async def node(self, node: str, doc: str, kind: str, name: str) -> None:
        await self.sql(
            "INSERT INTO plan_nodes (guild_id, campaign_id, id, document_id, kind, name, name_key,"
            " summary, quote, chunk_ids, position, created_at) VALUES (%s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, '{}', 0, 1)",
            GUILD, self.one.id, node, doc, kind, name, name.lower(), SECRET, SECRET,
        )  # fmt: skip

    async def bob(self) -> Any:
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def test_a_dm_sees_each_file_how_far_its_read_is_and_what_it_found(self) -> None:
        doc = "a" * 32
        await self.give(doc, "The Lost Light.pdf")
        await self.sql(
            "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status, next_chunk,"
            " total_chunks, started_by, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'reading', 1, 3, %s, 20, 30)",
            GUILD, self.one.id, doc, ALICE.id,
        )  # fmt: skip
        await self.node("1" * 32, doc, "chapter", "The Harbour")
        await self.node("2" * 32, doc, "place", "Saltmere")
        await self.node("3" * 32, doc, "person", "Captain Varn")
        await self.sql(
            "INSERT INTO plan_links (guild_id, campaign_id, id, document_id, from_id, to_id, need,"
            " quote, chunk_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s, 'alive', 'q', '{}', 1)",
            GUILD, self.one.id, "4" * 32, doc, "1" * 32, "3" * 32,
        )  # fmt: skip
        got = await web.held(self.web, self.session, self.cref)
        [one] = got["documents"]
        self.assertEqual(
            (one["id"], one["name"], one["characters"]), (doc, "The Lost Light.pdf", 5000)
        )
        self.assertEqual(
            one["read"],
            {"status": "reading", "nextChunk": 1, "totalChunks": 3, "stopReason": "",
             "skippedParts": 0, "updatedAt": 30},
        )  # fmt: skip
        self.assertEqual(one["found"], {"chapters": 1, "places": 1, "people": 1, "links": 1})
        self.assertFalse(one["restored"])
        self.assertEqual((got["documentsMax"], got["charactersHeld"]), (20, 5000))
        self.assertEqual(got["mode"], "off")

    async def test_nothing_of_the_book_comes_back(self) -> None:
        doc = "b" * 32
        await self.give(doc, "Notes.txt")
        await self.node("5" * 32, doc, "chapter", "The Lighthouse")
        got = await web.held(self.web, self.session, self.cref)
        self.assertNotIn(
            "silver key", repr(got)
        )  # (the summary and the quote stay in the database)
        self.assertNotIn("Lighthouse", repr(got))  # (and so do the chapters' own names)

    async def test_a_file_not_read_yet_and_a_restored_one_say_so(self) -> None:
        await self.give("c" * 32, "Fresh.pdf")
        await self.give("d" * 32, "Restored.pdf", chunks=0)
        got = await web.held(self.web, self.session, self.cref)
        by_name = {d["name"]: d for d in got["documents"]}
        self.assertIsNone(by_name["Fresh.pdf"]["read"])
        self.assertEqual(
            by_name["Fresh.pdf"]["found"], {"chapters": 0, "places": 0, "people": 0, "links": 0}
        )
        self.assertTrue(by_name["Restored.pdf"]["restored"])  # (no text until it is given again)

    async def test_a_campaign_with_nothing_given_has_an_empty_list(self) -> None:
        got = await web.held(self.web, self.session, self.cref)
        self.assertEqual((got["documents"], got["charactersHeld"]), ([], 0))

    async def test_a_player_and_another_campaigns_dm_are_told_nothing(self) -> None:
        await self.give("e" * 32, "Mine.pdf")
        with self.assertRaises(RecapError) as caught:
            await web.held(
                self.web, await self.bob(), self.cref
            )  # BOB DMs campaign "Two", not "One"
        self.assertEqual(caught.exception.code, "not_found")

    async def test_another_campaigns_files_are_never_in_the_list(self) -> None:
        await self.give("f" * 32, "Theirs.pdf", campaign=self.two.id)
        got = await web.held(self.web, self.session, self.cref)
        self.assertEqual(got["documents"], [])

    async def test_the_route_answers_404_until_the_switch_is_on(self) -> None:
        await self.give("9" * 32, "Mine.pdf")
        for on in (False, True):
            app = create_app(
                settings(adventure_on=on),
                self.web,
                FakeDiscord(),
                payments=FakeProvider(b"h" * 32, "https://x.example"),
                clock=lambda: NOW,
            )
            token = await sessions.sign_in(self.web, cast(Any, ALICE), [THURSDAY], now=NOW, days=30)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
            ) as client:
                client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
                got = await client.get(f"/me/campaigns/{self.cref}/adventure", headers=HEADERS)
            self.assertEqual(got.status_code, 200 if on else 404, got.text)
            if on:
                self.assertEqual([d["name"] for d in got.json()["documents"]], ["Mine.pdf"])


class ReadList(Holding):
    async def link(self, rid: str, doc: str, a: str, b: str, need: str) -> None:
        await self.sql(
            "INSERT INTO plan_links (guild_id, campaign_id, id, document_id, from_id, to_id, need,"
            " quote, chunk_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'q', '{}', 1)",
            GUILD, self.one.id, rid, doc, a, b, need,
        )  # fmt: skip

    async def test_the_list_is_the_cards_order_chapters_places_people_then_links(self) -> None:
        doc = "a" * 32
        await self.give(doc, "Book.pdf")
        await self.node("3" * 32, doc, "person", "Captain Varn")
        await self.node("2" * 32, doc, "place", "Saltmere")
        await self.node("1" * 32, doc, "chapter", "The Harbour")
        await self.link("4" * 32, doc, "1" * 32, "3" * 32, "alive")
        got = await web.read_nodes(self.web, self.session, self.cref)
        self.assertEqual(got["total"], 4)
        self.assertEqual(
            [(i["kind"], i["name"], i["summary"]) for i in got["items"]],
            [("chapter", "The Harbour", SECRET), ("place", "Saltmere", SECRET),
             ("person", "Captain Varn", SECRET), ("link", "The Harbour → Captain Varn", "alive")],
        )  # fmt: skip

    async def test_pages_are_fifty_lines_and_run_on_into_the_links(self) -> None:
        doc = "b" * 32
        await self.give(doc, "Big.pdf")
        for n in range(49):
            await self.node(f"{n:032x}", doc, "place", f"Place {n:02d}")
        await self.node("e" * 32, doc, "chapter", "Chapter")
        await self.link("f" * 32, doc, "e" * 32, f"{0:032x}", "open")
        await self.link("d" * 32, doc, "e" * 32, f"{1:032x}", "")
        first = await web.read_nodes(self.web, self.session, self.cref)
        self.assertEqual((first["total"], len(first["items"])), (52, 50))
        self.assertEqual(first["items"][-1]["kind"], "place")
        second = await web.read_nodes(self.web, self.session, self.cref, 50)
        self.assertEqual([i["kind"] for i in second["items"]], ["link", "link"])
        past = await web.read_nodes(self.web, self.session, self.cref, 500)
        self.assertEqual(past["items"], [])

    async def test_only_a_dm_of_the_campaign_reads_it(self) -> None:
        doc = "c" * 32
        await self.give(doc, "Mine.pdf")
        await self.node("1" * 32, doc, "chapter", "The Harbour")
        with self.assertRaises(RecapError):
            await web.read_nodes(self.web, await self.bob(), self.cref)

    async def test_another_campaigns_book_is_never_in_it(self) -> None:
        await self.give("d" * 32, "Theirs.pdf", campaign=self.two.id)
        got = await web.read_nodes(self.web, self.session, self.cref)
        self.assertEqual((got["total"], got["items"]), (0, []))

    async def test_the_route_answers_404_until_the_switch_is_on(self) -> None:
        doc = "e" * 32
        await self.give(doc, "Mine.pdf")
        await self.node("1" * 32, doc, "chapter", "The Harbour")
        for on in (False, True):
            app = create_app(
                settings(adventure_on=on),
                self.web,
                FakeDiscord(),
                payments=FakeProvider(b"h" * 32, "https://x.example"),
                clock=lambda: NOW,
            )
            token = await sessions.sign_in(self.web, cast(Any, ALICE), [THURSDAY], now=NOW, days=30)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
            ) as client:
                client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
                got = await client.get(f"/me/campaigns/{self.cref}/adventure/read", headers=HEADERS)
            self.assertEqual(got.status_code, 200 if on else 404, got.text)
            if on:
                self.assertEqual(got.json()["items"][0]["name"], "The Harbour")


class Removing(Holding):
    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT document_id, state, error, user_id FROM web_adventure_removals"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_a_dm_queues_a_removal_and_the_bot_does_it_as_discord_does(self) -> None:
        doc = "a" * 32
        await self.give(doc, "The Lost Light.pdf")
        await self.node("1" * 32, doc, "chapter", "The Harbour")
        rid = await web.remove(self.web, self.session, self.cref, doc)
        first = await web.removal(self.web, self.session, self.cref, rid)
        self.assertEqual(first["state"], "waiting")
        self.assertEqual((await self.rows())[0]["user_id"], ALICE.id)
        store = bot.RemovalStore(self.db)
        item = await store.claim_next(GUILD)
        assert item is not None
        self.assertEqual(await bot.run_one(AdventureStore(self.db), store, GUILD, item), "")
        got = await web.removal(self.web, self.session, self.cref, rid)
        self.assertEqual((got["state"], got["error"]), ("done", ""))
        held = await web.held(self.web, self.session, self.cref)
        self.assertEqual(held["documents"], [])  # the file, its nodes and links went with it
        self.assertEqual((await web.read_nodes(self.web, self.session, self.cref))["total"], 0)

    async def test_asking_twice_while_one_is_open_gives_the_same_removal(self) -> None:
        await self.give("b" * 32, "Book.pdf")
        first = await web.remove(self.web, self.session, self.cref, "b" * 32)
        again = await web.remove(self.web, self.session, self.cref, "b" * 32)
        self.assertEqual((first, len(await self.rows())), (again, 1))

    async def test_a_player_a_missing_file_and_a_bad_id_are_all_not_found(self) -> None:
        await self.give("c" * 32, "Book.pdf")
        for who, doc in ((await self.bob(), "c" * 32), (self.session, "d" * 32),
                         (self.session, "not-an-id")):  # fmt: skip
            with self.assertRaises(RecapError) as caught:
                await web.remove(self.web, who, self.cref, doc)
            self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(await self.rows(), [])

    async def test_another_campaigns_file_cannot_be_removed(self) -> None:
        await self.give("e" * 32, "Theirs.pdf", campaign=self.two.id)
        with self.assertRaises(RecapError):
            await web.remove(self.web, self.session, self.cref, "e" * 32)

    async def test_a_removal_is_read_back_by_a_dm_of_the_campaign_only(self) -> None:
        await self.give("f" * 32, "Book.pdf")
        rid = await web.remove(self.web, self.session, self.cref, "f" * 32)
        with self.assertRaises(RecapError):
            await web.removal(self.web, await self.bob(), self.cref, rid)
        with self.assertRaises(RecapError):
            await web.removal(self.web, self.session, self.cref, "0" * 32)

    async def test_a_dm_who_stopped_being_one_is_refused_by_the_bot(self) -> None:
        await self.give("9" * 32, "Book.pdf")
        rid = await web.remove(self.web, self.session, self.cref, "9" * 32)
        await self.sql("DELETE FROM campaign_dms WHERE campaign_id = %s", self.one.id)
        store = bot.RemovalStore(self.db)
        item = await store.claim_next(GUILD)
        assert item is not None
        self.assertEqual(
            await bot.run_one(AdventureStore(self.db), store, GUILD, item), "not_yours"
        )
        self.assertEqual((await self.rows())[0]["state"], "failed")
        self.assertIsNotNone(rid)

    async def test_the_sweep_drops_a_day_old_row(self) -> None:
        await self.give("7" * 32, "Book.pdf")
        await web.remove(self.web, self.session, self.cref, "7" * 32)
        store = bot.RemovalStore(self.db, clock=lambda: 10**10)
        self.assertEqual(await store.sweep(GUILD), 1)

    async def test_the_routes_answer_404_until_the_switch_is_on(self) -> None:
        await self.give("6" * 32, "Book.pdf")
        for on in (False, True):
            app = create_app(
                settings(adventure_on=on),
                self.web,
                FakeDiscord(),
                payments=FakeProvider(b"h" * 32, "https://x.example"),
                clock=lambda: NOW,
            )
            token = await sessions.sign_in(self.web, cast(Any, ALICE), [THURSDAY], now=NOW, days=30)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
            ) as client:
                client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
                got = await client.post(
                    f"/me/campaigns/{self.cref}/adventure/{'6' * 32}/remove", headers=HEADERS
                )
                self.assertEqual(got.status_code, 202 if on else 404, got.text)
                if on:
                    polled = await client.get(
                        f"/me/campaigns/{self.cref}/adventure/removals/{got.json()['removal']}",
                        headers=HEADERS,
                    )
                    self.assertEqual(polled.json()["state"], "waiting")


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class Giving(Holding):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bucket = FakeBucket()

    async def switch_on(self) -> None:
        await self.sql("UPDATE campaigns SET story_mode = 'watch' WHERE id = %s", self.one.id)
        await self.sql(
            "INSERT INTO shared_confirmations (guild_id, campaign_id, id, user_id, purpose,"
            " fingerprint, confirmed_at, restored)"
            " VALUES (%s, %s, 'c1', %s, 'shared_story', %s, 1, false)",
            GUILD, self.one.id, ALICE.id, "0" * 64,
        )  # fmt: skip

    async def test_a_dm_gives_a_file_and_it_waits_for_the_bot(self) -> None:
        await self.switch_on()
        gid = await web.give(
            self.web, self.session, self.cref, self.bucket, "The Lost Light.txt", b"Chapter one."
        )
        [key] = self.bucket.objects
        self.assertRegex(key, r"^a/[0-9a-f]{32}\.txt$")
        got = await web.giving(self.web, self.session, self.cref, gid)
        self.assertEqual(
            (got["state"], got["stage"], got["name"]), ("waiting", "waiting", "The Lost Light.txt")
        )
        self.assertNotIn(key, repr(got))  # (the staged key never leaves the database)

    async def test_the_switch_and_the_confirmation_come_first_and_nothing_is_kept(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await web.give(self.web, self.session, self.cref, self.bucket, "a.txt", b"words")
        self.assertEqual(caught.exception.code, "not_on")
        self.assertEqual(self.bucket.objects, {})  # (the staged object is deleted again)

    async def test_a_second_file_while_one_waits_is_busy(self) -> None:
        await self.switch_on()
        await web.give(self.web, self.session, self.cref, self.bucket, "a.txt", b"one")
        with self.assertRaises(RecapError) as caught:
            await web.give(self.web, self.session, self.cref, self.bucket, "b.txt", b"two")
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(len(self.bucket.objects), 1)

    async def test_a_player_and_a_file_that_is_not_what_it_says_are_refused(self) -> None:
        await self.switch_on()
        with self.assertRaises(RecapError) as caught:
            await web.give(self.web, await self.bob(), self.cref, self.bucket, "a.txt", b"words")
        self.assertEqual(caught.exception.code, "not_found")
        for name, data in (("a.pdf", b"not a pdf"), ("a.exe", b"MZ"), ("a.txt", b""),
                           ("a.docx", b"%PDF-")):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await web.give(self.web, self.session, self.cref, self.bucket, name, data)
            self.assertEqual(bad.exception.code, "bad_file")
        self.assertEqual(self.bucket.objects, {})

    async def test_no_bucket_says_uploads_are_off(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await web.give(self.web, self.session, self.cref, None, "a.txt", b"words")
        self.assertEqual(caught.exception.code, "uploads_off")

    async def test_another_dm_cannot_read_the_progress(self) -> None:
        await self.switch_on()
        gid = await web.give(self.web, self.session, self.cref, self.bucket, "a.txt", b"one")
        with self.assertRaises(RecapError):
            await web.giving(self.web, await self.bob(), self.cref, gid)
        with self.assertRaises(RecapError):
            await web.giving(self.web, self.session, self.cref, "0" * 32)


class Reading(Holding):
    async def test_a_dm_says_yes_and_the_request_waits_for_the_bot(self) -> None:
        await self.give("a" * 32, "Book.pdf")
        rid = await web.read(self.web, self.session, self.cref, "a" * 32)
        got = await web.reading(self.web, self.session, self.cref, rid)
        self.assertEqual((got["state"], got["error"], got["documentId"]), ("waiting", "", "a" * 32))

    async def test_a_second_yes_while_one_is_open_is_busy(self) -> None:
        await self.give("b" * 32, "Book.pdf")
        await web.read(self.web, self.session, self.cref, "b" * 32)
        with self.assertRaises(RecapError) as caught:
            await web.read(self.web, self.session, self.cref, "b" * 32)
        self.assertEqual(caught.exception.code, "busy")

    async def test_a_missing_file_a_restored_one_and_a_read_one_say_so(self) -> None:
        with self.assertRaises(RecapError) as gone:
            await web.read(self.web, self.session, self.cref, "c" * 32)
        self.assertEqual(gone.exception.code, "gone")
        await self.give("d" * 32, "Restored.pdf", chunks=0)
        with self.assertRaises(RecapError) as empty:
            await web.read(self.web, self.session, self.cref, "d" * 32)
        self.assertEqual(empty.exception.code, "not_readable")
        await self.give("e" * 32, "Done.pdf")
        await self.sql(
            "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status, next_chunk,"
            " total_chunks, started_by, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'done', 3, 3, %s, 1, 1)",
            GUILD, self.one.id, "e" * 32, ALICE.id,
        )  # fmt: skip
        with self.assertRaises(RecapError) as done:
            await web.read(self.web, self.session, self.cref, "e" * 32)
        self.assertEqual(done.exception.code, "already_read")

    async def test_a_player_a_bad_id_and_another_dm_are_all_not_found(self) -> None:
        await self.give("f" * 32, "Book.pdf")
        for who, doc in ((await self.bob(), "f" * 32), (self.session, "nope")):
            with self.assertRaises(RecapError) as caught:
                await web.read(self.web, who, self.cref, doc)
            self.assertEqual(caught.exception.code, "not_found")
        rid = await web.read(self.web, self.session, self.cref, "f" * 32)
        with self.assertRaises(RecapError):
            await web.reading(self.web, await self.bob(), self.cref, rid)

    async def test_the_list_carries_the_cost_in_the_cards_words_until_it_is_read(self) -> None:
        await self.give("1" * 32, "Book.pdf")
        await self.give("2" * 32, "Restored.pdf", chunks=0)
        got = await web.held(self.web, self.session, self.cref)
        by_name = {d["name"]: d for d in got["documents"]}
        self.assertRegex(by_name["Book.pdf"]["estimate"], r"^About [\d,]+ AI credits? for the adv")
        self.assertIsNone(by_name["Restored.pdf"]["estimate"])
        await self.sql(
            "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status, next_chunk,"
            " total_chunks, started_by, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'done', 3, 3, %s, 1, 1)",
            GUILD, self.one.id, "1" * 32, ALICE.id,
        )  # fmt: skip
        again = await web.held(self.web, self.session, self.cref)
        self.assertIsNone({d["name"]: d for d in again["documents"]}["Book.pdf"]["estimate"])

    async def test_the_routes_answer_404_until_the_switch_is_on(self) -> None:
        await self.give("3" * 32, "Book.pdf")
        for on in (False, True):
            app = create_app(
                settings(adventure_on=on),
                self.web,
                FakeDiscord(),
                payments=FakeProvider(b"h" * 32, "https://x.example"),
                clock=lambda: NOW,
            )
            token = await sessions.sign_in(self.web, cast(Any, ALICE), [THURSDAY], now=NOW, days=30)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
            ) as client:
                client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
                got = await client.post(
                    f"/me/campaigns/{self.cref}/adventure/{'3' * 32}/read", headers=HEADERS
                )
                self.assertEqual(got.status_code, 202 if on else 404, got.text)
                if on:
                    polled = await client.get(
                        f"/me/campaigns/{self.cref}/adventure/reads/{got.json()['read']}",
                        headers=HEADERS,
                    )
                    self.assertEqual(polled.json()["state"], "waiting")
