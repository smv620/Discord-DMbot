"""What the background job keeps for a character's card (#5050): the sentence and the first and
last mention, per character and per campaign. Invented names. Needs Postgres."""

from __future__ import annotations

from dmbot.campaigns.store import CampaignStore
from dmbot.memory.card_summaries import CardSummaries, Mention
from dmbot.memory.models import CONFIRMED, NewName
from tests.test_memory_names import DM, GUILD, NamesTest

KEY = "a" * 64


class TheRows(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.rows = CardSummaries(self.db, clock=lambda: 1_700_000_000)
        made = await self.memory.add_names(
            GUILD,
            self.campaign.id,
            [NewName("Shafina", "character", CONFIRMED), NewName("Rook", "character", CONFIRMED)],
            source="dm",
        )
        self.shafina, self.rook = (str(x) for x in made.value)

    async def test_the_sentence_is_kept_with_the_hash_of_its_facts(self) -> None:
        self.assertIsNone(await self.rows.get(GUILD, self.campaign.id, self.shafina))
        kept = await self.rows.put_who(
            GUILD, self.campaign.id, self.shafina, "A halfling innkeeper.\n", KEY
        )
        self.assertTrue(kept)
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None
        self.assertEqual(
            (got.who, got.facts_key, got.made_at), ("A halfling innkeeper.", KEY, 1_700_000_000)
        )
        self.assertIsNone(got.first)
        self.assertIsNone(got.last)

    async def test_the_mentions_keep_the_sentence_and_the_sentence_keeps_the_mentions(self) -> None:
        first, last = (
            Mention(1_699_000_000, 4, "the party reached the inn"),
            Mention(1_699_900_000, 21),
        )
        await self.rows.put_who(GUILD, self.campaign.id, self.shafina, "A halfling innkeeper.", KEY)
        await self.rows.put_mentions(GUILD, self.campaign.id, self.shafina, first, last)
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None
        self.assertEqual((got.who, got.first, got.last), ("A halfling innkeeper.", first, last))
        self.assertEqual(got.scanned_at, 1_700_000_000)
        await self.rows.put_who(
            GUILD, self.campaign.id, self.shafina, "A halfling innkeeper, Bryn Shander.", "b" * 64
        )
        again = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert again is not None
        self.assertEqual((again.first, again.last), (first, last))  # (the sentence left them)

    async def test_a_scene_written_later_lands_on_the_mention_it_describes(self) -> None:
        await self.rows.put_mentions(
            GUILD, self.campaign.id, self.shafina, Mention(1_699_000_000, 4), None
        )
        self.assertTrue(
            await self.rows.put_scene(
                GUILD, self.campaign.id, self.shafina, which="first", scene="when the party met her"
            )
        )
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None and got.first is not None
        self.assertEqual((got.first.line, got.first.scene), (4, "when the party met her"))
        # (no last mention: there is nothing for a scene to describe)
        self.assertFalse(
            await self.rows.put_scene(
                GUILD, self.campaign.id, self.shafina, which="last", scene="never"
            )
        )
        with self.assertRaises(ValueError):
            await self.rows.put_scene(
                GUILD, self.campaign.id, self.shafina, which="both", scene="x"
            )

    async def test_a_character_never_mentioned_keeps_a_row_that_says_so(self) -> None:
        await self.rows.put_mentions(GUILD, self.campaign.id, self.rook, None, None)
        got = await self.rows.get(GUILD, self.campaign.id, self.rook)
        assert got is not None
        self.assertEqual((got.first, got.last, got.scanned_at), (None, None, 1_700_000_000))

    async def test_a_character_that_is_gone_has_no_row_made(self) -> None:
        self.assertFalse(await self.rows.put_who(GUILD, self.campaign.id, "f" * 32, "x.", KEY))
        self.assertFalse(
            await self.rows.put_mentions(GUILD, self.campaign.id, "f" * 32, Mention(1, 1), None)
        )
        self.assertEqual(await self.rows.get_many(GUILD, self.campaign.id, []), {})

    async def test_another_campaigns_rows_are_never_read(self) -> None:
        other = await CampaignStore(self.db).create(GUILD, "Two", DM)
        await self.rows.put_who(GUILD, self.campaign.id, self.shafina, "A halfling.", KEY)
        self.assertEqual(await self.rows.get_many(GUILD, other.id, [self.shafina]), {})

    async def test_a_removed_character_takes_its_row_with_it(self) -> None:
        await self.rows.put_who(GUILD, self.campaign.id, self.rook, "A thief.", KEY)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM memory_entities WHERE id = %s", (self.rook,))
        self.assertIsNone(await self.rows.get(GUILD, self.campaign.id, self.rook))
