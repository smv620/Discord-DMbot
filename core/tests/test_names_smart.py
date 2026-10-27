"""The Names page's search by meaning (#2733, #2664): "ice goddess" finds the frost goddess,
"ally of Brannoc" finds the names connected to Brannoc; only a DM can ask, before any word leaves
for Voyage AI; nothing from another campaign; nothing secret in the text searched; a failure says
plainly that the search could not run; and the DM's words are never logged. A fake Voyage puts
words about the same thing at the same point. Invented names."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import httpx

from dmbot.ai import AIOutOfFunds
from dmbot.memory import vectors as vs
from dmbot.memory.embed_sources import EntitySource
from dmbot.memory.entity_text import compose
from dmbot.memory.voyage import ProviderError, Ranked
from dmbot.web import names_smart, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW
from tests.test_web_all_names import MEMBER, Listing
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

MODEL = "voyage-3.5"
TOPICS = {
    ("ice", "frost", "winter"): [1.0, 0.0, 0.0],
    ("ally", "friend"): [0.0, 1.0, 0.0],
    ("dragon", "wyrm"): [0.0, 0.0, 1.0],
}


def point(text: str) -> list[float]:
    out = [0.05, 0.05, 0.05]
    for words, vector in TOPICS.items():
        if any(w in text.lower() for w in words):
            out = [a + b for a, b in zip(out, vector, strict=True)]
    return out


class FakeVoyage:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.embedded: list[str] = []
        self.reranked: list[tuple[str, list[str]]] = []
        self.fail = fail

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        if self.fail:
            raise self.fail
        self.embedded += texts
        return [point(t) for t in texts]

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[Ranked]:
        self.reranked.append((query, list(documents)))
        score = [sum(a * b for a, b in zip(point(query), point(d), strict=True)) for d in documents]
        order = sorted(range(len(documents)), key=lambda i: -score[i])[:top_k]
        return [Ranked(i, min(score[i] / 2, 1.0)) for i in order]


class Smart(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (Listing made Mira, the Keep and Dawnbreaker with no description: give them one)
        await self.describe(self.mira, "A ferrywoman who knows every shore")
        self.auril = await self.add("Auril", "deity", description="A goddess of ice and winter")
        self.brannoc = await self.add("Brannoc", "npc", description="A smith in the town")
        self.vecnor = await self.add(
            "Vecnor", "deity", description="A frost lich nobody knows of yet"
        )
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", self.vecnor
        )
        await self.link("ally_of", self.mira, self.brannoc)
        self.voyage = FakeVoyage()
        self.calls = 0
        self.smart = names_smart.SmartNames(self.voyage, MODEL, self.count)
        await self.make_numbers(self.g, self.one.id)
        self.bob = await self.signed(BOB, THURSDAY)

    async def count(self) -> None:
        self.calls += 1

    async def describe(self, entity: str, text: str) -> None:
        await self.sql("UPDATE memory_entities SET description = %s WHERE id = %s", text, entity)

    async def link(self, predicate: str, a: str, b: str, *, secret: bool = False) -> None:
        from dmbot.memory.models import DM as DM_SOURCE

        await self.memory.add_relation(
            self.g, self.one.id, a, predicate, b, source=DM_SOURCE, confidence=1.0,
            status="confirmed", secret=secret,
        )  # fmt: skip

    async def make_numbers(self, guild: int, campaign: str, model: str = MODEL) -> None:
        """The numbers the after-session job would keep: the entry source's own text, by `point`."""
        items = [
            vs.Item(p.kind, p.owner_id, p.hash, point(p.text))
            for p in await EntitySource(self.db).pieces(guild, campaign)
        ]
        await vs.VectorStore(self.db).save(guild, campaign, model, items)

    async def ask(self, words: str, who: Any = None, smart: Any = ...) -> dict[str, Any]:
        return await names_smart.search(
            self.web, who or self.session, self.cref, words,
            self.smart if smart is ... else smart,
        )  # fmt: skip

    async def unavailable(self, words: str = "ice", **kw: Any) -> str:
        with self.assertRaises(names_smart.NotAvailable) as caught:
            await self.ask(words, **kw)
        return caught.exception.why

    async def refused_ask(self, who: Any, words: str = "ice") -> str:
        with self.assertRaises(RecapError) as caught:
            await self.ask(words, who)
        return caught.exception.code


class TheText(Smart):
    async def text_of(self, entity: str) -> str | None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT dmbot_entity_parts(%s, %s, %s) AS p", (self.g, self.one.id, entity)
            )
            row = await cur.fetchone()
        return compose(row["p"]) if row and row["p"] else None

    async def test_the_text_holds_the_name_the_other_names_and_the_connections(self) -> None:
        text = await self.text_of(self.mira)
        assert text is not None
        self.assertTrue(text.startswith("Mira Voss: A ferrywoman who knows every shore"))
        self.assertIn("Also called the Ferrywoman.", text)
        self.assertIn("is an ally of Brannoc", text)

    async def test_a_secret_name_and_a_secret_connection_are_never_in_it(self) -> None:
        await self.link("enemy_of", self.mira, self.auril, secret=True)
        text = await self.text_of(self.mira) or ""
        self.assertNotIn("Lady Grey", text)  # (Mira's secret name)
        self.assertNotIn("Auril", text)  # (a secret connection)

    async def test_a_connection_to_a_name_the_players_do_not_know_is_left_out(self) -> None:
        await self.link("ally_of", self.mira, self.vecnor)
        self.assertNotIn("Vecnor", await self.text_of(self.mira) or "")

    async def test_a_name_with_no_description_has_no_text(self) -> None:
        self.assertIsNone(await self.text_of(self.keep))

    async def test_the_numbers_are_made_from_the_same_text(self) -> None:
        pieces = {
            p.owner_id: p.text for p in await EntitySource(self.db).pieces(self.g, self.one.id)
        }
        self.assertEqual(pieces[self.mira], await self.text_of(self.mira))


class TheSearch(Smart):
    async def test_ice_goddess_finds_the_frost_goddess_first(self) -> None:
        got = await self.ask("ice goddess")
        found = [n["name"] for n in got["names"]]
        self.assertIn("Auril", found)
        self.assertNotIn("Brannoc", found)

    async def test_a_connection_phrase_finds_the_names_connected(self) -> None:
        got = await self.ask("ally of Brannoc")
        self.assertEqual({n["name"] for n in got["names"]}, {"Mira Voss", "Brannoc"})

    async def test_the_rows_have_the_lists_own_shape(self) -> None:
        got = await self.ask("ally of Brannoc")
        row = next(n for n in got["names"] if n["name"] == "Mira Voss")
        plain = next(n for n in (await self.page())["names"] if n["name"] == "Mira Voss")
        self.assertEqual(row, plain)

    async def test_a_dm_finds_what_the_players_do_not_know_yet_and_it_says_so(self) -> None:
        got = await self.ask("frost")
        vecnor = next(n for n in got["names"] if n["name"] == "Vecnor")
        self.assertTrue(vecnor["hidden"])

    async def test_nothing_close_is_an_empty_answer_not_a_failure(self) -> None:
        got = await self.ask("dragon")
        self.assertEqual((got["names"], got["capped"]), ([], False))

    async def test_another_campaigns_numbers_are_never_compared(self) -> None:
        other = await self.memory.add_entity(
            self.g, self.two.id, type="deity", name="Elsewhere", source="dm",
            status="confirmed", description="A goddess of ice and winter",
        )  # fmt: skip
        await self.make_numbers(self.g, self.two.id)
        self.assertIsNotNone(other)
        got = await self.ask("ice goddess")
        self.assertNotIn("Elsewhere", [n["name"] for n in got["names"]])

    async def test_numbers_made_with_another_model_are_not_compared(self) -> None:
        smart = names_smart.SmartNames(self.voyage, "voyage-9")
        got = await self.ask("ice goddess", smart=smart)
        self.assertEqual(got["names"], [])

    async def test_both_calls_are_counted_and_the_words_are_cut_and_tidied(self) -> None:
        await self.ask("  ice \n  goddess  " + "x" * 500)
        self.assertEqual(self.calls, 2)
        self.assertEqual(len(self.voyage.embedded[0]), names_smart.WORDS_MAX)
        self.assertTrue(self.voyage.embedded[0].startswith("ice goddess xxx"))

    async def test_the_words_are_never_logged(self) -> None:
        with self.assertLogs("dmbot.web.names_smart", level="INFO") as logged:
            await self.ask("ice goddess")
        self.assertNotIn("goddess", "\n".join(logged.output))


class TheRefusals(Smart):
    async def test_only_a_dm_may_ask_and_no_word_leaves_for_anyone_else(self) -> None:
        member = await self.signed(BOB, MEMBER)
        for who in (self.bob, member):  # (a manager who is not a DM, too)
            self.assertEqual(await self.refused_ask(who), "not_found")
        self.assertEqual(self.voyage.embedded, [])
        self.assertEqual(self.calls, 0)

    async def test_another_server_and_a_bad_address_get_not_found(self) -> None:
        for ref in (f"999.{self.one.id}", "nonsense", f"{self.g}.{'0' * 32}"):
            with self.assertRaises(RecapError) as caught:
                await names_smart.search(self.web, self.session, ref, "ice", self.smart)
            self.assertEqual(caught.exception.code, "not_found", ref)
        self.assertEqual(self.voyage.embedded, [])

    async def test_nothing_to_search_for_is_refused(self) -> None:
        for words in ("", "   \n "):
            self.assertEqual(await self.refused_ask(self.session, words), "bad_text")

    async def test_a_dm_stopped_being_one_is_not_answered(self) -> None:
        await self.sql("DELETE FROM campaign_dms WHERE campaign_id = %s", self.one.id)
        self.assertEqual(await self.refused_ask(self.session), "not_found")


class TheFallback(Smart):
    async def test_a_search_that_is_switched_off_says_so_for_a_dm_only(self) -> None:
        self.assertEqual(await self.unavailable(smart=None), "off")
        self.assertEqual(await self.refused_ask(self.bob), "not_found")

    async def test_no_credit_left_says_so(self) -> None:
        self.voyage.fail = AIOutOfFunds("out")
        self.assertEqual(await self.unavailable(), "no_credit")

    async def test_voyage_not_answering_says_so(self) -> None:
        self.voyage.fail = ProviderError("down")
        self.assertEqual(await self.unavailable(), "unreachable")


class TheList(Smart):
    async def test_the_list_door_takes_several_ids_and_one(self) -> None:
        both = await self.page(only=f"{self.mira},{self.sword}")
        self.assertEqual({n["name"] for n in both["names"]}, {"Mira Voss", "Dawnbreaker"})
        one = await self.page(only=self.mira)
        self.assertEqual([n["name"] for n in one["names"]], ["Mira Voss"])
        self.assertEqual((await self.page(only="nothing"))["names"], [])


class TheRoute(Smart):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW, smart_names=self.smart,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(self.client.aclose)
        self.url = f"/me/campaigns/{self.cref}/all-names/smart"  # (a GET: no longer a route)

    async def post(self, words: Any, ref: str | None = None, client: Any = None) -> Any:
        url = f"/me/campaigns/{ref or self.cref}/all-names/search"
        return await (client or self.client).post(url, json={"q": words}, headers=HEADERS)

    async def test_the_search_over_http_is_a_post_with_the_words_in_the_body(self) -> None:
        got = await self.post("ice goddess")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertIn("Auril", [n["name"] for n in body["names"]])
        self.assertIs(body["capped"], False)
        self.assertNotIn("available", body)
        # never in the address: a GET is not a route
        self.assertIn(
            (await self.client.get(self.url, params={"q": "ice goddess"})).status_code, (404, 405)
        )

    async def test_a_search_that_cannot_run_is_the_not_available_refusal(self) -> None:
        self.voyage.fail = ProviderError("down")
        got = await self.post("ice")
        self.assertEqual((got.status_code, got.json()), (409, {"error": "not_available"}))

    async def test_a_search_switched_off_is_the_not_available_refusal_too(self) -> None:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW, smart_names=None,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        got = await self.post("ice", client=client)
        self.assertEqual((got.status_code, got.json()), (409, {"error": "not_available"}))

    async def test_refusals_over_http(self) -> None:
        for bad in ("", "   ", None, 5):
            got = await self.post(bad)
            self.assertEqual((got.status_code, got.json()), (422, {"error": "bad_text"}), bad)
        nothing = await self.client.post(
            self.url.replace("smart", "search"), content=b"not json", headers=HEADERS
        )
        self.assertEqual(nothing.status_code, 422)
        other = await self.post("ice", ref=f"{self.g}.{'0' * 32}")
        self.assertEqual((other.status_code, other.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await self.post("ice", client=anonymous)).status_code, 401)

    async def test_a_manager_who_is_not_a_dm_gets_not_found_over_http(self) -> None:
        token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        got = await self.post("ice", client=client)
        self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}))
        self.assertEqual(self.voyage.embedded, [])

    async def test_one_person_gets_twenty_searches_a_minute(self) -> None:
        for _ in range(20):
            self.assertEqual((await self.post("ice")).status_code, 200)
        self.assertEqual((await self.post("ice")).status_code, 429)

    async def test_the_door_has_one_caller(self) -> None:
        from tests.test_recap_web import callers

        self.assertEqual(callers("dmbot_web_names_near("), {"schema.py", "web/names_smart.py"})


class TheEdges(Smart):
    async def near(self, vector: list[float]) -> tuple[list[dict[str, Any]], bool] | None:
        return await names_smart._near(self.web, self.session, self.g, self.one.id, MODEL, vector)

    async def test_a_zero_a_huge_and_an_empty_search_vector_find_nothing(self) -> None:
        for vector in ([0.0, 0.0, 0.0], [0.5] * 5000, []):
            self.assertEqual(await self.near(vector), ([], False), len(vector))

    async def test_an_empty_vector_only_proves_who_is_asking(self) -> None:
        self.assertIsNone(
            await names_smart._near(self.web, self.bob, self.g, self.one.id, MODEL, [])
        )

    async def test_a_person_a_merged_and_a_proposed_name_are_never_found(self) -> None:
        for name, kind in (("Pip", "npc"), ("Old Mira", "npc"), ("Maybe Mira", "npc")):
            made = await self.add(name, kind, description="A goddess of ice and winter")
            if name == "Pip":  # (a person at the table: DMbot makes these itself)
                await self.sql("UPDATE memory_entities SET type = 'person' WHERE id = %s", made)
            if name == "Old Mira":
                await self.sql(
                    "UPDATE memory_entities SET status = 'merged', merged_into = %s WHERE id = %s",
                    self.mira,
                    made,
                )
            if name == "Maybe Mira":
                await self.sql("UPDATE memory_entities SET status = 'proposed' WHERE id = %s", made)
        await self.make_numbers(self.g, self.one.id)
        found = {n["name"] for n in (await self.ask("ice goddess"))["names"]}
        self.assertFalse(found & {"Pip", "Old Mira", "Maybe Mira"}, found)

    async def test_the_text_the_reranker_reads_holds_nothing_secret(self) -> None:
        await self.link("enemy_of", self.mira, self.auril, secret=True)
        await self.make_numbers(self.g, self.one.id)
        await self.ask("ally of Brannoc")
        sent = "\n".join(d for _, docs in self.voyage.reranked for d in docs)
        self.assertIn("Mira Voss", sent)
        self.assertNotIn("Lady Grey", sent)  # (Mira's secret name)
        self.assertNotIn("is an enemy of Auril", sent)  # (a secret connection)

    async def test_a_scan_that_runs_out_of_time_says_so_and_costs_nothing_more(self) -> None:
        from unittest.mock import patch

        import psycopg

        real = names_smart._near
        calls = 0

        async def slow(*args: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise psycopg.errors.QueryCanceled("statement timeout")
            return await real(*args)

        with patch.object(names_smart, "_near", slow):
            self.assertEqual(await self.unavailable("ice goddess"), "slow")
        self.assertEqual(self.calls, 1)  # (the rerank was never asked)

    async def test_more_numbers_than_one_search_compares_is_said(self) -> None:
        await self.sql(
            "INSERT INTO memory_vectors (guild_id, campaign_id, owner_kind, owner_id, model,"
            " text_hash, vector, made_at)"
            " SELECT %s, %s, 'assertion', 'filler-' || n, %s, 'h', ARRAY[0.1, 0.1, 0.1]::real[], 1"
            " FROM generate_series(1, 10001) AS n",
            self.g, self.one.id, MODEL,
        )  # fmt: skip
        got = await self.ask("ice goddess")
        self.assertTrue(got["capped"])
        self.assertIn("Auril", [n["name"] for n in got["names"]])  # (the newest are compared)


class ThePrivacy(Smart):
    """What a name's text may hold is what the players may know (#2756)."""

    async def text_of(self, entity: str) -> str:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT dmbot_entity_parts(%s, %s, %s) AS p", (self.g, self.one.id, entity)
            )
            row = await cur.fetchone()
        return compose(row["p"]) if row and row["p"] else ""

    async def test_a_connection_the_dm_has_not_revealed_is_left_out(self) -> None:
        await self.link("enemy_of", self.mira, self.auril)
        await self.sql(
            "UPDATE memory_relations SET unrevealed = true"
            " WHERE (subject_id, object_id) IN ((%s, %s), (%s, %s))",
            self.mira, self.auril, self.auril, self.mira,
        )  # fmt: skip
        self.assertNotIn("Auril", await self.text_of(self.mira))

    async def test_a_connection_to_a_story_planning_entry_is_left_out(self) -> None:
        for kind in ("thread", "quest", "mystery", "hook", "goal", "promise", "deed"):
            planned = await self.add(f"The {kind}", kind, description="Something planned")
            await self.link("ally_of", self.mira, planned)
        text = await self.text_of(self.mira)
        for kind in ("thread", "quest", "mystery", "hook", "goal", "promise", "deed"):
            self.assertNotIn(f"The {kind}", text)

    async def test_a_connection_to_an_entry_whose_own_name_is_a_secret_is_left_out(self) -> None:
        await self.link("ally_of", self.mira, self.auril)
        self.assertIn("Auril", await self.text_of(self.mira))
        await self.alias(self.auril, "Auril", secret=True)  # (its own spelling is the secret)
        self.assertNotIn("Auril", await self.text_of(self.mira))

    async def test_a_public_connection_still_reads(self) -> None:
        self.assertIn("Brannoc", await self.text_of(self.mira))

    async def test_the_helper_is_not_open_to_everyone(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT has_function_privilege('public', oid, 'EXECUTE') AS open"
                " FROM pg_proc WHERE proname = 'dmbot_entity_parts'"
            )
            rows = await cur.fetchall()
        self.assertTrue(rows)
        self.assertFalse(any(r["open"] for r in rows))


class TheWaits(Smart):
    async def test_a_voyage_call_that_never_answers_is_unreachable_after_the_limit(self) -> None:
        class Hung(FakeVoyage):
            async def embed(self, texts: Sequence[str], *, query: bool = False) -> Any:
                await asyncio.sleep(60)

        smart = names_smart.SmartNames(Hung(), MODEL, self.count)
        with patch.object(names_smart, "CALL_WAIT_S", 0.05):
            self.assertEqual(await self.unavailable(smart=smart), "unreachable")

    async def test_a_rerank_that_never_answers_is_unreachable_too(self) -> None:
        class Hung(FakeVoyage):
            async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> Any:
                await asyncio.sleep(60)

        smart = names_smart.SmartNames(Hung(), MODEL, self.count)
        with patch.object(names_smart, "CALL_WAIT_S", 0.05):
            self.assertEqual(await self.unavailable("ice goddess", smart=smart), "unreachable")

    async def test_no_free_scan_slot_in_time_is_slow(self) -> None:
        for _ in range(names_smart.SEARCHES_AT_ONCE):
            await self.smart.scans.acquire()  # (every slot taken)
        with patch.object(names_smart, "CALL_WAIT_S", 0.05):
            self.assertEqual(await self.unavailable("ice goddess"), "slow")

    async def test_at_most_two_scans_run_at_once_and_the_slot_is_given_back(self) -> None:
        running = peak = 0
        real = names_smart._near

        async def counting(*args: Any) -> Any:
            nonlocal running, peak
            if not args[-1]:  # (the who-is-asking check reads nothing)
                return await real(*args)
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            try:
                return await real(*args)
            finally:
                running -= 1

        with patch.object(names_smart, "_near", counting):
            await asyncio.gather(*(self.ask("ice goddess") for _ in range(5)))
        self.assertEqual(peak, names_smart.SEARCHES_AT_ONCE)
        for _ in range(names_smart.SEARCHES_AT_ONCE):  # all slots are free again
            await asyncio.wait_for(self.smart.scans.acquire(), 1)

    async def test_the_slot_is_given_back_when_the_scan_fails(self) -> None:
        import psycopg

        async def cancelled(*args: Any) -> Any:
            if not args[-1]:
                return ([], False)
            raise psycopg.errors.QueryCanceled("statement timeout")

        with patch.object(names_smart, "_near", cancelled):
            self.assertEqual(await self.unavailable("ice goddess"), "slow")
        for _ in range(names_smart.SEARCHES_AT_ONCE):
            await asyncio.wait_for(self.smart.scans.acquire(), 1)

    def test_the_website_pool_cuts_a_statement_at_five_seconds(self) -> None:
        import inspect

        from dmbot.web import __main__ as web_main

        self.assertIn("statement_timeout=5000", inspect.getsource(web_main))


class TheStoryTypes(Smart):
    def test_the_sql_list_of_story_planning_types_is_the_one_the_players_rules_use(self) -> None:
        import re

        from dmbot.memory.meaning import PLAYER_HIDDEN_TYPES
        from dmbot.schema import NAMES_SMART

        listed = re.findall(r"o\.type NOT IN \(([^)]*)\)", NAMES_SMART)
        self.assertEqual(len(listed), 1)
        in_sql = tuple(re.findall(r"'([a-z_]+)'", listed[0]))
        self.assertEqual(in_sql, PLAYER_HIDDEN_TYPES)
