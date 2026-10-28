"""A character sheet on the website (#2816): a player sees and links their own characters' sheets,
forgets them, and a DM reads every sheet and asks for the campaign's to be read again. The website
only queues; the bot fetches and applies, so these tests check who may ask, what a link must look
like, the cooldowns and what the answer shows. Invented names and numbers throughout."""

from __future__ import annotations

import json
import unittest
from typing import Any, ClassVar

import httpx

from dmbot.web import sessions, sheets
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB, NOW
from tests.test_web_all_names import Listing
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

LINK = "https://www.dndbeyond.com/characters/12345"
# A server Bob has left: the campaign is not in his session, so it is "not found" for him.
ELSEWHERE = DiscordGuild(id=424242, name="Elsewhere", manage=False)
SHEET = {
    "v": 1,
    "source": "dndbeyond",
    "name": "Sorrel",
    "species": "Elf",
    "level": 3,
    "classes": [{"name": "Wizard", "level": 3, "subclass": None}],
    "max_hp": 18,
    "ac": 12,
}


class Sheets(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.player = await self.signed(BOB, THURSDAY)
        # Sorrel is Bob's character, with a linked sheet; Ivy is his too, with no sheet yet.
        self.sorrel = await self.add("Sorrel", "character")
        self.ivy = await self.add("Ivy", "character")
        for entity in (self.sorrel, self.ivy):
            await self.sql(
                "UPDATE memory_entities SET played_by = %s WHERE id = %s", BOB.id, entity
            )
        await self.sql(
            "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
            " fetched_at, player_id) VALUES (%s, %s, %s, %s, %s::jsonb, 'dndbeyond', %s, %s)",
            self.g,
            self.one.id,
            self.sorrel,
            LINK,
            json.dumps(SHEET),
            NOW,
            BOB.id,
        )

    async def sql_all(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(statement, args)
            return list(await cur.fetchall())

    async def read(self, who: Any = None) -> dict[str, Any]:
        return await sheets.read_sheets(self.web, who or self.player, self.cref)

    async def queue(self, entity: str, who: Any = None, **body: Any) -> str:
        return await sheets.queue_action(self.web, who or self.player, self.cref, entity, body)

    async def refuse_sheet(self, entity: str, who: Any = None, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await sheets.queue_action(self.web, who or self.player, self.cref, entity, body)
        return caught.exception.code

    async def bot_marks(self, answer: str, state: str) -> None:
        stage = "done" if state == "done" else "waiting"
        await self.sql(
            "UPDATE web_sheet_actions SET state = %s, stage = %s WHERE id = %s",
            state,
            stage,
            answer,
        )

    async def stage_of(self, answer: str) -> str:
        return str((await sheets.read_action(self.web, self.player, self.cref, answer))["stage"])


class TheReading(Sheets):
    async def test_a_player_sees_their_own_sheet_with_its_link_and_numbers(self) -> None:
        got = await self.read()
        self.assertFalse(got["isDm"])
        self.assertEqual([s["name"] for s in got["sheets"]], ["Sorrel"])
        sorrel = got["sheets"][0]
        self.assertEqual(
            (sorrel["mine"], sorrel["url"], sorrel["source"]), (True, LINK, "dndbeyond")
        )
        self.assertEqual(sorrel["summary"]["level"], 3)
        self.assertEqual(sorrel["summary"]["classes"][0]["name"], "Wizard")

    async def test_the_dm_sees_every_sheet_and_none_is_theirs(self) -> None:
        got = await self.read(self.session)
        self.assertTrue(got["isDm"])
        self.assertEqual([s["mine"] for s in got["sheets"]], [False])

    async def test_someone_who_is_neither_dm_nor_player_sees_nothing_at_all(self) -> None:
        # Bob plays no character in this campaign and isn't its DM: nothing to see.
        await self.sql(
            "UPDATE memory_entities SET played_by = NULL WHERE id = ANY(%s)",
            [[self.sorrel, self.ivy]],
        )
        with self.assertRaises(RecapError) as caught:
            await self.read()
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_sheet_that_belonged_to_a_previous_player_is_not_shown(self) -> None:
        await self.sql(
            "UPDATE memory_entities SET played_by = %s WHERE id = %s", ALICE.id, self.sorrel
        )
        got = await self.read(self.session)
        self.assertEqual(got["sheets"], [])


class TheQueue(Sheets):
    async def test_a_player_links_a_sheet_and_the_answer_is_waiting_then_done(self) -> None:
        answer = await self.queue(self.ivy, action="link", url=LINK)
        self.assertEqual(await self.stage_of(answer), "waiting")
        await self.bot_marks(answer, "done")
        self.assertEqual(await self.stage_of(answer), "done")

    async def test_a_link_of_any_other_shape_is_refused_before_it_is_queued(self) -> None:
        for bad in (
            "https://example.com/characters/1",
            "https://www.dndbeyond.com/characters/x",
            "http://www.dndbeyond.com/characters/1",
            "https://www.dndbeyond.com/characters/1234567890123",
        ):
            self.assertEqual(
                await self.refuse_sheet(self.ivy, action="link", url=bad), "bad_link", bad
            )

    async def test_only_the_player_may_link_or_forget_a_character(self) -> None:
        self.assertEqual(
            await self.refuse_sheet(self.ivy, self.session, action="link", url=LINK), "not_allowed"
        )
        self.assertEqual(
            await self.refuse_sheet(self.sorrel, self.session, action="forget"), "not_allowed"
        )

    async def test_forget_needs_a_sheet_to_forget(self) -> None:
        self.assertEqual(await self.refuse_sheet(self.ivy, action="forget"), "not_found")
        self.assertTrue(await self.queue(self.sorrel, action="forget"))

    async def test_a_second_press_gets_the_open_choice_back(self) -> None:
        first = await self.queue(self.sorrel, action="forget")
        self.assertEqual(await self.queue(self.sorrel, action="forget"), first)

    async def test_linking_twice_inside_twenty_seconds_is_too_soon(self) -> None:
        await self.queue(self.ivy, action="link", url=LINK)
        self.assertEqual(await self.refuse_sheet(self.sorrel, action="link", url=LINK), "too_soon")

    async def test_read_again_is_the_dms_alone_and_once_a_minute(self) -> None:
        self.assertEqual(await self.refuse_sheet("-", action="read_again"), "not_allowed")
        first = await self.queue("-", self.session, action="read_again")
        self.assertEqual(await self.queue("-", self.session, action="read_again"), first)
        await self.bot_marks(first, "done")
        self.assertEqual(
            await self.refuse_sheet("-", self.session, action="read_again"), "read_too_soon"
        )

    # ---- typed and document (#2818) ------------------------------------------------------------

    TYPED: ClassVar[dict[str, Any]] = {
        "className": "Wizard",
        "level": 3,
        "species": "Elf",
        "names": "Misty Step, Shield",
    }
    DOC = "https://docs.example.org/sheet.pdf"

    async def door(self, act: str, lnk: str, who: Any = None, entity: str | None = None) -> Any:
        """The door itself, with what Python would have sent, so a caller that skips the Python
        checks still meets the door's."""
        session = who or self.player
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_sheet_action_add(%s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, entity or self.ivy, act, lnk),
            )
            row = await cur.fetchone()
        return row["r"] if row else None

    async def test_a_player_types_in_their_own_character_and_it_waits_for_the_bot(self) -> None:
        answer = await self.queue(self.ivy, action="typed", **self.TYPED)
        self.assertEqual(await self.stage_of(answer), "waiting")
        (row,) = await self.sql_all(
            "SELECT action, url, payload FROM web_sheet_actions WHERE id = %s", answer
        )
        self.assertEqual((row["action"], row["url"]), ("typed", ""))
        self.assertEqual(json.loads(row["payload"]), self.TYPED)

    async def test_a_typed_sheet_the_door_does_not_like_is_refused_even_past_python(self) -> None:
        good = json.dumps(self.TYPED)
        self.assertEqual((await self.door("typed", good))["status"], "ok")
        # a long list with many line breaks and quotes is legitimate even when its JSON text grows
        crowded = json.dumps({**self.TYPED, "names": '"a"\n' * 375})
        self.assertGreater(len(crowded), 2000)
        await self.sql("UPDATE web_sheet_actions SET created_at = created_at - 30")  # past the 20 s
        self.assertEqual((await self.door("typed", crowded))["status"], "ok")
        for bad in (
            "not json",
            "[]",
            "5",
            "null",
            '"x"',
            json.dumps({**self.TYPED, "level": None}),
            json.dumps({**self.TYPED, "className": None}),
            json.dumps({**self.TYPED, "species": True}),
            json.dumps({**self.TYPED, "names": "x" * 4001}),
            json.dumps({**self.TYPED, "level": 21}),
            json.dumps({**self.TYPED, "level": 0}),
            json.dumps({**self.TYPED, "level": "3"}),
            json.dumps({**self.TYPED, "className": " "}),
            json.dumps({**self.TYPED, "className": "x" * 61}),
            json.dumps({**self.TYPED, "species": "x" * 61}),
            json.dumps({**self.TYPED, "names": "x" * 1501}),
            json.dumps({**self.TYPED, "extra": 1}),
            json.dumps({k: v for k, v in self.TYPED.items() if k != "species"}),
        ):
            self.assertEqual((await self.door("typed", bad))["status"], "bad_typed", bad)

    async def test_typed_input_python_refuses_never_reaches_the_door(self) -> None:
        for bad in (
            {"className": "", "level": 3},
            {"className": "Wizard", "level": 21},
            {"className": "Wizard", "level": True},
            {"className": "Wizard", "level": 3, "names": "x" * 1501},
            {"className": 5, "level": 3},
        ):
            self.assertEqual(
                await self.refuse_sheet(self.ivy, action="typed", **bad), "bad_typed", bad
            )

    async def test_only_the_player_may_type_in_their_character(self) -> None:
        self.assertEqual(
            await self.refuse_sheet(self.ivy, self.session, action="typed", **self.TYPED),
            "not_allowed",
        )

    async def test_a_player_gives_a_document_link_and_it_waits_for_the_bot(self) -> None:
        answer = await self.queue(self.ivy, action="document", url=self.DOC)
        self.assertEqual(await self.stage_of(answer), "waiting")
        (row,) = await self.sql_all(
            "SELECT action, url, payload FROM web_sheet_actions WHERE id = %s", answer
        )
        self.assertEqual((row["action"], row["url"], row["payload"]), ("document", self.DOC, ""))

    async def test_a_document_link_of_the_wrong_shape_is_refused(self) -> None:
        for bad in (
            "",
            "ftp://example.org/a",
            "http://example.org/a",  # the bot's fetch is https only
            "javascript:alert(1)",
            "https://",
            "https://example.org/a b",
            "https://example.org/a\nb",
            "https://example.org/<x>",
            "https://example.org/" + "a" * 400,
        ):
            self.assertEqual(
                await self.refuse_sheet(self.ivy, action="document", url=bad), "bad_document", bad
            )
            self.assertEqual((await self.door("document", bad))["status"], "bad_document", bad)
        self.assertEqual(
            (await self.door("document", "https://example.org/" + "a" * 380))["status"], "ok"
        )

    async def test_the_link_of_a_document_is_never_given_back(self) -> None:
        answer = await self.queue(self.ivy, action="document", url=self.DOC)
        shown = await sheets.read_action(self.web, self.player, self.cref, answer)
        self.assertNotIn(self.DOC, json.dumps(shown))
        self.assertNotIn(self.DOC, json.dumps(await self.read()))

    async def test_a_document_read_costs_a_minute_and_a_typed_sheet_twenty_seconds(self) -> None:
        await self.queue(self.ivy, action="document", url=self.DOC)
        self.assertEqual(
            await self.refuse_sheet(self.sorrel, action="document", url=self.DOC + "2"), "too_soon"
        )
        # a typed sheet is not held up by a document, and a link is held up by it only for the
        # twenty seconds of the link's own count, which a document does not start
        self.assertTrue(await self.queue(self.ivy, action="typed", **self.TYPED))
        # thirty seconds on: a typed sheet or a link may go again, a document may not
        await self.sql("UPDATE web_sheet_actions SET created_at = created_at - 30")
        self.assertTrue(await self.queue(self.sorrel, action="typed", **{**self.TYPED, "level": 4}))
        self.assertEqual(
            await self.refuse_sheet(self.sorrel, action="document", url=self.DOC + "3"), "too_soon"
        )
        # eighty more seconds: the document's minute is over
        await self.sql("UPDATE web_sheet_actions SET created_at = created_at - 80")
        self.assertTrue(await self.queue(self.sorrel, action="document", url=self.DOC + "4"))

    async def test_a_link_and_a_typed_sheet_share_their_twenty_seconds(self) -> None:
        await self.queue(self.ivy, action="typed", **self.TYPED)
        self.assertEqual(await self.refuse_sheet(self.sorrel, action="link", url=LINK), "too_soon")

    async def test_the_same_typed_sheet_pressed_twice_is_one_choice(self) -> None:
        first = await self.queue(self.ivy, action="typed", **self.TYPED)
        self.assertEqual(await self.queue(self.ivy, action="typed", **self.TYPED), first)

    async def test_an_unknown_action_is_refused(self) -> None:
        self.assertEqual(await self.refuse_sheet(self.ivy, action="rename"), "bad_action")

    async def test_the_answer_is_read_back_only_by_the_person_who_made_it(self) -> None:
        answer = await self.queue(self.sorrel, action="forget")
        with self.assertRaises(RecapError) as caught:
            await sheets.read_action(self.web, self.session, self.cref, answer)
        self.assertEqual(caught.exception.code, "not_found")


class TheRoutes(Sheets):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(sheets_on=True),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = await self.client_for(BOB)

    async def client_for(
        self, user: Any, guild: Any = THURSDAY, app: Any = None
    ) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, user, [guild], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app or self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
            headers={"X-DMbot-Request": "1"},  # (the cross-site check every write passes)
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_the_player_reads_and_queues_over_http(self) -> None:
        base = f"/me/campaigns/{self.cref}/sheets"
        got = await self.client.get(base)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["sheets"][0]["url"], LINK)
        queued = await self.client.post(f"{base}/{self.ivy}", json={"action": "link", "url": LINK})
        self.assertEqual(queued.status_code, 202)
        answer = queued.json()["answer"]
        read = await self.client.get(f"{base}/answers/{answer}")
        self.assertEqual(read.json()["stage"], "waiting")

    async def test_a_bad_link_is_400_and_a_campaign_outside_the_session_is_404(self) -> None:
        base = f"/me/campaigns/{self.cref}/sheets"
        bad = await self.client.post(f"{base}/{self.ivy}", json={"action": "link", "url": "nope"})
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_link"}))
        elsewhere = await self.client_for(BOB, ELSEWHERE)
        self.assertEqual((await elsewhere.get(base)).status_code, 404)

    async def test_everything_is_404_while_the_switch_is_off(self) -> None:
        off = create_app(
            settings(sheets_on=False),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        client = await self.client_for(BOB, app=off)
        self.assertEqual((await client.get(f"/me/campaigns/{self.cref}/sheets")).status_code, 404)


class TheLinkShape(unittest.TestCase):
    """Any link Discord accepts becomes the one address the door and the bot keep (no database)."""

    def test_every_link_discord_accepts_is_kept_as_the_one_address(self) -> None:
        for given in (
            LINK,
            LINK + "/sheet",
            "https://dndbeyond.com/characters/12345",
            LINK + "?tab=spells",
            "<" + LINK + ">",
        ):
            kept = sheets._choice({"action": "link", "url": given})
            self.assertEqual(kept, ("link", LINK), given)

    def test_the_typed_form_and_a_document_link_are_kept_as_the_door_wants_them(self) -> None:
        action, payload = sheets._choice(
            {
                "action": "typed",
                "className": " Wizard ",
                "level": 3,
                "species": " Elf",
                "names": "a",
            }
        )
        self.assertEqual(action, "typed")
        self.assertEqual(
            json.loads(payload), {"className": "Wizard", "level": 3, "species": "Elf", "names": "a"}
        )
        self.assertEqual(
            sheets._choice({"action": "document", "url": "  https://example.org/a.pdf "}),
            ("document", "https://example.org/a.pdf"),
        )
