"""The website's list of "Words DMbot stopped asking about" (#3577, the website half of #3033): the
answers a DM gave to "make these words a kind?", listed for a DM of the campaign alone and lifted by
a queued action the bot applies through `clear_kind_offer`. Invented words throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory import kind_offers
from dmbot.web import kind_offers as web_offers
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_name_actions import Acting

WORDS = "sworn on the ashes of"
OTHER = "carries the lantern for"


class Offers(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.memory.answer_kind_offer(self.g, self.one.id, WORDS, kind_offers.NEVER, seen=3)
        await self.memory.answer_kind_offer(self.g, self.one.id, OTHER, kind_offers.LATER, seen=3)

    async def listed(self, who: Any = None, ref: str | None = None) -> list[dict[str, Any]]:
        got = await web_offers.read_kind_offers(self.web, who or self.session, ref or self.cref)
        offers: list[dict[str, Any]] = got["offers"]
        return offers

    async def clear(self, words: str) -> str:
        return await self.queue_clear(words)

    async def queue_clear(self, words: str, who: Any = None, ref: str | None = None) -> str:
        from dmbot.web import all_names

        return await all_names.queue_confirm(
            self.web,
            who or self.session,
            ref or self.cref,
            {"action": "offer-clear", "words": words},
        )


class TheList(Offers):
    async def test_a_dm_sees_each_answer_with_its_state(self) -> None:
        got = await self.listed()
        self.assertEqual(
            sorted((o["words"], o["state"]) for o in got),
            [(OTHER, "later"), (WORDS, "never")],
        )

    async def test_a_player_gets_nothing(self) -> None:
        player = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as caught:
            await self.listed(player)
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_another_campaign_has_its_own_list(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.listed(bob, f"{self.g}.{self.two.id}"), [])


class TheClear(Offers):
    async def test_lifting_a_never_removes_the_row_and_nothing_else(self) -> None:
        answer = await self.queue_clear(WORDS)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(answer))["state"], "done")
        self.assertEqual([o["words"] for o in await self.listed()], [OTHER])

    async def test_lifting_twice_is_done_not_a_failure(self) -> None:
        await self.queue_clear(WORDS)
        await self.apply_all()
        refused = await self.refused_clear(WORDS)
        self.assertEqual(refused, "not_found")  # the door no longer finds a row

    async def refused_clear(self, words: str, who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.queue_clear(words, who)
        return caught.exception.code

    async def test_two_presses_before_the_bot_gets_to_it_are_both_done(self) -> None:
        await self.queue_clear(WORDS)
        await self.queue_clear(WORDS)
        self.assertEqual([o.error for o in await self.apply_all()], ["", ""])
        self.assertEqual([o["words"] for o in await self.listed()], [OTHER])

    async def test_words_that_are_not_a_row_of_this_campaign_are_not_found(self) -> None:
        self.assertEqual(await self.refused_clear("words nobody said"), "not_found")
        # (another campaign's rows are not reachable by their words either)
        await self.memory.answer_kind_offer(
            self.g, self.two.id, "owes a favour to", "never", seen=3
        )
        self.assertEqual(await self.refused_clear("owes a favour to"), "not_found")

    async def test_only_a_dm_of_the_campaign_may_lift_it(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.refused_clear(WORDS, bob), "not_allowed")
        self.assertEqual(len(await self.listed()), 2)

    async def test_the_bot_refuses_a_sender_who_is_not_a_dm_any_more(self) -> None:
        await self.queue_clear(WORDS)
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertEqual(len(await self.listed()), 2)

    async def test_bad_words_are_refused_before_the_door(self) -> None:
        for bad in (None, 7, "", "   ", "a\x00b", "w" * 301):
            with self.assertRaises(RecapError, msg=repr(bad)):
                await self.queue_clear(bad)  # type: ignore[arg-type]

    async def test_the_limit_of_300_is_the_words_alone(self) -> None:
        long_words = "w" * 300
        await self.memory.answer_kind_offer(self.g, self.one.id, long_words, "never", seen=3)
        await self.queue_clear(long_words)
        self.assertEqual([o.error for o in await self.apply_all()], [""])


class TheRoutes(Offers):
    async def test_the_list_and_a_lift_over_http(self) -> None:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        base = f"/me/campaigns/{self.cref}"
        got = await client.get(f"{base}/kind-offers")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(len(got.json()["offers"]), 2)
        sent = await client.post(
            f"{base}/all-names-confirm",
            json={"action": "offer-clear", "words": WORDS},
            headers=HEADERS,
        )
        self.assertEqual(sent.status_code, 202)
        await self.apply_all()
        done = await client.get(f"{base}/all-names/answers/{sent.json()['answer']}")
        self.assertEqual(done.json()["state"], "done")
        left = await client.get(f"{base}/kind-offers")
        self.assertEqual([o["words"] for o in left.json()["offers"]], [OTHER])
