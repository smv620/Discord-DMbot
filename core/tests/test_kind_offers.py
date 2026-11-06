"""Offering the table's repeated words as a kind of connection (#3033). Real Postgres; invented
names and words."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import kind_offers
from dmbot.memory.kind_offers_backup import KindOffersSection
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

WORDS = "sworn on the ashes of"


class Base(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.people = [
            await self.add(n, type="character") for n in ("Mira", "Tavin", "Orsk", "Ilsa")
        ]
        self.hall = await self.add("Greyhall", type="faction")

    async def tie(self, a: str, b: str, words: str = WORDS) -> str:
        made = await self.relate(a, "associated_with", b, detail=words, status="confirmed")
        return str(made.value[0].id)

    async def three(self, words: str = WORDS) -> list[str]:
        return [await self.tie(p, self.hall, words) for p in self.people[:3]]

    async def offer(self) -> Any:
        return await self.memory.kind_offer(GUILD_A, self.c)


class TheCount(Base):
    async def test_three_matching_connections_make_one_offer(self) -> None:
        await self.three()
        offer = await self.offer()
        self.assertEqual((offer.words_key, offer.count), (WORDS, 3))
        self.assertEqual(offer.subject_types, ("character",))
        self.assertEqual(offer.object_types, ("faction",))

    async def test_case_and_spacing_do_not_split_the_words(self) -> None:
        await self.tie(self.people[0], self.hall, WORDS)
        await self.tie(self.people[1], self.hall, WORDS.upper())
        await self.tie(self.people[2], self.hall, "  sworn  on the ashes of ")
        self.assertEqual((await self.offer()).count, 3)

    async def test_two_make_none(self) -> None:
        await self.tie(self.people[0], self.hall)
        await self.tie(self.people[1], self.hall)
        self.assertIsNone(await self.offer())

    async def test_words_a_kind_already_covers_make_none(self) -> None:
        await self.three("is a member of")  # (the member kind's own words)
        self.assertIsNone(await self.offer())

    async def test_connections_that_share_no_end_kind_make_none(self) -> None:
        keep = await self.add("Brindle", type="place")
        await self.tie(self.people[0], self.hall)
        await self.tie(self.people[1], keep)
        await self.tie(self.hall, self.people[2])
        self.assertIsNone(await self.offer())


class TheAnswers(Base):
    async def test_never_holds(self) -> None:
        await self.three()
        await self.memory.answer_kind_offer(GUILD_A, self.c, WORDS, kind_offers.NEVER, seen=3)
        self.assertIsNone(await self.offer())
        await self.tie(self.people[3], self.hall)  # even a fourth
        self.assertIsNone(await self.offer())

    async def test_not_now_holds_until_three_more(self) -> None:
        await self.three()
        await self.memory.answer_kind_offer(GUILD_A, self.c, WORDS, kind_offers.LATER, seen=3)
        self.assertIsNone(await self.offer())
        for i in range(3):
            other = await self.add(f"Extra {i}", type="character")
            self.assertIsNone(await self.offer(), i)
            await self.tie(other, self.hall)
        self.assertEqual((await self.offer()).count, 6)

    async def test_clearing_a_never_lets_the_words_be_offered_again(self) -> None:
        await self.three()
        await self.memory.answer_kind_offer(GUILD_A, self.c, WORDS, kind_offers.NEVER, seen=3)
        self.assertTrue(await self.memory.clear_kind_offer(GUILD_A, self.c, WORDS))
        self.assertIsNotNone(await self.offer())

    async def test_an_answer_whose_words_are_gone_is_deleted_by_the_sweep(self) -> None:
        ids = await self.three()
        await self.memory.answer_kind_offer(GUILD_A, self.c, WORDS, kind_offers.NEVER, seen=3)
        for rid in ids:
            await self.memory.update_relation(GUILD_A, self.c, rid, source="dm", status="rejected")
        await self.offer()  # (the sweep)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_kind_offers")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class TheYes(Base):
    async def test_yes_makes_the_kind_and_moves_every_connection_with_one_undo(self) -> None:
        await self.three()
        made = await self.memory.make_kind_of_words(GUILD_A, self.c, WORDS, source="dm")
        self.assertEqual(made.value.key, "sworn_on_the_ashes_of")
        self.assertFalse(made.value.core)
        rels = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        live = [r for r in rels if r.status == "confirmed"]
        self.assertEqual({r.predicate for r in live}, {"sworn_on_the_ashes_of"})
        self.assertEqual(len(live), 3)
        self.assertIsNone(await self.offer())  # (nothing left to offer)
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        back = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.status == "confirmed"
        ]
        self.assertEqual({r.predicate for r in back}, {"associated_with"})
        self.assertEqual(len(back), 3)

    async def test_only_the_dm_can_say_yes(self) -> None:
        await self.three()
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_kind_of_words(GUILD_A, self.c, WORDS, source="extractor")

    async def test_words_that_changed_since_the_question_are_refused(self) -> None:
        ids = await self.three()
        await self.memory.update_relation(GUILD_A, self.c, ids[0], source="dm", status="rejected")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_kind_of_words(GUILD_A, self.c, WORDS, source="dm")


class TheGuards(Base):
    async def test_words_that_could_never_be_a_kind_are_not_offered(self) -> None:
        await self.three("忠誠")  # (no letters to make a key from)
        self.assertIsNone(await self.offer())
        await self.three("3 oaths")  # (a key can't start with a digit)
        self.assertIsNone(await self.offer())

    async def test_words_with_marks_are_not_offered_because_a_kind_would_read_them_differently(
        self,
    ) -> None:
        await self.three("sworn on Veil's ashes")
        self.assertIsNone(await self.offer())

    async def test_an_unmakeable_offer_does_not_block_the_next_one(self) -> None:
        await self.three("忠誠")
        await self.three(WORDS)
        self.assertEqual((await self.offer()).words_key, WORDS)

    async def test_the_same_connection_written_twice_moves_cleanly(self) -> None:
        a, b = self.people[0], self.hall
        await self.tie(a, b, WORDS)
        await self.tie(a, b, WORDS.upper())  # (the same connection written twice)
        await self.tie(self.people[1], b, WORDS)
        await self.tie(self.people[2], b, WORDS)
        made = await self.memory.make_kind_of_words(GUILD_A, self.c, WORDS, source="dm")
        live = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.status == "confirmed"
        ]
        self.assertEqual({r.predicate for r in live}, {made.value.key})

    async def test_a_secret_connection_stays_secret_after_the_move(self) -> None:
        await self.three()
        one = await self.relate(
            self.people[3], "associated_with", self.hall, detail=WORDS, status="confirmed",
            secret=True,
        )  # fmt: skip
        self.assertTrue(one.value[0].secret)
        await self.memory.make_kind_of_words(GUILD_A, self.c, WORDS, source="dm")
        moved = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.status == "confirmed" and r.subject_id == self.people[3]
        ]
        self.assertEqual([r.secret for r in moved], [True])


class TheBackup(Base):
    async def test_a_backup_round_trips_the_answers(self) -> None:
        await self.three()
        await self.memory.answer_kind_offer(GUILD_A, self.c, WORDS, kind_offers.NEVER, seen=3)
        section = KindOffersSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
        self.assertIsNotNone(await self.offer())  # (the answer is gone)
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, section.check(rows))
        self.assertIsNone(await self.offer())  # (and back)

    def test_a_damaged_section_is_refused_whole(self) -> None:
        good = {"words_key": WORDS, "state": "never", "seen": 3, "created_at": 5}
        KindOffersSection().check([good])
        for bad in (
            {**good, "state": "yes"}, {**good, "seen": True}, {**good, "words_key": ""},
            {**good, "created_at": 0}, {**good, "extra": 1},
        ):  # fmt: skip
            with self.assertRaises(CampaignError):
                KindOffersSection().check([bad])
        with self.assertRaises(CampaignError):
            KindOffersSection().check([good, good])  # one answer for each set of words
