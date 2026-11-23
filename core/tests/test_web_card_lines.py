"""A character's card lines on the website (#5050 slice d): the DM's Names page carries them, and
nobody else's does. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.card_summaries import CardSummaries, Mention
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import all_names, card_lines
from dmbot.web.recaps import _scoped
from tests.test_scenes import Base

START = 1_700_000_000


class OnTheNamesPage(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.rows = CardSummaries(self.db)
        self.cref = f"{self.g}.{self.one.id}"
        # (the sentence and the mentions are made by the background job)
        await self.rows.put_who(self.g, self.one.id, self.mira, "A harbour official.", "a" * 64)
        await self.rows.put_mentions(
            self.g, self.one.id, self.mira, Mention(START, 2), Mention(START, 9)
        )
        # (a session with lines gives "Session 1", so the first line is drawn)
        sid = await TranscriptStore(self.db).open_session(self.g, self.one.id, START)
        await TranscriptStore(self.db).add_lines(self.g, sid, [Line(START * 1000, 1, "Hi", "Hi")])
        await self.rows.put_scene(
            self.g, self.one.id, self.mira, which="first", scene="the party arrived"
        )

    async def page(self, who: Any, *, cards: bool = True) -> dict[str, dict[str, Any]]:
        got = await all_names.read_page(self.web, who, self.cref, cards=cards)
        return {n["id"]: n for n in got["names"]}

    async def test_a_dm_reads_the_three_lines(self) -> None:
        row = (await self.page(self.session))[self.mira]
        self.assertEqual(row["who"], "A harbour official.")
        self.assertNotIn("whoPending", row)
        self.assertTrue(row["firstMet"].startswith("First met: Session 1, "))
        self.assertIn("The party arrived.", row["firstMet"])
        self.assertTrue(row["lastMentioned"].startswith("Last mentioned: Session 1, "))

    async def test_with_the_switch_off_there_are_no_keys(self) -> None:
        row = (await self.page(self.session, cards=False))[self.mira]
        self.assertNotIn("who", row)

    async def test_a_character_the_job_has_not_reached_has_no_keys(self) -> None:
        others = [r for i, r in (await self.page(self.session)).items() if i != self.mira]
        self.assertTrue(others)
        self.assertTrue(all("who" not in r for r in others))

    async def test_the_lore_is_read_and_another_campaign_gives_nothing(self) -> None:
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.mira], upload_key="u1", label="Harbour notes",
            headings={self.mira: "The quay"},
        )  # fmt: skip
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_card_lines(%s, %s, %s) AS r", (self.g, self.one.id, [self.mira])
            )
            mine = (await cur.fetchone())["r"]["cards"][self.mira]["lore"]
            cur = await conn.execute(
                "SELECT dmbot_web_card_lines(%s, %s, %s) AS r", (self.g, other, [self.mira])
            )
            theirs = (await cur.fetchone())["r"]
        self.assertEqual(mine["label"], "Harbour notes")
        self.assertEqual(mine["heading"], "The quay")
        self.assertGreater(mine["at"], 0)
        self.assertIsNone(theirs)  # (not a DM of that campaign: the door gives nothing)

    async def test_a_row_with_only_a_sentence_is_a_summary_not_still_writing(self) -> None:
        got = card_lines.lines_of("Mira", {"who": "A harbour official."}, {})
        self.assertEqual(got, {"who": "A harbour official."})

    async def test_a_row_the_door_did_not_return_gets_no_key(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            rows = [{"id": self.someone, "name": "Someone"}]
            await card_lines.attach(conn, self.g, self.one.id, rows)
        self.assertEqual(rows, [{"id": self.someone, "name": "Someone"}])

    async def test_a_member_who_is_not_a_dm_is_given_nothing(self) -> None:
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await _scoped(conn, self.g)
            rows = [{"id": self.mira, "name": "Mira"}]
            await card_lines.attach(conn, self.g, self.one.id, rows)
        self.assertEqual(rows, [{"id": self.mira, "name": "Mira"}])

    async def test_another_campaign_is_never_read(self) -> None:
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            rows = [{"id": self.mira, "name": "Mira"}]
            await card_lines.attach(conn, self.g, other, rows)
        self.assertEqual(rows, [{"id": self.mira, "name": "Mira"}])


class TheWords(Base):
    def test_pending_and_never_mentioned_and_lore(self) -> None:
        pending = card_lines.lines_of("Shafina", None, {})
        self.assertEqual(pending, {"who": "DMbot is still writing this.", "whoPending": True})
        lore = {"label": "Icewind Dale notes", "heading": "Bryn Shander", "at": 5}
        card = {
            "who": "An innkeeper.", "madeAt": 5, "scannedAt": 6, "first": None, "last": None,
            "lore": lore,
        }  # fmt: skip
        got = card_lines.lines_of("Shafina", card, {})
        self.assertEqual(
            got["firstMet"], "First met: in your lore, *Icewind Dale notes*, under 'Bryn Shander'."
        )
        self.assertEqual(got["lastMentioned"], "Not mentioned at the table yet.")
        self.assertNotIn("whoPending", got)
