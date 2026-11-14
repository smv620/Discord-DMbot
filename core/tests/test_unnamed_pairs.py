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


class TheJoinRules(MemoryTest):
    async def two_women(self) -> tuple[str, str]:
        first = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        second = (await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")).entity.id
        return first, second

    async def test_something_described_through_either_is_refused(self) -> None:
        first, second = await self.two_women()
        await self.memory.add_unnamed(GUILD_A, self.c, what="dog", anchor_id=second)
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_same_person(GUILD_A, self.c, first, second)

    async def test_what_the_newer_had_moves_onto_the_older_and_undo_puts_it_back(self) -> None:
        first, second = await self.two_women()
        port = await self.add("Termalaine", type="place", status="confirmed")
        tie = await self.relate(second, "located_in", port, status="confirmed")
        done = await self.memory.join_same_person(GUILD_A, self.c, first, second)
        ties = await self.memory.relations(GUILD_A, self.c, entity_id=first)
        self.assertEqual([r.object_id for r in ties], [port])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        back = await self.memory.relations(GUILD_A, self.c, entity_id=second)
        self.assertEqual([r.id for r in back], [tie.value[0].id])

    async def test_a_joined_away_person_cannot_be_joined_again(self) -> None:
        first, second = await self.two_women()
        await self.memory.join_same_person(GUILD_A, self.c, first, second)
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_same_person(GUILD_A, self.c, first, second)

    async def test_two_campaigns_each_with_a_woman_offer_nothing(self) -> None:
        await self.memory.add_unnamed(GUILD_A, self.c, what="the woman")
        other = await self.campaigns.create(GUILD_A, "Sunken City", 7)
        await self.memory.add_unnamed(GUILD_A, other.id, what="the woman")
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, self.c), [])
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, other.id), [])


class TheKeeper(unittest.TestCase):
    """Which of two a Yes keeps is one answer, even for two made in the same second (#3457)."""

    def keeper(self, rows: list[dict[str, object]], ties: dict[str, int] | None = None) -> str:
        (offer,) = same_person.unnamed_pairs(rows, (), ties)
        return offer.unnamed_id

    def test_the_strictly_older_is_kept(self) -> None:
        rows = [_row("b", "the woman", 1), _row("a", "the woman", 2)]
        self.assertEqual(self.keeper(rows, {"a": 5}), "b")  # (more ties do not beat age)

    def test_in_a_tie_of_age_the_one_with_more_confirmed_ties_is_kept(self) -> None:
        rows = [_row("a", "the woman", 7), _row("b", "the woman", 7)]
        self.assertEqual(self.keeper(rows, {"b": 2, "a": 1}), "b")

    def test_in_a_tie_of_both_the_smaller_id_is_kept_whatever_the_order_given(self) -> None:
        rows = [_row("m", "the woman", 7), _row("c", "the woman", 7)]
        self.assertEqual(self.keeper(rows), "c")
        self.assertEqual(self.keeper(rows[::-1]), "c")


class ThePairRule(unittest.TestCase):
    def test_a_no_to_one_pair_does_not_hide_the_others(self) -> None:
        rows = [_row("a", "the woman", 1), _row("b", "the woman", 2), _row("c", "the woman", 3)]
        found = same_person.unnamed_pairs(rows, {("a", "b"), ("a", "c")})
        self.assertEqual([(o.unnamed_id, o.named_id) for o in found], [("b", "c")])

    def test_only_a_character_is_paired(self) -> None:
        rows = [{**_row("a", "the lantern", 1), "type": "item"},
                {**_row("b", "the lantern", 2), "type": "item"}]  # fmt: skip
        self.assertEqual(same_person.unnamed_pairs(rows), [])

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
