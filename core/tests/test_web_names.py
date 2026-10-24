"""The campaign's names on the website, with the narrator's "said as" spelling (#1451), run as the
website's own database role: only a DM of the campaign reads or changes them; a secret name, a
person at the table, a guess and a note of how a name was misheard never appear; the write goes
through the change log, so Undo on the Discord name card still works; and the narrator reads the
saved spelling. The website's role has no right on the memory tables at all."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import psycopg

from dmbot.bot import DMBot
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.recaps.audio import names as nm
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.names import list_names, set_said_as
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings


class Names(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        g = THURSDAY.id
        add = self.memory.add_entity
        self.kazeth = (
            await add(
                g, self.one.id, type="npc", name="Ka'zeth", source=DM_SOURCE, status="confirmed"
            )
        ).value.id
        self.title = (
            await self.memory.add_alias(
                g, self.one.id, self.kazeth, "the Grey Voice", kind="title", source=DM_SOURCE,
                status="confirmed", secret=True,
            )
        ).value.id  # fmt: skip
        self.short = (
            await self.memory.add_alias(
                g, self.one.id, self.kazeth, "Zeth", kind="short", source=DM_SOURCE,
                status="confirmed",
            )
        ).value.id  # fmt: skip
        self.heard = (
            await self.memory.add_alias(
                g, self.one.id, self.kazeth, "Kayzeth", kind="misheard", source=DM_SOURCE,
                status="confirmed",
            )
        ).value.id  # fmt: skip
        self.old = (
            await self.memory.add_alias(
                g, self.one.id, self.kazeth, "Kazet", kind="old", source=DM_SOURCE,
                status="confirmed",
            )
        ).value.id  # fmt: skip
        await add(g, self.one.id, type="npc", name="Maybe Manor", source=DM_SOURCE)  # a guess
        self.other = (
            await add(
                g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE, status="confirmed"
            )
        ).value.id
        self.cref = f"{g}.{self.one.id}"
        self.oref = f"{g}.{self.two.id}"
        self.main = await self.alias_of("Ka'zeth")

    async def alias_of(self, text: str, campaign: str | None = None) -> str:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_aliases WHERE campaign_id = %s AND text = %s",
                (campaign or self.one.id, text),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def person(self) -> None:
        """A person at the table, as DMbot records them (only DMbot makes these)."""
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("SELECT 1")  # keep the guild set for the inserts below
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'person', 'Alice Real', '',"
                " 'confirmed', 'dm', 0)",
                (THURSDAY.id, self.one.id, "e" * 32),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at) VALUES"
                " (%s, %s, %s, %s, 'Alice Real', 'alice real', 'full', false, 'confirmed',"
                " '{}', 'dm', 0)",
                (THURSDAY.id, self.one.id, "a" * 32, "e" * 32),
            )

    async def names(self, campaign: str | None = None) -> dict[str, Any]:
        return {
            n["text"]: n for n in await list_names(self.web, self.session, campaign or self.cref)
        }

    # ---- reading -----------------------------------------------------------------------

    async def test_a_dm_sees_the_public_confirmed_names_and_nothing_else(self) -> None:
        await self.person()
        shown = await self.names()
        self.assertEqual(set(shown), {"Ka'zeth", "Zeth"})
        self.assertEqual(set(shown["Zeth"]), {"id", "text", "kind", "saidAs"})
        self.assertIsNone(shown["Zeth"]["saidAs"])
        everything = str(await list_names(self.web, self.session, self.cref))
        for hidden in ("Grey Voice", "Kayzeth", "Kazet", "Maybe Manor", "Alice Real", "Brynwater"):
            self.assertNotIn(hidden, everything)

    async def test_another_campaigns_dm_a_stranger_and_a_bad_address_get_not_found(self) -> None:
        bob_token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        bob = await sessions.find(self.web, bob_token, now=NOW)
        assert bob is not None
        for who, ref in (
            (bob, self.cref),  # Bob DMs "Two", not "One"
            (self.session, self.oref),  # Alice DMs "One", not "Two"
            (self.session, f"999.{self.one.id}"),  # a server outside this session's list
            (self.session, "nonsense"),
            (self.session, f"{THURSDAY.id}.{self.one.id}²"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await list_names(self.web, who, ref)
            self.assertEqual(caught.exception.code, "not_found")

    # ---- writing -----------------------------------------------------------------------

    async def test_a_dm_sets_changes_and_clears_a_spelling(self) -> None:
        for spelling, expected in (("  Kah-zeth ", "Kah-zeth"), ("Kaz-eth", "Kaz-eth"), ("", None)):
            await set_said_as(self.web, self.session, self.cref, self.main, spelling)
            self.assertEqual((await self.names())["Ka'zeth"]["saidAs"], expected)
            self.assertEqual(
                await self.memory.said_as(THURSDAY.id, self.one.id, self.kazeth), expected
            )

    async def test_it_goes_through_the_change_log_so_undo_still_works(self) -> None:
        g, c = THURSDAY.id, self.one.id
        before = await self.memory.version(g, c)
        await set_said_as(self.web, self.session, self.cref, self.main, "Kah-zeth")
        await set_said_as(self.web, self.session, self.cref, self.main, "Kaz-eth")
        self.assertEqual(await self.memory.version(g, c), before + 2)  # one logged change each
        async with self.db.guild(g) as conn:
            cur = await conn.execute(
                "SELECT version, batch, op, source, table_name, row_id, before->>'said_as' AS was,"
                " after->>'said_as' AS now, before->'sound_codes' IS NOT NULL AS shaped"
                " FROM memory_changes WHERE campaign_id = %s AND version > %s ORDER BY version",
                (c, before),
            )
            log = await cur.fetchall()
        self.assertEqual(
            [(r["version"], r["batch"], r["op"], r["source"], r["row_id"]) for r in log],
            [(before + 1, before + 1, "update", "dm", self.main),
             (before + 2, before + 2, "update", "dm", self.main)],
        )  # fmt: skip
        self.assertEqual(
            [(r["was"], r["now"]) for r in log], [(None, "Kah-zeth"), ("Kah-zeth", "Kaz-eth")]
        )
        self.assertTrue(all(r["table_name"] == "memory_aliases" and r["shaped"] for r in log))
        # Undo on the Discord card takes the last one back, exactly as for a spelling set there.
        await self.memory.undo(g, c, before + 2, source=DM_SOURCE)
        self.assertEqual(await self.memory.said_as(g, c, self.kazeth), "Kah-zeth")

    async def test_whitespace_is_tidied_like_the_bot_does(self) -> None:
        await set_said_as(self.web, self.session, self.cref, self.main, " Kah \t  zeth\n")
        self.assertEqual((await self.names())["Ka'zeth"]["saidAs"], "Kah zeth")

    async def test_saying_the_same_again_logs_nothing(self) -> None:
        await set_said_as(self.web, self.session, self.cref, self.main, "Kah-zeth")
        version = await self.memory.version(THURSDAY.id, self.one.id)
        await set_said_as(self.web, self.session, self.cref, self.main, "Kah-zeth")
        await set_said_as(self.web, self.session, self.cref, self.short, None)
        self.assertEqual(await self.memory.version(THURSDAY.id, self.one.id), version)

    async def test_a_too_long_spelling_and_odd_characters_are_refused(self) -> None:
        for bad in ("x" * 81, "bell\x07", "zero\x00width"):
            with self.subTest(bad=bad[:8]), self.assertRaises(RecapError) as caught:
                await set_said_as(self.web, self.session, self.cref, self.main, bad)
            self.assertEqual(caught.exception.code, "bad_text")
        await set_said_as(self.web, self.session, self.cref, self.main, "x" * 80)  # the limit
        self.assertEqual(len((await self.names())["Ka'zeth"]["saidAs"]), 80)

    async def test_a_secret_name_a_stranger_and_another_campaign_are_refused(self) -> None:
        bob_token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        bob = await sessions.find(self.web, bob_token, now=NOW)
        assert bob is not None
        theirs = await self.alias_of("Brynwater", self.two.id)
        for who, ref, alias in (
            (self.session, self.cref, self.title),  # a secret name: told as "not found"
            (self.session, self.cref, self.heard),  # a note of how a name was misheard
            (self.session, self.cref, self.old),  # an old spelling: nothing is spoken from it
            (self.session, self.cref, "0" * 32),
            (self.session, self.cref, "not-an-id"),
            (self.session, self.cref, theirs),  # another campaign's name through my campaign
            (self.session, self.oref, theirs),  # another campaign I do not DM
            (bob, self.cref, self.main),  # not a DM of it
        ):
            with self.subTest(alias=alias[:6]), self.assertRaises(RecapError) as caught:
                await set_said_as(self.web, who, ref, alias, "Nope")
            self.assertEqual(caught.exception.code, "not_found")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_aliases WHERE said_as IS NOT NULL"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_door_answers_a_secret_name_exactly_as_a_missing_one(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            answers = []
            for alias in (self.title, "0" * 32):
                for spelling in ("Grey", None):  # setting it, and clearing it
                    cur = await conn.execute(
                        "SELECT dmbot_web_set_said_as(%s, %s, %s, %s) AS r",
                        (THURSDAY.id, self.one.id, alias, spelling),
                    )
                    row = await cur.fetchone()
                    answers.append(row and row["r"])
        self.assertEqual(answers, ["not_found"] * 4)

    async def test_the_door_answers_nothing_without_the_server_set_for_the_transaction(
        self,
    ) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_set_said_as(%s, %s, %s, 'x') AS r, dmbot_web_names(%s, %s) AS n",
                (THURSDAY.id, self.one.id, self.main, THURSDAY.id, self.one.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["n"]), ("not_allowed", None))

    # ---- the narrator and the role's rights ------------------------------------------------

    async def test_a_saved_spelling_reaches_the_narrators_dictionary(self) -> None:
        await set_said_as(self.web, self.session, self.cref, self.main, "Kah-zeth")

        async def recanted(*_a: object, **_k: object) -> tuple[str, ...]:
            return ()  # (nothing was taken back)

        bot = SimpleNamespace(lookup=LookupCache(self.memory), _recanted=recanted)
        got = await DMBot.recap_audio_inputs(bot, THURSDAY.id, self.one.id, SimpleNamespace())  # type: ignore[arg-type]
        assert got is not None
        rules = nm.rules(got.names)
        self.assertEqual([(r.string_to_replace, r.alias) for r in rules], [("Ka'zeth", "Kah-zeth")])
        self.assertIn("the Grey Voice", got.secret_names)

    async def test_the_website_role_cannot_touch_the_memory_tables(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for statement in (
                "SELECT text FROM memory_aliases",
                "SELECT said_as FROM memory_aliases",
                "UPDATE memory_aliases SET said_as = 'x'",
                "SELECT 1 FROM memory_changes",
            ):
                with (
                    self.subTest(statement),
                    self.assertRaises(psycopg.errors.InsufficientPrivilege),
                ):
                    async with conn.transaction():
                        await conn.execute(statement)

    async def test_the_doors_have_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_names("), {"schema.py", "web/names.py"})
        self.assertEqual(callers("dmbot_web_set_said_as("), {"schema.py", "web/names.py"})


class TheRoutes(Names):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_read_and_write_over_http(self) -> None:
        got = await self.client.get(f"/me/campaigns/{self.cref}/names")
        self.assertEqual(got.status_code, 200)
        self.assertEqual({n["text"] for n in got.json()["names"]}, {"Ka'zeth", "Zeth"})
        put = await self.client.put(
            f"/me/campaigns/{self.cref}/names/{self.main}/said-as",
            json={"saidAs": "Kah-zeth"}, headers=HEADERS,
        )  # fmt: skip
        self.assertEqual((put.status_code, put.json()), (200, {"ok": True}))
        self.assertEqual((await self.names())["Ka'zeth"]["saidAs"], "Kah-zeth")
        cleared = await self.client.put(
            f"/me/campaigns/{self.cref}/names/{self.main}/said-as",
            json={"saidAs": None}, headers=HEADERS,
        )  # fmt: skip
        self.assertEqual(cleared.status_code, 200)

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/names"
        self.assertEqual(
            (await self.client.get(f"/me/campaigns/{self.oref}/names")).status_code, 404
        )
        for body, status in (({"saidAs": "x" * 81}, 422), ({"saidAs": 5}, 422), ({}, 422)):
            put = await self.client.put(f"{url}/{self.main}/said-as", json=body, headers=HEADERS)
            self.assertEqual(put.status_code, status, body)
        secret = await self.client.put(
            f"{url}/{self.title}/said-as", json={"saidAs": "Grey"}, headers=HEADERS
        )
        self.assertEqual((secret.status_code, secret.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get(url)).status_code, 401)
