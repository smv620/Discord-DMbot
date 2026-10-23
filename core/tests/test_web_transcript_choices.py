"""A DM puts a skipped stretch back after the session, or leaves it out again (#2567), run as the
website's own database role. Lines here are made up."""

from __future__ import annotations

from typing import Any

from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions, transcripts
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_transcripts import CAROL

START = 1_700_000_000
BASE_MS = (START + 86_400) * 1000


def lines() -> list[Line]:
    return [
        Line(BASE_MS + 1_000, ALICE.id, "the pass is closed", "the pass is closed", 4000),
        Line(BASE_MS + 6_000, ALICE.id, "valley lore one", "valley lore one", 5000, "off_topic"),
        Line(BASE_MS + 12_000, ALICE.id, "valley lore two", "valley lore two", 5000, "off_topic"),
        Line(BASE_MS + 20_000, BOB.id, "we climb", "we climb", 3000),
    ]


class Choices(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.store_t = TranscriptStore(self.db)
        self.sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START + 86_400)
        await self.store_t.add_lines(THURSDAY.id, self.sid, lines())
        await self.store_t.end_session(THURSDAY.id, self.sid, START + 90_000)
        self.bob = await self.session_of(BOB)
        self.carol = await self.session_of(CAROL)

    async def session_of(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def cleaned(self, who: Any) -> dict[str, Any]:
        return await transcripts.read_session(
            self.web, who, self.cref, 1, "cleaned", now=NOW, enforce_plans=False
        )

    async def choose(self, who: Any, *, put_back: bool, **span: int) -> dict[str, Any]:
        return await transcripts.choose(
            self.web,
            who,
            self.cref,
            1,
            str(ALICE.id),
            span.get("from_ms", BASE_MS + 6_000),
            span.get("to_ms", BASE_MS + 12_000),
            put_back=put_back,
            now=NOW,
            enforce_plans=False,
        )

    async def test_the_cleaned_page_says_where_a_skipped_stretch_is_without_its_words(self) -> None:
        page = await self.cleaned(self.carol)
        (skip,) = page["skips"]
        self.assertEqual(
            (skip["speaker"], skip["fromMs"], skip["toMs"], skip["seconds"], skip["lines"]),
            (str(ALICE.id), BASE_MS + 6_000, BASE_MS + 12_000, 10, 2),
        )
        self.assertIn("off-topic chat skipped", page["lines"][skip["line"]])
        self.assertNotIn("valley lore", str(page["skips"]))
        self.assertEqual(page["filter"], {"kept": 2, "leftOut": 2, "putBack": 0})

    async def test_anyone_who_may_read_gets_the_words_of_a_stretch(self) -> None:
        got = await transcripts.stretch(
            self.web, self.carol, self.cref, 1, str(ALICE.id), BASE_MS + 6_000, BASE_MS + 12_000,
            now=NOW, enforce_plans=False,
        )  # fmt: skip
        self.assertEqual([x["text"] for x in got["lines"]], ["valley lore one", "valley lore two"])
        self.assertEqual(got["lines"][0]["when"], "0:00:06")

    async def test_a_stretch_with_nothing_in_it_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await transcripts.stretch(
                self.web, self.carol, self.cref, 1, str(ALICE.id), BASE_MS + 100, BASE_MS + 200,
                now=NOW, enforce_plans=False,
            )  # fmt: skip
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_dm_puts_it_back_and_the_cleaned_text_has_the_words(self) -> None:
        self.assertEqual(await self.choose(self.session, put_back=True), {"changed": 2})
        page = await self.cleaned(self.carol)
        self.assertEqual(page["skips"], [])
        self.assertTrue(any("valley lore one" in x for x in page["lines"]))
        self.assertEqual(page["filter"], {"kept": 2, "leftOut": 0, "putBack": 2})
        (choice,) = page["putBacks"]
        self.assertEqual(
            (choice["speaker"], choice["lines"], choice["putBack"]), (str(ALICE.id), 2, True)
        )
        # the readers are told: both lines wait to be read
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_lines WHERE reread AND topic_by = %s",
                (ALICE.id,),
            )
            row = await cur.fetchone()
        self.assertEqual(row["n"], 2)

    async def test_put_back_lines_wait_in_the_queue_until_they_are_marked_read(self) -> None:
        await self.choose(self.session, put_back=True)
        waiting = await self.store_t.unread_put_back(THURSDAY.id, self.one.id)
        self.assertEqual([x.text for x in waiting], ["valley lore one", "valley lore two"])
        done = await self.store_t.mark_read(
            THURSDAY.id, self.one.id, ALICE.id, [x.started_ms for x in waiting]
        )
        self.assertEqual(done, 2)
        self.assertEqual(await self.store_t.unread_put_back(THURSDAY.id, self.one.id), [])

    async def test_leaving_it_out_again_works_and_the_choice_is_final(self) -> None:
        await self.choose(self.session, put_back=True)
        self.assertEqual(await self.choose(self.session, put_back=False), {"changed": 2})
        page = await self.cleaned(self.carol)
        self.assertEqual(len(page["skips"]), 1)
        self.assertEqual(page["filter"]["putBack"], 0)
        self.assertEqual(await self.choose(self.session, put_back=False), {"changed": 0})

    async def test_only_the_dms_of_the_campaign_may_choose(self) -> None:
        for who in (self.bob, self.carol):  # Bob DMs another campaign, Carol none
            with self.subTest(), self.assertRaises(RecapError) as caught:
                await self.choose(who, put_back=True)
            self.assertEqual(caught.exception.code, "not_a_dm")
        self.assertEqual(len((await self.cleaned(self.carol))["skips"]), 1)

    async def test_leaving_out_never_hides_a_plain_game_line(self) -> None:
        got = await self.choose(
            self.session, put_back=False, from_ms=BASE_MS + 1_000, to_ms=BASE_MS + 1_000
        )
        self.assertEqual(got, {"changed": 0})

    async def test_a_bad_span_is_refused(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await self.choose(self.session, put_back=True, from_ms=10, to_ms=5)
        self.assertEqual(caught.exception.code, "bad_choice")


class Settling(Base):
    """The end-of-session pass: the filter leans to keeping (#2567)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store_t = TranscriptStore(self.db)
        self.sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START + 86_400)

    async def topics(self) -> list[str]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT topic FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.sid,),
            )
            return [r["topic"] for r in await cur.fetchall()]

    async def test_a_short_stretch_between_game_lines_and_a_dm_aside_are_kept(self) -> None:
        await self.store_t.add_lines(
            THURSDAY.id,
            self.sid,
            [
                Line(BASE_MS + 1_000, BOB.id, "one", "one", 4000),
                Line(BASE_MS + 6_000, BOB.id, "chat", "chat", 3000, "off_topic"),
                Line(BASE_MS + 10_000, BOB.id, "two", "two", 4000),
                Line(BASE_MS + 100_000, ALICE.id, "lore", "lore", 5000, "off_topic"),
            ],
        )
        done = await self.store_t.settle_topics(THURSDAY.id, self.sid, {ALICE.id})
        self.assertEqual(await self.topics(), ["game"] * 4)
        self.assertEqual((done.turned_back, done.left_out, done.put_back), (2, 0, 0))

    async def test_a_clearly_unrelated_stretch_is_still_left_out_and_it_runs_twice(self) -> None:
        rows = [Line(BASE_MS + 1_000, BOB.id, "one", "one", 4000)]
        rows += [
            Line(BASE_MS + 6_000 + i * 8_000, BOB.id, "x", "x", 7000, "off_topic") for i in range(6)
        ]
        rows.append(Line(BASE_MS + 70_000, BOB.id, "two", "two", 3000))
        await self.store_t.add_lines(THURSDAY.id, self.sid, rows)
        for _ in range(2):
            done = await self.store_t.settle_topics(THURSDAY.id, self.sid, {ALICE.id})
            self.assertEqual((done.turned_back, done.left_out), (0, 6))
        self.assertEqual((await self.topics()).count("off_topic"), 6)
