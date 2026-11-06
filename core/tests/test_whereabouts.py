"""An item has one current whereabouts (#3363): carrying and being kept in a place end each
other, the newest wins, and with no order carrying wins. Names are invented."""

from __future__ import annotations

import unittest
from dataclasses import replace

from dmbot.memory import whereabouts
from dmbot.memory.models import CONFIRMED, PROPOSED, Relation
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

TAVIN, MIRA, SWORD, VAULT, CHEST = "t" * 32, "m" * 32, "s" * 32, "v" * 32, "c" * 32


def rel(
    subject: str, predicate: str, obj: str, detail: str = "", rid: str = "f" * 32,
    start: int | None = None, end: int | None = None, status: str = CONFIRMED,
    secret: bool = False,
) -> Relation:  # fmt: skip
    return Relation(
        rid, subject, predicate, obj, detail, 1.0, status, "dm", (), start, end, None, None,
        secret, 0,
    )  # fmt: skip


def keeping(rid: str = "1" * 32, detail: str = "is kept in", **kw: object) -> Relation:
    return rel(SWORD, "located_in", VAULT, detail, rid=rid, **kw)  # type: ignore[arg-type]


def carrying(rid: str = "2" * 32, who: str = TAVIN, **kw: object) -> Relation:
    return rel(who, "carries", SWORD, rid=rid, **kw)  # type: ignore[arg-type]


class Deciding(unittest.TestCase):
    def test_a_new_carrying_ends_the_present_keeping_and_it_becomes_history(self) -> None:
        kept = keeping(start=100)
        got = whereabouts.settle(carrying(start=200), "character", "item", [kept])
        self.assertEqual(dict(got.ends), {kept.id: {"detail": "was kept in", "to_session_at": 200}})
        self.assertEqual(dict(got.new_changes), {})

    def test_an_undated_carrying_still_makes_the_keeping_history(self) -> None:
        kept = keeping(detail="")  # no word: kept, for an item
        got = whereabouts.settle(carrying(), "character", "item", [kept])
        self.assertEqual(dict(got.ends), {kept.id: {"detail": "was kept in"}})

    def test_a_later_keeping_ends_the_carrying(self) -> None:
        carried = carrying(start=100)
        got = whereabouts.settle(keeping(start=200), "item", "place", [carried])
        self.assertEqual(dict(got.ends), {carried.id: {"to_session_at": 200}})
        self.assertEqual(dict(got.new_changes), {})

    def test_with_no_order_between_them_carrying_wins_and_the_place_is_history(self) -> None:
        got = whereabouts.settle(keeping(), "item", "place", [carrying()])
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {"detail": "was kept in"}))
        # an older sighting than the carrying is no order either
        older = whereabouts.settle(keeping(start=50), "item", "place", [carrying(start=100)])
        self.assertEqual(dict(older.new_changes), {"detail": "was kept in"})

    def test_an_older_carrying_read_late_never_ends_a_newer_keeping(self) -> None:
        kept = keeping(start=700)
        got = whereabouts.settle(carrying(start=300), "character", "item", [kept])
        self.assertEqual(dict(got.ends), {})
        self.assertEqual(dict(got.new_changes), {"to_session_at": 700})  # the carrying is history

    def test_another_carrier_is_left_alone_on_purpose(self) -> None:
        # the ruling is about keeping; two carriers are for the DM to sort out
        got = whereabouts.settle(
            carrying(who=MIRA, rid="4" * 32), "character", "item", [carrying()]
        )
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))

    def test_other_ties_are_left_alone(self) -> None:
        owns = rel(TAVIN, "owns", SWORD, rid="4" * 32)
        past = keeping(rid="5" * 32, detail="was kept in")
        ended = keeping(rid="6" * 32, end=10)
        # ownership is never ended; a past keeping or an ended fact is history already
        got = whereabouts.settle(carrying(), "character", "item", [owns, past, ended])
        self.assertEqual(dict(got.ends), {})
        self.assertEqual(whereabouts.settle(owns, "character", "item", [keeping()]).ends, {})
        # a past keeping read now ends nothing
        got = whereabouts.settle(past, "item", "place", [carrying()])
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))
        # a character's place is not an item's: a sighting does not touch what Tavin carries
        seen = rel(TAVIN, "located_in", VAULT, "was last seen in", rid="7" * 32)
        self.assertEqual(whereabouts.settle(seen, "character", "place", [carrying()]).ends, {})
        # a proposal ends nothing until it is confirmed
        waiting = replace(carrying(), status=PROPOSED)
        self.assertEqual(whereabouts.settle(waiting, "character", "item", [keeping()]).ends, {})

    def test_a_hidden_fact_never_changes_what_the_players_can_see(self) -> None:
        kept = keeping()
        secret_carry = carrying(secret=True)
        got = whereabouts.settle(secret_carry, "character", "item", [kept])
        self.assertEqual(dict(got.ends), {})  # a secret carrying leaves the visible keeping
        hidden_kept = keeping(secret=True)
        got = whereabouts.settle(hidden_kept, "item", "place", [carrying(start=1)])
        self.assertEqual(dict(got.new_changes), {"detail": "was kept in"})  # its own, hidden
        # a hidden carrying does not turn a visible keeping into history
        got = whereabouts.settle(keeping(), "item", "place", [secret_carry])
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))
        # a visible fact may end a hidden one
        got = whereabouts.settle(carrying(), "character", "item", [keeping(secret=True)])
        self.assertEqual(set(got.ends), {"1" * 32})


PARTY, TOWN_A, TOWN_B = "p" * 32, "a" * 32, "b" * 32


def seen(place: str, rid: str, **kw: object) -> Relation:
    return rel(PARTY, "located_in", place, "was last seen in", rid=rid, **kw)  # type: ignore[arg-type]


class Movers(unittest.TestCase):
    """A character or a group (the party) has one present place of each kind (#3363, Supervisor
    2026-11-05): the most recent wins, by when it happened, and only the same kind is ended."""

    def test_a_later_sighting_ends_the_older_one_and_it_becomes_history(self) -> None:
        old = seen(TOWN_A, "1" * 32, start=100)
        got = whereabouts.settle(seen(TOWN_B, "2" * 32, start=200), "faction", "place", [old])
        self.assertEqual(dict(got.ends), {old.id: {"to_session_at": 200}})
        self.assertEqual(dict(got.new_changes), {})

    def test_an_earlier_sighting_read_late_is_history_and_never_ends_the_newer(self) -> None:
        newer = seen(TOWN_A, "1" * 32, start=300)
        got = whereabouts.settle(seen(TOWN_B, "2" * 32, start=200), "faction", "place", [newer])
        self.assertEqual(dict(got.ends), {})
        self.assertEqual(dict(got.new_changes), {"to_session_at": 300})

    def test_an_older_and_a_newer_place_together_whatever_the_row_order(self) -> None:
        older = seen(TOWN_A, "1" * 32, start=100)
        newer = seen(TOWN_B, "3" * 32, start=300)
        for rows in ([older, newer], [newer, older]):
            got = whereabouts.settle(seen("c" * 32, "2" * 32, start=200), "faction", "place", rows)
            self.assertEqual(dict(got.ends), {older.id: {"to_session_at": 200}})
            self.assertEqual(dict(got.new_changes), {"to_session_at": 300})

    def test_with_no_order_on_either_side_both_stay_for_the_dm_to_settle(self) -> None:
        got = whereabouts.settle(
            seen(TOWN_B, "2" * 32), "faction", "place", [seen(TOWN_A, "1" * 32)]
        )
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))
        same_session = whereabouts.settle(
            seen(TOWN_B, "2" * 32, start=100),
            "faction",
            "place",
            [seen(TOWN_A, "1" * 32, start=100)],
        )
        self.assertEqual((dict(same_session.ends), dict(same_session.new_changes)), ({}, {}))

    def test_a_newer_place_that_is_only_proposed_ends_nothing(self) -> None:
        old = seen(TOWN_A, "1" * 32, start=100)
        got = whereabouts.settle(
            seen(TOWN_B, "2" * 32, start=200, status=PROPOSED), "faction", "place", [old]
        )
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))

    def test_a_proposed_older_place_is_never_ended_by_a_confirmed_one(self) -> None:
        proposed = seen(TOWN_A, "1" * 32, start=100, status=PROPOSED)
        got = whereabouts.settle(seen(TOWN_B, "2" * 32, start=200), "faction", "place", [proposed])
        self.assertEqual(dict(got.ends), {})

    def test_only_the_same_kind_of_place_is_ended(self) -> None:
        lives = rel(PARTY, "located_in", TOWN_A, "lives in", rid="1" * 32, start=100)
        got = whereabouts.settle(seen(TOWN_B, "2" * 32, start=200), "faction", "place", [lives])
        self.assertEqual(dict(got.ends), {})  # (a sighting never ends where someone lives)
        lived = rel(PARTY, "located_in", TOWN_A, "lived in", rid="3" * 32, start=100)
        new_home = rel(PARTY, "located_in", TOWN_B, "lives in", rid="4" * 32, start=200)
        self.assertEqual(dict(whereabouts.settle(new_home, "faction", "place", [lived]).ends), {})

    def test_a_new_home_ends_the_old_one_and_reads_in_the_past(self) -> None:
        lives = rel(TAVIN, "located_in", TOWN_A, "lives in", rid="1" * 32, start=100)
        new_home = rel(TAVIN, "located_in", TOWN_B, "lives in", rid="2" * 32, start=200)
        got = whereabouts.settle(new_home, "character", "place", [lives])
        self.assertEqual(dict(got.ends), {lives.id: {"detail": "lived in", "to_session_at": 200}})

    def test_where_someone_comes_from_is_never_ended(self) -> None:
        born = rel(TAVIN, "located_in", TOWN_A, "comes from", rid="1" * 32, start=100)
        other = rel(TAVIN, "located_in", TOWN_B, "comes from", rid="2" * 32, start=200)
        self.assertEqual(dict(whereabouts.settle(other, "character", "place", [born]).ends), {})

    def test_a_fact_the_dm_types_is_the_newest(self) -> None:
        old = seen(TOWN_A, "1" * 32, start=500)
        got = whereabouts.settle(
            seen(TOWN_B, "2" * 32, start=100), "faction", "place", [old], by_hand=True, now=900
        )
        self.assertEqual(
            dict(got.ends), {old.id: {"to_session_at": 500}}
        )  # (never before it began)

    def test_a_hidden_fact_never_ends_one_the_players_can_see(self) -> None:
        old = seen(TOWN_A, "1" * 32, start=100)
        got = whereabouts.settle(
            seen(TOWN_B, "2" * 32, start=200, secret=True), "faction", "place", [old]
        )
        self.assertEqual(dict(got.ends), {})

    def test_the_same_place_again_and_a_place_in_a_place_are_left_alone(self) -> None:
        same = whereabouts.settle(
            seen(TOWN_A, "2" * 32, start=200),
            "faction",
            "place",
            [seen(TOWN_A, "1" * 32, start=100)],
        )
        self.assertEqual(dict(same.ends), {})
        # a tavern (a place) in a town is not something that moves
        tavern = rel(TOWN_A, "located_in", TOWN_B, "", rid="5" * 32, start=200)
        self.assertEqual(
            dict(whereabouts.settle(tavern, "place", "place", [seen(TOWN_A, "1" * 32)]).ends), {}
        )

    def test_a_hidden_newer_place_never_turns_a_visible_one_into_history(self) -> None:
        visible = seen(TOWN_A, "1" * 32, start=100)
        hidden = seen(TOWN_B, "2" * 32, start=200, secret=True)
        # (settling the visible one later, with the hidden newer one in the facts)
        got = whereabouts.settle(visible, "faction", "place", [hidden])
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))
        # a hidden fact read late is history itself, since only hidden facts could see it
        hidden_old = seen(TOWN_A, "3" * 32, start=100, secret=True)
        late = whereabouts.settle(
            seen(TOWN_B, "4" * 32, start=300, secret=True), "faction", "place", [hidden_old]
        )
        self.assertEqual(dict(late.ends), {hidden_old.id: {"to_session_at": 300}})

    def test_a_home_read_late_is_history_and_reads_in_the_past(self) -> None:
        newer = rel(TAVIN, "located_in", TOWN_B, "lives in", rid="1" * 32, start=300)
        older = rel(TAVIN, "located_in", TOWN_A, "lives in", rid="2" * 32, start=100)
        got = whereabouts.settle(older, "character", "place", [newer])
        self.assertEqual(dict(got.new_changes), {"to_session_at": 300, "detail": "lived in"})

    def test_a_date_missing_on_either_side_is_no_order(self) -> None:
        dated = seen(TOWN_A, "1" * 32, start=100)
        undated = seen(TOWN_B, "2" * 32)
        for new, old in ((undated, dated), (dated, undated)):
            got = whereabouts.settle(new, "faction", "place", [old])
            self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))

    def test_a_place_inside_the_other_is_one_place_and_ends_nothing(self) -> None:
        inn = seen(TOWN_A, "1" * 32, start=100)
        got = whereabouts.settle(
            seen(TOWN_B, "2" * 32, start=200), "faction", "place", [inn],
            related=lambda a, b: {a, b} == {TOWN_A, TOWN_B},
        )  # fmt: skip
        self.assertEqual(dict(got.ends), {})

    def test_an_object_that_is_not_a_place_ends_nothing(self) -> None:
        old = seen(TOWN_A, "1" * 32, start=100)
        got = whereabouts.settle(seen(MIRA, "2" * 32, start=200), "faction", "character", [old])
        self.assertEqual(dict(got.ends), {})

    def test_a_group_only_has_its_sighting_replaced(self) -> None:
        based = rel(PARTY, "located_in", TOWN_A, "is based in", rid="1" * 32, start=100)
        new = rel(PARTY, "located_in", TOWN_B, "is based in", rid="2" * 32, start=200)
        self.assertEqual(dict(whereabouts.settle(new, "faction", "place", [based]).ends), {})
        # a character's work place is replaced
        works = rel(TAVIN, "located_in", TOWN_A, "works in", rid="3" * 32, start=100)
        moved = rel(TAVIN, "located_in", TOWN_B, "works in", rid="4" * 32, start=200)
        got = whereabouts.settle(moved, "character", "place", [works])
        self.assertEqual(dict(got.ends), {works.id: {"detail": "worked in", "to_session_at": 200}})


class ByHand(unittest.TestCase):
    def test_a_fact_the_dm_types_is_the_newest_and_never_history(self) -> None:
        # Tavin carries it, then the DM types "kept in the chest": the carrying ends
        carried = carrying(start=100)
        got = whereabouts.settle(keeping(), "item", "place", [carried], by_hand=True, now=900)
        self.assertEqual(dict(got.ends), {carried.id: {"to_session_at": 900}})
        self.assertEqual(dict(got.new_changes), {})
        # the chest keeps it, then the DM types "Tavin carries it": the keeping becomes history
        kept = keeping(start=700)
        got = whereabouts.settle(carrying(start=300), "character", "item", [kept], by_hand=True)
        self.assertEqual(set(got.ends), {kept.id})
        self.assertEqual(dict(got.new_changes), {})  # (not made history, though it is older)
        # the same facts from a reader keep the old order
        self.assertEqual(
            dict(whereabouts.settle(keeping(), "item", "place", [carried]).new_changes),
            {"detail": "was kept in"},
        )

    def test_a_hidden_fact_the_dm_types_still_never_changes_what_players_see(self) -> None:
        got = whereabouts.settle(
            keeping(secret=True), "item", "place", [carrying()], by_hand=True, now=900
        )
        self.assertEqual((dict(got.ends), dict(got.new_changes)), ({}, {}))


class InTheStore(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", type="character")
        self.sword = await self.add("Moonblade", type="item")
        self.vault = await self.add("Greyvault", type="place")
        self.chest = await self.add("Old Chest", type="place")

    async def put(
        self, a: str, pred: str, b: str, detail: str = "", at: int | None = None,
        status: str = CONFIRMED, by_hand: bool = False,
    ) -> Relation:  # fmt: skip
        got = await self.memory.add_relation(
            GUILD_A, self.c, a, pred, b, source="dm", confidence=1.0, status=status,
            detail=detail, from_session_at=at, by_hand=by_hand,
        )  # fmt: skip
        return got.value[0]

    async def held(self) -> dict[tuple[str, str], tuple[str, int | None]]:
        rels = await self.memory.relations(GUILD_A, self.c)
        return {(r.predicate, r.object_id): (r.detail, r.to_session_at) for r in rels}

    async def test_the_party_seen_in_two_towns_keeps_the_later_one_present(self) -> None:
        party = await self.add("The party", type="faction")
        await self.put(party, "located_in", self.vault, "was last seen in", 100)
        await self.put(party, "located_in", self.chest, "was last seen in", 200)
        held = await self.held()
        self.assertEqual(held[("located_in", self.vault)], ("was last seen in", 200))  # history
        self.assertEqual(held[("located_in", self.chest)], ("was last seen in", None))  # present

    async def test_a_proposed_newer_place_waits_and_ends_the_confirmed_one_only_when_kept(
        self,
    ) -> None:
        party = await self.add("The party", type="faction")
        await self.put(party, "located_in", self.vault, "was last seen in", 100)
        proposed = await self.put(
            party, "located_in", self.chest, "was last seen in", 200, status=PROPOSED
        )
        self.assertEqual((await self.held())[("located_in", self.vault)][1], None)  # still present
        await self.memory.update_relation(
            GUILD_A, self.c, proposed.id, status=CONFIRMED, source="dm"
        )
        self.assertEqual((await self.held())[("located_in", self.vault)][1], 200)

    async def test_undo_of_the_move_brings_the_old_place_back(self) -> None:
        party = await self.add("The party", type="faction")
        await self.put(party, "located_in", self.vault, "was last seen in", 100)
        newest = await self.memory.add_relation(
            GUILD_A, self.c, party, "located_in", self.chest, source="dm", confidence=1.0,
            status=CONFIRMED, detail="was last seen in", from_session_at=200,
        )  # fmt: skip
        self.assertEqual((await self.held())[("located_in", self.vault)][1], 200)
        assert newest.batch is not None
        await self.memory.undo(GUILD_A, self.c, newest.batch, source="dm")
        held = await self.held()
        self.assertEqual(held[("located_in", self.vault)][1], None)  # present again
        self.assertNotIn(("located_in", self.chest), held)

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

    async def test_the_dm_typing_a_keeping_over_a_carrying_ends_the_carrying(self) -> None:
        await self.put(self.tavin, "carries", self.sword, "", 100)
        await self.put(self.sword, "located_in", self.chest, "is kept in", by_hand=True)
        held = await self.held()
        self.assertEqual(held[("located_in", self.chest)], ("is kept in", None))  # not history
        self.assertIsNotNone(held[("carries", self.sword)][1])  # Tavin's carrying has ended

    async def test_the_dm_typing_a_carrying_over_a_keeping_makes_the_keeping_history(self) -> None:
        await self.put(self.sword, "located_in", self.chest, "is kept in", 700)
        await self.put(self.tavin, "carries", self.sword, "", 300, by_hand=True)
        held = await self.held()
        self.assertEqual(held[("carries", self.sword)], ("", None))  # live, though dated earlier
        self.assertEqual(held[("located_in", self.chest)][0], "was kept in")

    async def test_owning_it_is_not_ended_by_either(self) -> None:
        await self.put(self.tavin, "owns", self.sword)
        await self.put(self.sword, "located_in", self.vault, "is kept in", 100)
        await self.put(self.tavin, "carries", self.sword, "", 200)
        self.assertEqual((await self.held())[("owns", self.sword)], ("", None))

    async def test_confirming_a_waiting_fact_settles_the_item_too(self) -> None:
        # a reader's facts wait for the DM's yes: it is the yes that makes them live
        await self.put(self.sword, "located_in", self.vault, "is kept in", 100)
        waiting = await self.put(self.tavin, "carries", self.sword, "", 200, status=PROPOSED)
        self.assertEqual((await self.held())[("located_in", self.vault)], ("is kept in", None))
        await self.memory.update_relation(
            GUILD_A, self.c, waiting.id, source="dm", status=CONFIRMED
        )
        self.assertEqual((await self.held())[("located_in", self.vault)], ("was kept in", 200))

    async def test_an_older_carrying_read_late_is_history_and_the_keeping_stands(self) -> None:
        await self.put(self.sword, "located_in", self.vault, "is kept in", 700)
        await self.put(self.tavin, "carries", self.sword, "", 300)
        self.assertEqual(
            await self.held(),
            {("located_in", self.vault): ("is kept in", None), ("carries", self.sword): ("", 700)},
        )

    async def test_a_repeated_carrying_leaves_the_keeping_alone(self) -> None:
        await self.put(self.tavin, "carries", self.sword, "", 100)
        await self.put(self.sword, "located_in", self.vault, "is kept in", 50)  # older: history
        before = await self.held()
        await self.put(self.tavin, "carries", self.sword, "", 100)  # the same fact again
        self.assertEqual(await self.held(), before)

    async def test_taking_back_a_removed_carrying_leaves_one_current_whereabouts(self) -> None:
        # (#3375) the DM removed the carrying, a place then kept the item; Undo brings the carrying
        # back, and the item still has one whereabouts: what the DM brought back is the newest
        carrying_now = await self.put(self.tavin, "carries", self.sword, "", 100)
        removed = await self.memory.update_relation(
            GUILD_A, self.c, carrying_now.id, source="dm", status="rejected"
        )
        await self.put(self.sword, "located_in", self.chest, "is kept in", 200)
        back = await self.memory.undo_connection(
            GUILD_A, self.c, removed.batch or 0, within=3600, secrets=True
        )
        self.assertEqual(back.value.status, CONFIRMED)
        self.assertEqual(
            await self.held(),
            {
                ("carries", self.sword): ("", None),
                ("located_in", self.chest): ("was kept in", None),
            },
        )


if __name__ == "__main__":
    unittest.main()
