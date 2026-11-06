"""The website's "Is this the same place?" question (#3533): the proposed other names of a place
that are only another way of writing it, listed for a DM of the campaign alone, and the Yes and No
answers queued for the bot and applied through the store. Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import place_spellings, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_name_actions import Acting


class Spellings(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # "Greywater Keep" is a place. The first proposed name is the same letters with a space; the
        # second is a different place's words; the third is a proposed name of a person.
        self.spaced = await self.propose(self.keep, "Grey Water Keep")
        self.other_words = await self.propose(self.keep, "The Grey Tower")
        self.person = await self.propose(self.mira, "Miravoss")

    async def propose(self, entity: str, text: str) -> str:
        made = await self.memory.add_alias(
            self.g, self.one.id, entity, text, kind="short", source=DM_SOURCE, status=PROPOSED
        )
        return made.value.id

    async def listed(self, who: Any = None, ref: str | None = None) -> list[dict[str, Any]]:
        got = await place_spellings.read_place_spellings(
            self.web, who or self.session, ref or self.cref
        )
        spellings: list[dict[str, Any]] = got["spellings"]
        return spellings

    async def status_of(self, alias_id: str) -> str:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT status FROM memory_aliases WHERE campaign_id = %s AND id = %s",
                (self.one.id, alias_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["status"])


class TheList(Spellings):
    async def test_a_dm_gets_only_the_spelling_of_a_place_that_is_the_same_letters(self) -> None:
        self.assertEqual(
            await self.listed(),
            [
                {
                    "id": self.spaced,
                    "placeId": self.keep,
                    "place": "Greywater Keep",
                    "spelling": "Grey Water Keep",
                }
            ],
        )

    async def test_a_player_gets_nothing(self) -> None:
        player = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as caught:
            await self.listed(player)
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_another_campaign_sees_none_of_it(self) -> None:
        # Bob DMs "Two" in the same server: his list is his own, and Alice's campaign is not his.
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(await self.listed(bob, f"{self.g}.{self.two.id}"), [])
        with self.assertRaises(RecapError) as caught:
            await self.listed(bob)
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_a_confirmed_or_rejected_name_is_not_waiting(self) -> None:
        await self.memory.update_alias(
            self.g, self.one.id, self.spaced, source=DM_SOURCE, status=CONFIRMED
        )
        self.assertEqual(await self.listed(), [])


class TheAnswers(Spellings):
    async def test_yes_confirms_the_other_name_and_it_leaves_the_list(self) -> None:
        answer = await self.queue(self.keep, action="spelling-yes", alias=self.spaced)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(answer))["state"], "done")
        self.assertEqual(await self.status_of(self.spaced), CONFIRMED)
        self.assertEqual(await self.listed(), [])

    async def test_no_turns_it_down_for_good_and_it_leaves_the_list(self) -> None:
        await self.queue(self.keep, action="spelling-no", alias=self.spaced)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.status_of(self.spaced), REJECTED)
        self.assertEqual(await self.listed(), [])
        # (a turned-down name is what keeps the same pair from being offered again)

    async def test_answering_twice_is_not_a_failure_but_the_other_way_is_gone(self) -> None:
        await self.queue(self.keep, action="spelling-yes", alias=self.spaced)
        await self.apply_all()
        # the same answer again: the wanted end state is already there, so nothing is changed
        refused = await self.refused_action(self.keep, action="spelling-yes", alias=self.spaced)
        self.assertEqual(refused, "not_found")  # the door no longer finds a proposed name

    async def test_the_bot_says_gone_when_it_was_answered_the_other_way_meanwhile(self) -> None:
        answer = await self.queue(self.keep, action="spelling-yes", alias=self.spaced)
        await self.memory.update_alias(
            self.g, self.one.id, self.spaced, source=DM_SOURCE, status=REJECTED
        )
        self.assertEqual([o.error for o in await self.apply_all()], ["gone"])
        self.assertEqual((await self.poll(answer))["state"], "failed")

    async def test_a_person_s_other_name_or_a_wrong_place_is_not_found(self) -> None:
        self.assertEqual(
            await self.refused_action(self.mira, action="spelling-yes", alias=self.person),
            "not_found",
        )
        self.assertEqual(
            await self.refused_action(self.keep, action="spelling-yes", alias=self.person),
            "not_found",
        )

    async def test_only_a_dm_of_the_campaign_may_answer(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(
            await self.refused_action(self.keep, who=bob, action="spelling-no", alias=self.spaced),
            "not_allowed",
        )
        self.assertEqual(await self.status_of(self.spaced), PROPOSED)

    async def test_a_bad_alias_id_is_refused_before_the_door(self) -> None:
        self.assertEqual(
            await self.refused_action(self.keep, action="spelling-yes", alias="nope"),
            "not_found",
        )


class TheRoutes(Spellings):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        self.url = f"/me/campaigns/{self.cref}"
        self.clients = []
        users: list[Any] = [ALICE, BOB]
        for user in users:
            token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://web-api.internal.test",
                follow_redirects=False,
            )
            client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
            self.addAsyncCleanup(client.aclose)
            self.clients.append(client)

    async def test_the_list_and_an_answer_over_http(self) -> None:
        dm, player = self.clients
        got = await dm.get(f"{self.url}/place-spellings")
        self.assertEqual(got.status_code, 200)
        self.assertEqual([x["spelling"] for x in got.json()["spellings"]], ["Grey Water Keep"])
        sent = await dm.post(
            f"{self.url}/all-names/{self.keep}",
            json={"action": "spelling-yes", "alias": self.spaced},
            headers=HEADERS,
        )
        self.assertEqual(sent.status_code, 202)
        await self.apply_all()
        done = await dm.get(f"{self.url}/all-names/answers/{sent.json()['answer']}")
        self.assertEqual(done.json()["state"], "done")
        self.assertEqual((await dm.get(f"{self.url}/place-spellings")).json(), {"spellings": []})
        # a member who is not a DM of it is turned away, not told it is empty
        self.assertEqual((await player.get(f"{self.url}/place-spellings")).status_code, 403)
