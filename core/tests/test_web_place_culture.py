"""A place's culture and who lives there on the website (#4412, migration 0590, the web half of
#4257 slices 3a and 3b): the doors give a row's named culture and the kept cultures, two queue
actions take only the fixed words (a lineage or a named culture and how strongly, or `other` with
the DM's words, or nothing to clear), and the bot runs them through the store's own rules (one row
per lineage side by side). Invented names. Needs Postgres."""

from __future__ import annotations

from tests.test_web_place_world import PlaceWorld


class Culture(PlaceWorld):
    async def rows(self, layer: str) -> list[dict[str, object]]:
        return [r for r in (await self.world())["rows"] if r["layer"] == layer]


class TheActions(Culture):
    async def test_a_lineage_and_how_strongly_is_one_row_per_lineage(self) -> None:
        for value, how in (("dwarf", "strong"), ("elf", "once")):
            ok = await self.run_one(self.keep, action="map-culture", value=value, how=how)
            self.assertEqual(ok, [""])
        got = {(r["value"], r["how"]) for r in await self.rows("culture")}
        self.assertEqual(got, {("dwarf", "strong"), ("elf", "once")})  # (side by side)
        await self.run_one(self.keep, action="map-culture", value="dwarf", how="traces")
        got = {(r["value"], r["how"]) for r in await self.rows("culture")}
        self.assertEqual(got, {("dwarf", "traces"), ("elf", "once")})  # (one lineage replaced)

    async def test_who_lives_here_with_and_without_a_share(self) -> None:
        await self.run_one(self.keep, action="map-population", value="halfling", how="most")
        await self.run_one(self.keep, action="map-population", value="human")  # (no share said)
        got = {(r["value"], r["how"]) for r in await self.rows("population")}
        self.assertEqual(got, {("halfling", "most"), ("human", "")})

    async def test_the_dms_own_words_make_a_named_culture_the_picker_then_lists(self) -> None:
        ok = await self.run_one(
            self.keep, action="map-culture", value="other", how="strong", words="Illuskan"
        )
        self.assertEqual(ok, [""])
        (row,) = await self.rows("culture")
        self.assertEqual((row["culture"], row["value"], row["how"]), ("Illuskan", "", "strong"))
        listed = (await self.world())["cultures"]
        self.assertEqual([c["name"] for c in listed], ["Illuskan"])
        # a named culture is picked again by its id
        again = await self.run_one(
            self.keep,
            action="map-culture", value=listed[0]["id"], how="some",
        )  # fmt: skip
        self.assertEqual(again, [""])

    async def test_clearing_takes_every_lineage_off(self) -> None:
        await self.run_one(self.keep, action="map-culture", value="dwarf", how="strong")
        await self.run_one(self.keep, action="map-culture", value="elf", how="some")
        self.assertEqual(await self.run_one(self.keep, action="map-culture", value=None), [""])
        self.assertEqual(await self.rows("culture"), [])

    async def test_only_the_fixed_words_are_taken(self) -> None:
        for body in (
            {"action": "map-culture", "value": "dwarf"},  # (a culture needs its strength)
            {"action": "map-culture", "value": "dwarf", "how": "mostly"},
            {"action": "map-culture", "value": "duergar", "how": "strong"},
            {"action": "map-culture", "value": "other", "how": "strong"},  # (no words)
            {"action": "map-culture", "value": "other", "how": "strong", "words": "x" * 61},
            {"action": "map-culture", "value": "dwarf", "how": "strong", "words": "x"},
            {"action": "map-culture", "value": None, "how": "strong"},
            {"action": "map-population", "value": "dwarf", "how": "strong"},  # (a culture's how)
            {"action": "map-population", "value": "e" * 32, "how": "all"},  # (population has no id)
            {"action": "map-population", "value": 5},
        ):
            self.assertIn(
                await self.refused_action(self.keep, **body),
                ("bad_name", "bad_action", "bad_text"),
                body,
            )
        self.assertEqual(await self.rows("culture"), [])
        self.assertEqual(await self.rows("population"), [])

    async def test_a_player_character_is_not_a_place_and_the_bot_refuses_it(self) -> None:
        outcome = await self.run_one(self.sword, action="map-culture", value="dwarf", how="strong")
        self.assertNotEqual(outcome, [""])


class TheNamedCultures(Culture):
    async def make(self, name: str = "Illuskan") -> str:
        await self.run_one(self.keep, action="map-culture", value="other", how="strong", words=name)
        return str((await self.world())["cultures"][0]["id"])

    async def test_a_rejected_culture_is_not_named_or_listed(self) -> None:
        culture = await self.make()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_cultures SET status = 'rejected' WHERE id = %s", (culture,)
            )
        got = await self.world()
        self.assertEqual(got["cultures"], [])
        (row,) = await self.rows("culture")
        self.assertIsNone(row["culture"])

    async def test_the_picker_lists_only_kept_cultures_by_name_and_at_most_fourteen(self) -> None:
        for n in range(16):
            await self.run_one(
                self.keep, action="map-culture", value="other", how="some", words=f"Folk {n:02d}"
            )
        names = [c["name"] for c in (await self.world())["cultures"]]
        self.assertEqual(len(names), 14)
        self.assertEqual(names, sorted(names, key=str.lower))

    async def test_another_campaigns_culture_is_not_listed_or_taken(self) -> None:
        other = await self.memory.add_entity(
            self.g, self.two.id, type="place", name="Their keep", source="dm", status="confirmed"
        )
        await self.memory.set_map_fact(
            self.g, self.two.id, other.value.id, "culture", "other", how="strong", words="Reghed",
            source="dm",
        )  # fmt: skip
        self.assertEqual((await self.world())["cultures"], [])
