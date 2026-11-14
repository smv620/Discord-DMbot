"""The read-only movers check (#3363): counts of movers that hold more than one current place of
one kind, split by kind and by how many, and nothing else. Invented ids."""

from __future__ import annotations

import unittest

from dmbot.devtools import movers_check as mc
from dmbot.memory.models import CONFIRMED
from tests.test_memory_names import GUILD, NamesTest

C = "c1"


def row(mover: str, kind: str = "character", place: str = "place", detail: str = "") -> mc.Row:
    return (C, mover, kind, place, detail)


class TheCounting(unittest.TestCase):
    def test_two_sightings_of_one_character_count_once_as_two_places(self) -> None:
        got = mc.count_movers([row("a"), row("a"), row("b")])
        self.assertEqual((got.movers_with_a_place, got.movers_over), (2, 1))
        self.assertEqual(dict(got.over), {("character", 2): 1})

    def test_different_kinds_of_tie_are_not_a_clash(self) -> None:
        got = mc.count_movers([row("a", detail="lives in"), row("a", detail="works in")])
        self.assertEqual(got.movers_over, 0)

    def test_where_someone_comes_from_and_a_past_tie_never_clash(self) -> None:
        rows = [row("a", detail="comes from")] * 2 + [row("a", detail="lived in")] * 2
        got = mc.count_movers(rows)
        self.assertEqual(got.movers_over, 0)

    def test_a_group_counts_only_for_its_sightings(self) -> None:
        based = [
            row("g", "faction", detail="is based in"),
            row("g", "faction", detail="is based in"),
        ]
        self.assertEqual(mc.count_movers(based).movers_over, 0)
        seen = [row("g", "faction"), row("g", "faction")]
        self.assertEqual(dict(mc.count_movers(seen).over), {("faction", 2): 1})

    def test_an_item_and_a_link_to_another_mover_are_not_counted(self) -> None:
        got = mc.count_movers([row("i", "item"), row("i", "item"), row("a", place="character")])
        self.assertEqual((got.movers_with_a_place, got.movers_over), (0, 0))

    def test_four_or_more_are_grouped_and_the_line_has_counts_only(self) -> None:
        got = mc.count_movers([row("a")] * 6)
        self.assertEqual(dict(got.over), {("character", 4): 1})
        line = mc.report(got)
        self.assertIn("1 hold more than one place", line)
        self.assertIn("4 or more", line)
        self.assertNotIn("c1", line)  # (no ids)

    def test_nothing_to_report_reads_plainly(self) -> None:
        self.assertIn("0 hold more than one place of one kind.", mc.report(mc.count_movers([])))


class TheRead(NamesTest):
    async def test_it_reads_current_confirmed_ties_of_one_server_only(self) -> None:
        async def add(name: str, kind: str, role: str | None = None) -> str:
            made = await self.memory.add_entity(
                GUILD, self.campaign.id, name=name, type=kind, role=role,
                status=CONFIRMED, source="dm",
            )  # fmt: skip
            return str(made.value.id)

        mira, hold, port = (
            await add("Mira", "character", "npc"),
            await add("Hold", "place"),
            await add("Port", "place"),
        )
        for place in (hold, port):  # (no dates: no order, so both stay current)
            await self.memory.add_relation(
                GUILD, self.campaign.id, mira, "located_in", place,
                source="dm", confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        rows = await mc.read_rows(self.db, GUILD, self.campaign.id)
        got = mc.count_movers(rows)
        self.assertEqual(dict(got.over), {("character", 2): 1})
        self.assertEqual(await mc.read_rows(self.db, GUILD, "x" * 32), [])

    async def test_it_leaves_out_ended_unconfirmed_and_other_servers_rows(self) -> None:
        async def add(name: str, kind: str, role: str | None, status: str = CONFIRMED) -> str:
            made = await self.memory.add_entity(
                GUILD, self.campaign.id, name=name, type=kind, role=role, status=status,
                source="dm",
            )  # fmt: skip
            return str(made.value.id)

        mira = await add("Mira", "character", "npc")
        towns = [await add(n, "place", None) for n in ("Hold", "Port", "Reach")]
        maybe = await add("Maybe", "place", None, "proposed")

        async def tie(place: str, **kw: object) -> str:
            made = await self.memory.add_relation(
                GUILD, self.campaign.id, mira, "located_in", place, source="dm", confidence=1.0,
                **kw,  # type: ignore[arg-type]
            )  # fmt: skip
            return str(made.value[0].id)

        await tie(towns[0], status=CONFIRMED)
        ended = await tie(towns[1], status=CONFIRMED)
        await self.memory.update_relation(
            GUILD, self.campaign.id, ended, source="dm", to_session_at=5
        )
        await tie(towns[2], status="proposed")
        await tie(maybe, status=CONFIRMED)  # (a proposed place)
        got = await mc.read_rows(self.db, GUILD, None)
        self.assertEqual(len(got), 1)  # only the one confirmed, current tie to a confirmed place
        self.assertEqual(await mc.read_rows(self.db, GUILD + 1, None), [])
