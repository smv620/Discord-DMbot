"""A campaign's homebrew on the website (#1525), run as the website's own database role: anyone in
the server reads the entries the DM let the players know, with no plan rule; a hidden entry never
reaches the page or the file, not even for the campaign's DM; a person outside the server gets "not
found"; and the download is the bot's own file for those entries, byte for byte."""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg

from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import HomebrewStore
from dmbot.web import homebrew as pages
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, API, QUILLON, THURSDAY, FakeDiscord, settings

CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all
SECRET = "A lich waits under the chapel"


class HomebrewBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"
        self.store_b = HomebrewStore(self.db)
        g, c = THURSDAY.id, self.one.id
        await self.store_b.add(
            g,
            c,
            ALICE.id,
            kind="spell",
            name="Zap",
            text="A bolt of blue light.",
            players_may_know=True,
        )
        await self.store_b.add(
            g, c, ALICE.id, kind="item", name="Moon Lamp", text="Glows at night.",
            summary="A lamp that glows", players_may_know=True,
        )  # fmt: skip
        await self.store_b.add(
            g, c, ALICE.id, kind="spell", name="Arc", text="A small spark.", players_may_know=True
        )
        await self.store_b.add(  # hidden: a spoiler
            g, c, ALICE.id, kind="monster", name="The Lich", text=SECRET, summary="Hidden foe"
        )
        await self.store_b.add(
            THURSDAY.id, self.two.id, BOB.id, kind="rule", name="Other", text="Another table's",
            players_may_know=True,
        )  # fmt: skip
        self.carol = await self.session_of(CAROL)
        self.bob = await self.session_of(BOB)

    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def shown_entries(self) -> list[Any]:
        return [e for e in await self.store_b.list(THURSDAY.id, self.one.id) if e.players_may_know]


class ThePage(HomebrewBase):
    async def test_any_member_reads_shown_entries_by_kind_then_name(self) -> None:
        for who in (
            self.session,
            self.bob,
            self.carol,
        ):  # Alice DMs it, Bob DMs another, Carol none
            got = await pages.read_homebrew(self.web, who, self.cref)
            self.assertEqual(got["name"], "One")
            self.assertEqual(
                [(e["kind"], e["name"]) for e in got["entries"]],
                [("spell", "Arc"), ("spell", "Zap"), ("item", "Moon Lamp")],
            )
        by = {e["name"]: e for e in got["entries"]}
        self.assertEqual(by["Moon Lamp"]["summary"], "A lamp that glows")
        self.assertIsNone(by["Zap"]["summary"])
        self.assertEqual(by["Zap"]["text"], "A bolt of blue light.")

    async def test_a_hidden_entry_never_reaches_the_page_even_for_the_dm(self) -> None:
        for who in (self.session, self.carol):
            shown = json.dumps(await pages.read_homebrew(self.web, who, self.cref))
            for hidden in (SECRET, "The Lich", "Hidden foe"):
                self.assertNotIn(hidden, shown)

    async def test_it_is_this_campaigns_entries_only(self) -> None:
        got = await pages.read_homebrew(self.web, self.carol, self.oref)
        self.assertEqual([e["name"] for e in got["entries"]], ["Other"])

    async def test_someone_outside_the_server_or_a_missing_campaign_gets_not_found(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{THURSDAY.id}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await pages.read_homebrew(self.web, who, ref)
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await pages.render(self.web, elsewhere, self.cref)

    async def test_only_the_wanted_fields_leave(self) -> None:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        self.assertEqual(set(got), {"name", "fileName", "entries"})
        self.assertEqual(
            set(got["entries"][0]), {"kind", "name", "summary", "text", "fromBook", "legacy"}
        )
        self.assertEqual(got["fileName"], homebrew_file.filename("One"))
        self.assertNotIn(str(ALICE.id), json.dumps(got))

    async def test_the_campaign_list_has_campaigns_with_a_shown_entry(self) -> None:
        got = await pages.list_campaigns(self.web, self.carol)
        self.assertEqual(
            [(c["ref"], c["name"], c["entries"]) for c in got],
            [(self.cref, "One", 3), (self.oref, "Two", 1)],
        )
        self.assertEqual({c["serverName"] for c in got}, {THURSDAY.name})
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)
        await self.store_b.add(  # only hidden: not listed
            THURSDAY.id, three.id, ALICE.id, kind="monster", name="Secret", text="x"
        )
        self.assertEqual(len(await pages.list_campaigns(self.web, self.carol)), 2)
        elsewhere = await self.session_of(CAROL, [QUILLON])
        self.assertEqual(await pages.list_campaigns(self.web, elsewhere), [])

    async def test_the_list_filters_before_it_limits(self) -> None:
        for n in range(101):
            await self.campaigns.create(THURSDAY.id, f"Aaa {n:03d}", ALICE.id)
        zed = await self.campaigns.create(THURSDAY.id, "Zed", ALICE.id)
        await self.store_b.add(
            THURSDAY.id, zed.id, ALICE.id, kind="rule", name="Last", text="x", players_may_know=True
        )
        names = [c["name"] for c in await pages.list_campaigns(self.web, self.carol)]
        self.assertEqual(names, ["One", "Two", "Zed"])


class BookTags(HomebrewBase):
    """A shared book's entries are tagged like the Discord players' card (#1579)."""

    async def mark(self, name: str, edition: str | None, *, target: str | None = None) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE homebrew_entries SET from_book = TRUE, book_edition = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND name = %s",
                (edition, THURSDAY.id, self.one.id, name),
            )
            if target is not None:
                await conn.execute(
                    "UPDATE campaigns SET target_ruleset = %s WHERE guild_id = %s AND id = %s",
                    (target, THURSDAY.id, self.one.id),
                )

    async def tags(self) -> dict[str, tuple[bool, bool]]:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        return {e["name"]: (e["fromBook"], e["legacy"]) for e in got["entries"]}

    async def test_a_2014_book_in_a_newer_campaign_is_legacy_a_2024_book_is_not(self) -> None:
        await self.mark("Zap", "2014", target="2024")
        await self.mark("Arc", "2024")
        self.assertEqual(
            await self.tags(),
            {"Arc": (True, False), "Zap": (True, True), "Moon Lamp": (False, False)},
        )

    async def test_the_same_test_as_the_bots(self) -> None:
        from dmbot.rules.book_rank import book_is_legacy

        for edition, target in (
            ("2014", "2024"),
            ("2014", "2014"),
            ("2024", "2024"),
            (None, "2024"),
        ):
            with self.subTest(edition=edition, target=target):
                await self.mark("Zap", edition, target=target)
                entries = await self.store_b.list(THURSDAY.id, self.one.id)
                zap = next(e for e in entries if e.name == "Zap")
                self.assertEqual((await self.tags())["Zap"][1], book_is_legacy(zap, target))

    async def test_a_hidden_book_entry_still_never_leaves_and_no_book_name_or_author(self) -> None:
        await self.mark("The Lich", "2014", target="2024")
        shown = json.dumps(await pages.read_homebrew(self.web, self.carol, self.cref))
        self.assertNotIn("The Lich", shown)
        self.assertNotIn(str(ALICE.id), shown)

    async def test_a_non_member_gets_nothing_and_the_file_ignores_the_tags(self) -> None:
        await self.mark("Zap", "2014", target="2024")
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await pages.read_homebrew(self.web, elsewhere, self.cref)
        _name, text = await pages.render(self.web, self.carol, self.cref)
        shown = [e for e in await self.store_b.list(THURSDAY.id, self.one.id) if e.players_may_know]
        self.assertEqual(text.encode(), homebrew_file.write("One", shown).encode())


class TheFile(HomebrewBase):
    async def test_the_download_is_the_bots_file_for_the_shown_entries_byte_for_byte(self) -> None:
        name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.shown_entries()
        self.assertEqual(len(shown), 3)
        self.assertEqual(text.encode(), homebrew_file.write("One", shown).encode())
        self.assertEqual(name, homebrew_file.filename("One"))
        self.assertNotIn(SECRET, text)
        self.assertNotIn("The Lich", text)

    async def test_a_campaign_with_nothing_shown_has_no_file(self) -> None:
        empty = await self.campaigns.create(THURSDAY.id, "Empty", ALICE.id)
        await self.store_b.add(THURSDAY.id, empty.id, ALICE.id, kind="monster", name="M", text="x")
        with self.assertRaises(RecapError) as caught:
            await pages.render(self.web, self.carol, f"{THURSDAY.id}.{empty.id}")
        self.assertEqual(caught.exception.code, "not_found")


class TheDoorsAndRole(HomebrewBase):
    async def test_the_websites_role_cannot_read_the_table(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute("SELECT * FROM homebrew_entries")

    async def test_the_doors_answer_nothing_without_the_server_set(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew(%s, %s) AS r, dmbot_web_homebrew_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_the_doors_check_the_sessions_server_list_themselves(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        async with self.web.user(CAROL.id, session=elsewhere.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew(%s, %s) AS r, dmbot_web_homebrew_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_each_door_has_one_caller(self) -> None:
        for door in ("dmbot_web_homebrew(", "dmbot_web_homebrew_campaigns"):
            self.assertEqual(callers(door), {"schema.py", "web/homebrew.py"}, door)


class TheRoutes(HomebrewBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(enforce_plans=True),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, user: Any, guilds: Any) -> None:
        token = await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def test_the_list_the_page_and_the_download_agree_with_plans_enforced(self) -> None:
        await self.sign_in(CAROL, [THURSDAY])
        mine = await self.client.get("/me/homebrew", headers=HEADERS)
        self.assertEqual([c["ref"] for c in mine.json()["campaigns"]], [self.cref, self.oref])
        page = await self.client.get(f"/me/campaigns/{self.cref}/homebrew", headers=HEADERS)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual([e["name"] for e in page.json()["entries"]], ["Arc", "Zap", "Moon Lamp"])
        file = await self.client.get(
            f"/me/campaigns/{self.cref}/homebrew/download", headers=HEADERS
        )
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(
            file.headers["content-disposition"], 'attachment; filename="homebrew-one.txt"'
        )
        self.assertEqual(
            file.content, homebrew_file.write("One", await self.shown_entries()).encode()
        )

    async def test_someone_outside_the_server_gets_404_and_the_list_is_empty(self) -> None:
        await self.sign_in(CAROL, [QUILLON])
        for path in (
            f"/me/campaigns/{self.cref}/homebrew",
            f"/me/campaigns/{self.cref}/homebrew/download",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        self.assertEqual((await self.client.get("/me/homebrew")).json(), {"campaigns": []})
