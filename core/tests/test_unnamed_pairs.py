"""Two people the story never named, described the same way in different sessions (#3447): DMbot
offers "Is this the same person?" once, never joins them itself, remembers a no, and a yes is one
change with one Undo. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import same_person
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest


def _row(i: str, what: str, created: int, anchor: str | None = None) -> dict[str, object]:
    return {
        "id": i, "type": "character", "status": "proposed", "unnamed": True,
        "unnamed_what": what, "unnamed_role": None, "unnamed_anchor": anchor,
        "created_at": created,
    }  # fmt: skip


class TheOffers(MemoryTest):
    async def two_women(self) -> tuple[str, str]:
        first = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        second = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        return first, second

    async def offers(self) -> list[same_person.Offer]:
        return await self.memory.same_person_offers(GUILD_A, self.c)

    async def test_one_woman_gives_no_offer_and_two_give_one(self) -> None:
        first = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        self.assertEqual(await self.offers(), [])
        second = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        (offer,) = await self.offers()
        self.assertTrue(offer.both_unnamed)
        self.assertEqual({offer.unnamed_id, offer.named_id}, {first, second})
        self.assertEqual(offer.reasons, (same_person.SAME_WORDS,))

    async def test_a_different_description_is_not_offered(self) -> None:
        await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")
        await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure")
        self.assertEqual(await self.offers(), [])

    async def test_not_the_same_is_remembered(self) -> None:
        first, second = await self.two_women()
        await self.memory.not_the_same_person(GUILD_A, self.c, first, second, user_id=7)
        self.assertEqual(await self.offers(), [])

    async def test_yes_joins_them_into_the_older_and_undo_restores_both(self) -> None:
        first, second = await self.two_women()
        (offer,) = await self.offers()
        done = await self.memory.join_same_person(GUILD_A, self.c, offer.unnamed_id, offer.named_id)
        assert done.batch is not None
        live = [e.id for e, _ in await self.memory.unnamed(GUILD_A, self.c)]
        self.assertEqual(live, [offer.unnamed_id])
        self.assertEqual(await self.offers(), [])
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(
            sorted(e.id for e, _ in await self.memory.unnamed(GUILD_A, self.c)),
            sorted([first, second]),
        )

    async def test_a_stray_pair_is_refused(self) -> None:
        first, _ = await self.two_women()
        with self.assertRaises(MemoryRuleError):
            await self.memory.not_the_same_person(GUILD_A, self.c, first, "0" * 32, user_id=7)

    async def test_the_tie_list_marks_a_secret_tie_for_the_dm(self) -> None:
        first, _ = await self.two_women()
        port = await self.add("Termalaine", type="place", status="confirmed")
        await self.relate(first, "located_in", port, status="confirmed")
        ties = await self.memory.tag_ties(GUILD_A, self.c, [first])
        self.assertEqual(
            [(t.predicate, t.other_name, t.secret) for t in ties[first]],
            [("located_in", "Termalaine", False)],
        )


class ThePairRule(unittest.TestCase):
    """Pure: the finder, with rows."""

    def test_only_unanchored_people_with_the_same_words_pair_with_the_oldest(self) -> None:
        rows = [_row("c", "the woman", 3), _row("a", "the woman", 1), _row("b", "the woman", 2),
                _row("d", "the sister", 4, anchor="a"), _row("e", "the monk", 5)]  # fmt: skip
        found = same_person.unnamed_pairs(rows)
        self.assertEqual([(o.unnamed_id, o.named_id) for o in found], [("a", "b"), ("a", "c")])

    def test_a_pair_the_dm_declined_is_left_out_in_either_order(self) -> None:
        rows = [_row("a", "the woman", 1), _row("b", "the woman", 2)]
        self.assertEqual(same_person.unnamed_pairs(rows, {("b", "a")}), [])
        self.assertEqual(same_person.unnamed_pairs(rows, {("a", "b")}), [])
