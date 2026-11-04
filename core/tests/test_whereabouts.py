"""An item has one current whereabouts (#3363): carrying and being kept in a place end each
other, and with no order between them carrying wins. Names are invented."""

from __future__ import annotations

import unittest
from dataclasses import replace

from dmbot.memory import whereabouts
from dmbot.memory.models import CONFIRMED, PROPOSED, Relation
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

TAVIN, SWORD, VAULT, CHEST = "t" * 32, "s" * 32, "v" * 32, "c" * 32


def rel(
    subject: str, predicate: str, obj: str, detail: str = "", rid: str = "f" * 32,
    start: int | None = None, end: int | None = None, status: str = CONFIRMED,
) -> Relation:  # fmt: skip
    return Relation(
        rid,
        subject,
        predicate,
        obj,
        detail,
        1.0,
        status,
        "dm",
        (),
        start,
        end,
        None,
        None,
        False,
        0,
    )


class Deciding(unittest.TestCase):
    def test_a_new_carrying_ends_the_present_keeping_and_it_becomes_history(self) -> None:
        kept = rel(SWORD, "located_in", VAULT, "is kept in", rid="1" * 32, start=100)
        carried = rel(TAVIN, "carries", SWORD, rid="2" * 32, start=200)
        got = whereabouts.settle(carried, "character", "item", [kept])
        self.assertEqual(dict(got.ends), {kept.id: {"detail": "was kept in", "to_session_at": 200}})
        self.assertIsNone(got.new_detail)

    def test_an_undated_carrying_still_makes_the_keeping_history(self) -> None:
        kept = rel(SWORD, "located_in", VAULT, "", rid="1" * 32)  # no word: kept, for an item
        got = whereabouts.settle(rel(TAVIN, "carries", SWORD), "character", "item", [kept])
        self.assertEqual(dict(got.ends), {kept.id: {"detail": "was kept in"}})

    def test_a_later_keeping_ends_the_carrying(self) -> None:
        carried = rel(TAVIN, "carries", SWORD, rid="2" * 32, start=100)
        chest = rel(SWORD, "located_in", CHEST, "is kept in", rid="3" * 32, start=200)
        got = whereabouts.settle(chest, "item", "place", [carried])
        self.assertEqual(dict(got.ends), {carried.id: {"to_session_at": 200}})
        self.assertIsNone(got.new_detail)

    def test_with_no_order_between_them_carrying_wins_and_the_place_is_history(self) -> None:
        carried = rel(TAVIN, "carries", SWORD, rid="2" * 32)
        found = rel(SWORD, "located_in", VAULT, "is kept in", rid="3" * 32)
        got = whereabouts.settle(found, "item", "place", [carried])
        self.assertEqual((dict(got.ends), got.new_detail), ({}, "was kept in"))
        # an older sighting than the carrying is no order either
        older = replace(found, from_session_at=50)
        newer_carry = replace(carried, from_session_at=100)
        self.assertEqual(
            whereabouts.settle(older, "item", "place", [newer_carry]).new_detail, "was kept in"
        )

    def test_other_ties_are_left_alone(self) -> None:
        kept = rel(SWORD, "located_in", VAULT, "is kept in", rid="1" * 32)
        owns = rel(TAVIN, "owns", SWORD, rid="4" * 32)
        past = rel(SWORD, "located_in", VAULT, "was kept in", rid="5" * 32)
        ended = rel(SWORD, "located_in", VAULT, "is kept in", rid="6" * 32, end=10)
        carried = rel(TAVIN, "carries", SWORD, rid="2" * 32)
        # ownership is never ended; a past keeping or an ended fact is history already
        self.assertEqual(
            whereabouts.settle(carried, "character", "item", [owns, past, ended]).ends, {}
        )
        self.assertEqual(whereabouts.settle(owns, "character", "item", [kept]).ends, {})
        # a character's place is not an item's: a sighting does not touch what Tavin carries
        seen = rel(TAVIN, "located_in", VAULT, "was last seen in", rid="7" * 32)
        self.assertEqual(whereabouts.settle(seen, "character", "place", [carried]).ends, {})
        # a proposal ends nothing until it is confirmed
        waiting = replace(carried, status=PROPOSED)
        self.assertEqual(whereabouts.settle(waiting, "character", "item", [kept]).ends, {})


class InTheStore(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", type="character")
        self.sword = await self.add("Moonblade", type="item")
        self.vault = await self.add("Greyvault", type="place")
        self.chest = await self.add("Old Chest", type="place")

    async def put(self, a: str, pred: str, b: str, detail: str = "", at: int | None = None) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, a, pred, b, source="dm", confidence=1.0, status=CONFIRMED,
            detail=detail, from_session_at=at,
        )  # fmt: skip

    async def held(self) -> dict[tuple[str, str], tuple[str, int | None]]:
        rels = await self.memory.relations(GUILD_A, self.c)
        return {(r.predicate, r.object_id): (r.detail, r.to_session_at) for r in rels}

    async def test_a_sword_kept_in_a_vault_then_carried_is_carried_and_was_kept(self) -> None:
        await self.put(self.sword, "located_in", self.vault, "is kept in", 100)
        await self.put(self.tavin, "carries", self.sword, "", 200)
        self.assertEqual(
            await self.held(),
            {
                ("located_in", self.vault): ("was kept in", 200),
                ("carries", self.sword): ("", None),
            },
        )

    async def test_an_amulet_carried_then_put_in_a_chest_is_kept_and_no_longer_carried(
        self,
    ) -> None:
        await self.put(self.tavin, "carries", self.sword, "", 100)
        await self.put(self.sword, "located_in", self.chest, "is kept in", 200)
        self.assertEqual(
            await self.held(),
            {
                ("carries", self.sword): ("", 200),
                ("located_in", self.chest): ("is kept in", None),
            },
        )

    async def test_a_read_that_gives_both_has_carrying_win_either_way_round(self) -> None:
        for first in ("kept", "carried"):
            tavin = await self.add(f"Tavin {first}", type="character")
            sword = await self.add(f"Blade {first}", type="item")
            vault = await self.add(f"Vault {first}", type="place")
            keep = (sword, "located_in", vault, "is kept in")
            carry = (tavin, "carries", sword, "")
            for a, p, b, d in (keep, carry) if first == "kept" else (carry, keep):
                await self.put(a, p, b, d)
            rels = {
                r.predicate: (r.detail, r.to_session_at)
                for r in await self.memory.relations(GUILD_A, self.c)
                if sword in (r.subject_id, r.object_id)
            }
            self.assertEqual(
                rels, {"carries": ("", None), "located_in": ("was kept in", None)}, first
            )

    async def test_owning_it_is_not_ended_by_either(self) -> None:
        await self.put(self.tavin, "owns", self.sword)
        await self.put(self.sword, "located_in", self.vault, "is kept in", 100)
        await self.put(self.tavin, "carries", self.sword, "", 200)
        self.assertEqual((await self.held())[("owns", self.sword)], ("", None))


if __name__ == "__main__":
    unittest.main()
