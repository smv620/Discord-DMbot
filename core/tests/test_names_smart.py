"""The Names page's search by meaning (#2733, #2664): "ice goddess" finds the frost goddess,
"ally of Brannoc" finds the names connected to Brannoc; only a DM can ask, before any word leaves
for Voyage AI; nothing from another campaign; nothing secret in the text searched; a failure says
plainly that the search could not run; and the DM's words are never logged. A fake Voyage puts
words about the same thing at the same point. Invented names."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

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
from tests.test_recap_web import BOB, NOW
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
        self.assertTrue(got["available"])
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
        self.assertTrue(got["canEdit"])

    async def test_a_dm_finds_what_the_players_do_not_know_yet_and_it_says_so(self) -> None:
        got = await self.ask("frost")
        vecnor = next(n for n in got["names"] if n["name"] == "Vecnor")
        self.assertTrue(vecnor["hidden"])

    async def test_nothing_close_is_an_empty_answer_not_a_failure(self) -> None:
        got = await self.ask("dragon")
        self.assertEqual((got["available"], got["why"], got["names"]), (True, None, []))

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
        got = await self.ask("ice", smart=None)
        self.assertEqual((got["available"], got["why"], got["names"]), (False, "off", []))
        self.assertEqual(await self.refused_ask(self.bob), "not_found")

    async def test_no_credit_left_says_so(self) -> None:
        self.voyage.fail = AIOutOfFunds("out")
        got = await self.ask("ice")
        self.assertEqual((got["available"], got["why"]), (False, "no_credit"))

    async def test_voyage_not_answering_says_so(self) -> None:
        self.voyage.fail = ProviderError("down")
        got = await self.ask("ice")
        self.assertEqual((got["available"], got["why"]), (False, "unreachable"))


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
        self.url = f"/me/campaigns/{self.cref}/all-names/smart"

    async def test_the_search_over_http(self) -> None:
        got = await self.client.get(self.url, params={"q": "ice goddess"})
        self.assertEqual(got.status_code, 200)
        self.assertIn("Auril", [n["name"] for n in got.json()["names"]])

    async def test_refusals_over_http(self) -> None:
        self.assertEqual((await self.client.get(self.url)).status_code, 422)  # nothing to search
        other = await self.client.get(f"/me/campaigns/{self.g}.{'0' * 32}/all-names/smart?q=ice")
        self.assertEqual((other.status_code, other.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get(self.url, params={"q": "ice"})).status_code, 401)

    async def test_one_person_gets_twenty_searches_a_minute(self) -> None:
        for _ in range(20):
            self.assertEqual(
                (await self.client.get(self.url, params={"q": "ice"})).status_code, 200
            )
        slow = await self.client.get(self.url, params={"q": "ice"})
        self.assertEqual(slow.status_code, 429)

    async def test_the_door_has_one_caller(self) -> None:
        from tests.test_recap_web import callers

        self.assertEqual(callers("dmbot_web_names_near("), {"schema.py", "web/names_smart.py"})
