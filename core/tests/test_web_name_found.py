"""What made a waiting name a name, on the website's name card (#5144, migration 0616): how it was
heard, its other spellings, DMbot's guess and the one line that made it a name (never for a speaker
who no longer agrees), in Discord's words and order. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory import name_words
from dmbot.memory.name_found import NameFound
from dmbot.transcript.models import Line, line_ref
from dmbot.transcript.store import TranscriptStore
from tests.test_web_api import ALICE
from tests.test_web_name_checks import AT, GUILD, WithChecks


class TheFoundCard(WithChecks):
    async def line_for(self, speaker: int, text: str) -> str:
        sid = await TranscriptStore(self.db).open_session(GUILD, self.one.id, AT)
        line = Line(4000, speaker, text, text)
        await TranscriptStore(self.db).add_lines(GUILD, sid, [line])
        return line_ref(sid, line.started_ms, speaker)

    async def agree(self, *users: int, leave: tuple[int, ...] = ()) -> None:
        async with self.db.guild(GUILD) as conn:
            for user in users:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, 0)"
                    " ON CONFLICT DO NOTHING",
                    (GUILD, user),
                )
            for user in leave:
                await conn.execute(
                    "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (GUILD, user)
                )

    async def keep(self, entity: str, how: str, ref: str | None, *, times: int, typed: int = 0):
        await NameFound(self.db).keep(
            GUILD, self.one.id, entity, how, ref, times=times, typed=typed
        )

    async def test_a_found_name_carries_how_it_was_heard_and_the_line_that_made_it(self) -> None:
        harrow = await self.suggest("Harrow")
        await self.memory.add_alias(
            GUILD, self.one.id, harrow, "Haro", kind="misheard", source="scan"
        )
        ref = await self.line_for(ALICE.id, "We rode to Harrow at dawn")
        await self.keep(harrow, "introduced", ref, times=5)
        await self.agree(ALICE.id)
        (card,) = (await self.listed())["names"]
        found = card["found"]
        self.assertEqual((found["how"], found["times"], found["typed"]), ("introduced", 5, 0))
        self.assertEqual(found["also"], ["Haro"])
        self.assertEqual(found["heard"], "Heard 5 times, sometimes as Haro.")
        self.assertEqual(found["heard"], name_words.heard_line(5, 0, ["Haro"]))
        self.assertIsNone(found["guess"])  # (a concept has no kind to guess)
        self.assertEqual(found["line"]["text"], "We rode to Harrow at dawn")
        self.assertIn("speaker", found["line"])

    async def test_a_speaker_who_no_longer_agrees_leaves_the_line_out_and_nothing_in_its_place(
        self,
    ) -> None:
        harrow = await self.suggest("Harrow")
        ref = await self.line_for(ALICE.id, "We rode to Harrow at dawn")
        await self.keep(harrow, "typed", ref, times=2, typed=1)
        await self.agree(ALICE.id, leave=(ALICE.id,))
        (card,) = (await self.listed())["names"]
        self.assertIsNone(card["found"]["line"])
        self.assertEqual(card["found"]["heard"], "Typed once in chat, and heard once.")
        self.assertNotIn("We rode", repr(card))

    async def test_a_name_with_no_found_row_is_todays_card_and_the_sure_ones_come_first(
        self,
    ) -> None:
        plain = await self.suggest("Aaron")
        scanned = await self.suggest("Bree")
        sure = await self.suggest("Cael")
        await self.keep(scanned, "scan", None, times=9)
        await self.keep(sure, "introduced", None, times=1)
        names = (await self.listed())["names"]
        self.assertEqual([n["name"] for n in names], ["Cael", "Bree", "Aaron"])
        self.assertNotIn("found", names[2])
        self.assertEqual(names[2]["id"], plain)

    async def test_the_guess_is_the_kind_it_was_given_when_it_was_read(self) -> None:
        place = await self.memory.add_entity(
            GUILD, self.one.id, type="place", name="Thornwick", source="scan", description="x"
        )
        await self.answers.link(GUILD, self.one.id, AT, [place.value.id])
        await self.keep(place.value.id, "place", None, times=3)
        (card,) = (await self.listed())["names"]
        self.assertEqual(card["found"]["guess"], "place")


class TheWords(unittest.TestCase):
    """`name_words` is what both cards share: the same words and order."""

    def test_the_heard_line_is_discords(self) -> None:
        also = ["Rothgar", "hroth gar", "Rothgard", "Rotgar"]
        self.assertEqual(
            name_words.heard_line(5, 0, also),
            "Heard 5 times, sometimes as Rothgar, hroth gar or Rothgard and 1 more.",
        )
        self.assertEqual(name_words.heard_line(1, 0, []), "Heard once.")
        self.assertEqual(
            name_words.heard_line(3, 1, ["Rothgar"], mark=lambda a: f"*{a}*"),
            "Typed once in chat, and heard 2 times, sometimes as *Rothgar*.",
        )
        self.assertEqual(
            name_words.heard_line(2, 0, [], written=True), "Found 2 times in your text."
        )
