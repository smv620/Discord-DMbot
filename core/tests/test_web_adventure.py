"""What DMbot holds of the DM's adventure, on the website (#3694, slice A): a DM-only read through
one door, counts and file names and never a word of the book, one campaign's documents never
another's, and the route behind its switch. Real Postgres and the web role; invented names."""

from __future__ import annotations

from typing import Any, cast

import httpx

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
