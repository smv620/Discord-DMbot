"""A place's culture and who lives there in the store (#4257, MapBot 3a; Supervisor's ruling):
the DM's word for one lineage leaves the others beside it, a culture always says how strong, a read
waits for the DM and keeps its part, neither layer is ever read up the place tree, and a
character's own lineage never makes a place's culture. Invented names. Needs Postgres."""

from __future__ import annotations

from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from tests.test_memory_store import GUILD_A, MemoryTest


class CulturePopulation(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.realm = await self.add("Karnholt", type="place", status=CONFIRMED)
        self.town = await self.add("Bree", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, self.realm, "region", source=BY_DM)
        await self.memory.add_relation(
            GUILD_A, self.c, self.town, "located_in", self.realm, source=BY_DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip

    async def facts(self, place: str) -> list[mf.Fact]:
        got = await self.memory.map_facts(GUILD_A, self.c, [place], gate.Viewer.the_dm())
        return sorted(got.get(place, []), key=lambda f: (f.layer, f.value))

    async def set(self, layer: str, value: str | None, how: str = "", **kw: str) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, kw.pop("place", self.town), layer, value, how=how, source=BY_DM, **kw
        )

    async def test_lineages_sit_side_by_side_and_one_is_changed_alone(self) -> None:
        await self.set(mf.CULTURE, "dwarf", "strong")
        await self.set(mf.CULTURE, "elf", "traces")
        await self.set(mf.POPULATION, "halfling", "most")
        await self.set(mf.POPULATION, "human", "some")
        await self.set(mf.CULTURE, "dwarf", "some")  # (the same lineage: its shade changes)
        got = [(f.layer, f.value, f.how) for f in await self.facts(self.town)]
        self.assertEqual(
            got,
            [(mf.CULTURE, "dwarf", "some"), (mf.CULTURE, "elf", "traces"),
             (mf.POPULATION, "halfling", "most"), (mf.POPULATION, "human", "some")],
        )  # fmt: skip
        await self.set(mf.POPULATION, None)  # ("Not sure": the whole layer goes)
        self.assertEqual({f.layer for f in await self.facts(self.town)}, {mf.CULTURE})

    async def test_a_culture_says_how_strong_and_a_population_need_not(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.set(mf.CULTURE, "dwarf")
        with self.assertRaises(MemoryRuleError):
            await self.set(mf.POPULATION, "halfling", "strong")  # (a culture's word)
        await self.set(mf.POPULATION, "halfling")  # ("populated by halflings": no share)
        await self.set(mf.CULTURE, mf.OTHER, "strong", words="duergar")
        values = {(f.layer, f.value, f.words) for f in await self.facts(self.town)}
        self.assertIn((mf.CULTURE, mf.OTHER, "duergar"), values)

    async def test_a_read_waits_for_the_dm_and_keeps_its_part(self) -> None:
        got = await self.memory.add_read_map_facts(
            GUILD_A, self.c,
            [Read(self.town, mf.CULTURE, "gnome", "strong", None, "the gnomish quarter"),
             Read(self.town, mf.POPULATION, "halfling", "many")],
            source="background",
        )  # fmt: skip
        self.assertEqual(got.value["written"], 2)
        rows = {f.layer: f for f in await self.facts(self.town)}
        self.assertEqual(rows[mf.CULTURE].part, "the gnomish quarter")
        self.assertEqual({f.status for f in rows.values()}, {mf.READ})
        await self.set(mf.POPULATION, "human", "most")  # (another lineage: the read stays beside)
        again = await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.town, mf.POPULATION, "elf", "few")], source="background"
        )
        self.assertEqual(again.value["written"], 1)  # (never a correction between lineages)
        human = [f for f in await self.facts(self.town) if f.value == "human"]
        self.assertEqual([(f.status, f.how) for f in human], [(mf.CONFIRMED, "most")])

    async def test_not_sure_clears_a_parts_culture_too(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c,
            [Read(self.town, mf.CULTURE, "gnome", "strong", None, "the gnomish quarter")],
            source="background",
        )  # fmt: skip
        await self.set(mf.CULTURE, "dwarf", "strong")  # (the whole town's: the part's stays)
        self.assertEqual(len(await self.facts(self.town)), 2)
        await self.set(mf.CULTURE, None)
        self.assertEqual(await self.facts(self.town), [])

    async def test_keep_keeps_only_the_layers_its_screen_shows(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c,
            [Read(self.realm, mf.TERRAIN, "mountain", "mostly"),
             Read(self.realm, mf.CULTURE, "dwarf", "strong")],
            source="background",
        )  # fmt: skip
        await self.memory.keep_map_facts(
            GUILD_A, self.c, self.realm, source=BY_DM, layers=(mf.CULTURE, mf.POPULATION)
        )
        status = {f.layer: f.status for f in await self.facts(self.realm)}
        self.assertEqual(status, {mf.CULTURE: mf.CONFIRMED, mf.TERRAIN: mf.READ})
        await self.memory.keep_map_facts(
            GUILD_A, self.c, self.realm, source=BY_DM, layers=mf.UP_TREE
        )
        status = {f.layer: f.status for f in await self.facts(self.realm)}
        self.assertEqual(status, {mf.CULTURE: mf.CONFIRMED, mf.TERRAIN: mf.CONFIRMED})

    async def test_never_read_up_the_tree(self) -> None:
        await self.set(mf.CULTURE, "dwarf", "strong", place=self.realm)
        await self.set(mf.POPULATION, "dwarf", "most", place=self.realm)
        held = await self.memory.map_facts_up(GUILD_A, self.c, self.town, gate.Viewer.the_dm())
        self.assertNotIn(mf.CULTURE, held)  # (a dwarven realm's town is not dwarven)
        self.assertNotIn(mf.POPULATION, held)

    async def test_a_characters_own_lineage_never_makes_a_places_culture(self) -> None:
        # (Supervisor's named test, through the reader the lore upload uses)
        text = "Brewner, a dwarven smith of Bree, mends gates. The dwarf Brewner lives in Bree."
        self.assertEqual(mf.read_facts("Bree", text, {}), [])
