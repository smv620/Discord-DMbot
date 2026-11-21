"""MapBot's "how far" and "which way" (#4670, MapBot layers slice 4b; Supervisor's ruling and the
ux-critic's words): the sums, the rounding, which map answers, the question shapes, the words, the
sidebar's thin layer, and who sees what (a player only from the table's maps). Invented names. The
last class needs Postgres; the rest need nothing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.maps import measure, measure_phrase
from dmbot.maps import measure_words as words
from dmbot.sidebar.map_answers import MapMeasuring
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

A, B = ("a", "Bryn Shander"), ("b", "Targos")


def a_map(**more: Any) -> measure.MapInfo:
    base: dict[str, Any] = dict(
        id="m1", title="Icewind Dale", kind="region", width=1000, height=1000, per_px=0.1,
        unit="miles", north=0.0, north_known=True,
    )  # fmt: skip
    base.update(more)
    return measure.MapInfo(**base)


def pins(map_id: str = "m1", a: tuple[float, float] = (0.5, 0.1), b: Any = (0.5, 0.5)) -> list[Any]:
    return [measure.Pin(map_id, "a", *a), measure.Pin(map_id, "b", *b)]


class TheSums(unittest.TestCase):
    def test_a_straight_line_with_the_scale(self) -> None:
        pa, pb = pins()
        self.assertAlmostEqual(measure.distance(a_map(), pa, pb) or 0, 40.0)
        self.assertIsNone(measure.distance(a_map(per_px=None), pa, pb))

    def test_rounding_never_says_zero(self) -> None:
        self.assertEqual(measure.rounded(39.6, "miles"), "40 miles")
        self.assertEqual(measure.rounded(1.1, "miles"), "1 mile")
        self.assertEqual(measure.rounded(2.3, "miles"), "2.5 miles")
        self.assertIsNone(measure.rounded(0.3, "miles"))
        self.assertEqual(measure.rounded(23, "feet"), "25 feet")
        self.assertIsNone(measure.rounded(1, "feet"))
        self.assertEqual(measure.rounded(2.5, "feet"), "5 feet")  # (a half rounds up, never 0)
        self.assertEqual(measure.rounded(0.75, "miles"), "1 mile")

    def test_the_bearing_uses_the_maps_north(self) -> None:
        pa, pb = pins()  # (a is above b on the picture)
        self.assertEqual(measure.direction(measure.bearing(a_map(), pb, pa) or 0), "north")
        tilted = a_map(north=90.0)  # (north points right: "up" is west)
        self.assertEqual(measure.direction(measure.bearing(tilted, pb, pa) or 0), "west")
        self.assertIsNone(measure.bearing(a_map(), pa, pa))
        for to, want in (((0.9, 0.5), "east"), ((0.5, 0.9), "south"), ((0.9, 0.1), "north-east")):
            here, there = measure.Pin("m1", "b", 0.5, 0.5), measure.Pin("m1", "a", *to)
            self.assertEqual(measure.direction(measure.bearing(a_map(), here, there) or 0), want)
        wide = a_map(width=4000, height=1000)  # (a wide map: its pixels decide, not a square)
        here, there = measure.Pin("m1", "b", 0.5, 0.5), measure.Pin("m1", "a", 0.6, 0.1)
        self.assertEqual(measure.direction(measure.bearing(wide, here, there) or 0), "north-east")
        # (the same fractions on a square map read north)
        self.assertEqual(measure.direction(measure.bearing(a_map(), here, there) or 0), "north")

    def test_the_most_detailed_map_with_a_scale_answers(self) -> None:
        town = a_map(id="t", kind="settlement", per_px=None, title="Targos")
        region = a_map(id="r", kind="region")
        both = [*pins("t"), *pins("r")]
        used, closest = measure.choose([region, town], both, "a", "b", need_scale=True)
        assert used is not None and closest is not None
        self.assertEqual((used.id, closest.id), ("r", "t"))
        used, _ = measure.choose([region, town], both, "a", "b", need_scale=False)
        assert used is not None
        self.assertEqual(used.id, "t")
        newer = a_map(id="r2", kind="region", created_at=5)  # (a newer version of a map first)
        used, _ = measure.choose([region, newer], [*pins("r"), *pins("r2")], "a", "b",
                                 need_scale=True)  # fmt: skip
        assert used is not None
        self.assertEqual(used.id, "r2")


class TheWords(unittest.TestCase):
    def test_how_far(self) -> None:
        self.assertEqual(
            measure.answer("far", [a_map()], pins(), A, B, dm=False),
            "About 40 miles from Bryn Shander to Targos in a straight line,"
            " on the Icewind Dale map.",
        )

    def test_less_than_the_map_can_tell(self) -> None:
        said = measure.answer("far", [a_map(per_px=0.001)], pins(), A, B, dm=False)
        self.assertTrue(said.startswith("About a mile or less from Bryn Shander"))

    def test_which_way_and_an_assumed_north(self) -> None:
        self.assertEqual(
            measure.answer("way", [a_map()], pins(), A, B, dm=False),
            "Bryn Shander is about north of Targos on the Icewind Dale map.",
        )
        said = measure.answer("way", [a_map(north_known=False)], pins(), A, B, dm=False)
        self.assertTrue(said.endswith("on the Icewind Dale map, if north is at the top."))

    def test_what_is_missing_and_the_fix_for_the_dm_only(self) -> None:
        no_scale = [a_map(per_px=None)]
        self.assertIn(
            "Your DM can add one", measure.answer("far", no_scale, pins(), A, B, dm=False)
        )
        self.assertIn("Maps page", measure.answer("far", no_scale, pins(), A, B, dm=True))
        apart = [*pins("m1")[:1], measure.Pin("m2", "b", 0.5, 0.5)]
        two = [a_map(), a_map(id="m2")]
        self.assertEqual(
            measure.answer("far", two, apart, A, B, dm=False),
            "I don't have a map with both Bryn Shander and Targos on it.",
        )
        self.assertEqual(
            measure.answer("far", [a_map()], pins()[:1], A, B, dm=False),
            "I can't find Targos on any map yet.",
        )

    def test_a_player_is_never_told_a_hidden_map_exists(self) -> None:
        # (the caller passes only the table's maps: the hidden one's pins are not counted)
        said = measure.answer("far", [], pins(), A, B, dm=False)
        self.assertEqual(said, "I can't find Bryn Shander on any map yet.")

    def test_a_title_that_says_map_is_not_doubled(self) -> None:
        self.assertEqual(words.on_map("Ten-Towns Map"), "the Ten-Towns Map")
        self.assertEqual(words.on_map("Map of the Sword Coast"), "Map of the Sword Coast")
        self.assertEqual(words.on_map(""), "the map")
        self.assertEqual(words.on_map("Icewind Dale"), "the Icewind Dale map")

    def test_every_measured_answer_says_about(self) -> None:
        for kind, maps, given in (
            ("far", [a_map()], pins()),
            ("far", [a_map(per_px=0.001)], pins()),
            ("way", [a_map()], pins()),
            ("way", [a_map()], pins(b=(0.5, 0.1))),  # (the same spot)
        ):
            with self.subTest(kind=kind):
                self.assertIn("about", measure.answer(kind, maps, given, A, B, dm=False).lower())

    def test_no_word_is_jargon_or_a_verdict(self) -> None:
        said = " ".join(
            [
                measure.answer("far", [a_map()], pins(), A, B, dm=True),
                measure.answer("far", [a_map(per_px=None)], pins(), A, B, dm=True),
                measure.answer("far", [a_map()], pins()[:1], A, B, dm=True),
            ]
        ).lower()
        for word in ("pin", "wrong", "correct", "exactly"):
            self.assertNotIn(word, said)


class TheQuestions(unittest.TestCase):
    def test_the_shapes(self) -> None:
        cases = {
            "Hey DMbot, how far is Bryn Shander from Targos?": ("far", "Bryn Shander", "Targos"),
            "how far from Targos to Bryn Shander": ("far", "Targos", "Bryn Shander"),
            "What's the distance between Targos and Termalaine?": ("far", "Targos", "Termalaine"),
            "which way is the Old Well from Thornwick?": ("way", "the Old Well", "Thornwick"),
            "What direction is Targos from Bryn Shander?": ("way", "Targos", "Bryn Shander"),
        }
        for q, want in cases.items():
            with self.subTest(q=q):
                got = measure_phrase.read(q)
                assert got is not None
                self.assertEqual((got.kind, got.a, got.b), want)

    def test_anything_else_goes_the_usual_way(self) -> None:
        for q in (
            "how far can I jump?",
            "how far to the next town?",
            "which way did he go?",
            "how far is Targos from Targos?",
            "what does Targos sell?",
        ):
            with self.subTest(q=q):
                self.assertIsNone(measure_phrase.read(q))


class Inner:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def answer(
        self, campaign: Any, question: str, *, asker_id: int, scene: str, table: bool = False
    ) -> str:
        self.asked.append(question)
        return "engine"


class TheLayer(unittest.IsolatedAsyncioTestCase):
    async def layer(self, *, on: bool, text: str | None = "About 40 miles …") -> Any:
        self.inner, self.dms = Inner(), list[bool]()

        async def measured(_c: Any, _asked: Any, dm: bool) -> str | None:
            self.dms.append(dm)
            return text

        return MapMeasuring(self.inner, measured, on=lambda: on, maps_page=lambda: "https://x/m")

    async def test_off_means_the_engine_as_before(self) -> None:
        layer = await self.layer(on=False)
        got = await layer.answer(None, "how far is A from B?", asker_id=1, scene="")
        self.assertEqual(got, "engine")
        self.assertEqual(self.dms, [])

    async def test_a_map_question_is_answered_by_code(self) -> None:
        layer = await self.layer(on=True)
        got = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="")
        self.assertEqual(got.model, "")
        self.assertIn("🗺️ Open the map: https://x/m", got.text)  # (the DM's private answer)
        self.assertEqual(self.dms, [True])

    async def test_the_tables_question_uses_the_tables_maps_and_no_link(self) -> None:
        layer = await self.layer(on=True)
        got = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="", table=True)
        self.assertNotIn("Open the map", got.text)
        self.assertEqual(self.dms, [False])

    async def test_a_dms_private_answer_is_kept_out_of_the_transcript(self) -> None:
        layer = await self.layer(on=True)
        private = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="")
        public = await layer.answer(
            None, "how far is Ab from Bc?", asker_id=1, scene="", table=True
        )
        self.assertEqual((private.in_game, public.in_game), (False, True))

    async def private_case(
        self, asker: int, table_says: Any = "I can't find Bc on any map yet."
    ) -> Any:
        self.dms = []
        campaign = SimpleNamespace(dm_user_ids=frozenset({7}))

        async def measured(_c: Any, _asked: Any, dm: bool) -> Any:
            self.dms.append(dm)
            return "About 40 miles …" if dm else table_says

        layer = MapMeasuring(Inner(), measured, on=lambda: True)
        return await layer.answer(
            campaign, "how far is Ab from Bc?", asker_id=asker, scene="", table=True
        )

    async def test_a_dms_table_question_the_tables_maps_miss_always_goes_privately(self) -> None:
        # (#4676, Supervisor's ruling on #4691: the table hears "Sent to the DM privately" whether
        # or not a hidden map has the answer; the DM's maps are read on the private path only)
        for table_says in (
            "I can't find Bc on any map yet.",
            "I don't have a map with both Ab and Bc on it.",
        ):
            got = await self.private_case(asker=7, table_says=table_says)
            self.assertTrue(got.one_for_dm)
            self.assertFalse(got.in_game)  # (never written to the raw transcript)
            self.assertEqual(got.text, table_says)  # (the table's own words, as a fallback)
            self.assertEqual(self.dms, [False])

    async def test_a_place_not_shown_goes_to_the_dm_privately_and_a_player_the_usual_way(
        self,
    ) -> None:
        # (#4692: the DM's table question goes privately, re-asked with every map; a player's goes
        # to the engine, with no hint and no read of the DM's maps)
        got = await self.private_case(asker=7, table_says=words.NOT_SHOWN)
        self.assertTrue(got.one_for_dm)
        self.assertFalse(got.in_game)
        self.assertEqual(self.dms, [False])
        got = await self.private_case(asker=9, table_says=words.NOT_SHOWN)
        self.assertEqual(got, "engine")
        self.assertEqual(self.dms, [False])

    async def test_a_dms_table_question_the_tables_maps_answer_stays_at_the_table(self) -> None:
        got = await self.private_case(asker=7, table_says="About 40 miles …")
        self.assertFalse(got.one_for_dm)
        self.assertEqual((got.text, self.dms), ("About 40 miles …", [False]))

    async def test_a_players_table_question_gets_the_tables_words(self) -> None:
        got = await self.private_case(asker=8)
        self.assertFalse(got.one_for_dm)
        self.assertEqual(got.text, "I can't find Bc on any map yet.")  # (no hint a map exists)
        self.assertEqual(self.dms, [False])  # (the DM's maps are never read for a player)

    def test_what_counts_as_measured(self) -> None:
        self.assertTrue(words.measured(words.far("40 miles", "A", "B", "X")))
        self.assertTrue(words.measured(words.way("A", "east", "B", "X", north_known=True)))
        self.assertTrue(words.measured(words.same_spot("A", "B", "X")))
        self.assertTrue(words.measured(words.at_most("a mile", "A", "B", "X")))
        for miss in (
            words.not_on_a_map("A", dm=True), words.no_shared_map("A", "B", dm=False),
            words.no_scale("X", dm=True), words.no_scale("X", dm=False),
        ):  # fmt: skip
            self.assertFalse(words.measured(miss), miss)

    async def test_a_failure_goes_to_the_engine(self) -> None:
        inner = Inner()

        async def broken(_c: Any, _a: Any, _dm: bool) -> str | None:
            raise RuntimeError("down")

        layer = MapMeasuring(inner, broken, on=lambda: True)
        got = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="")
        self.assertEqual(got, "engine")

    async def test_the_plans_no_is_the_engines(self) -> None:
        inner = Inner()

        async def refuse(_c: Any, _who: int) -> str | None:
            return "no AI on this plan"

        inner._gate = refuse  # type: ignore[attr-defined]
        measured: list[bool] = []

        async def measure_it(_c: Any, _a: Any, dm: bool) -> str | None:
            measured.append(dm)
            return "About 1 mile"

        layer = MapMeasuring(inner, measure_it, on=lambda: True)
        got = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="")
        self.assertEqual((got, measured), ("engine", []))

    async def test_names_that_are_not_kept_places_go_to_the_engine(self) -> None:
        layer = await self.layer(on=True, text=None)
        got = await layer.answer(None, "how far is Ab from Bc?", asker_id=1, scene="")
        self.assertEqual(got, "engine")


class WhoSeesWhat(
    PinsBase
):  # (Postgres: the store's read, the ruling's condition tested both ways)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        await self.record()  # (Targos and Bryn Shander pinned, a scale bar read)
        # (the players' answer is the table's: with no player character it is common knowledge
        # only, failing closed, Supervisor's ruling M on #4537; a party of one stands for it)
        await self.memory.add_entity(
            GUILD_A, self.one.id, name="Pcwyn Testhand", type="character",
            role="player_character", played_by=4243, status="confirmed", source="dm",
        )  # fmt: skip

    async def ask(self, question: str, *, dm: bool) -> Any:  # (words, NOT_SHOWN or None)
        asked = measure_phrase.read(question)
        assert asked is not None
        return await self.maps.measure(GUILD_A, self.one.id, asked, dm=dm)

    async def test_the_dm_gets_the_distance_from_a_dm_only_map(self) -> None:
        said = await self.ask("how far is Targos from Bryn Shander?", dm=True)
        assert said is not None
        self.assertTrue(said.startswith("About "), said)
        self.assertIn("in a straight line", said)

    async def test_a_player_gets_nothing_from_a_dm_only_map(self) -> None:
        said = await self.ask("how far is Targos from Bryn Shander?", dm=False)
        self.assertEqual(said, "I can't find Targos on any map yet.")  # (no hint it exists)

    async def test_a_player_gets_it_from_a_map_shown_to_the_table(self) -> None:
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        said = await self.ask("how far is Targos from Bryn Shander?", dm=False)
        assert said is not None
        self.assertTrue(said.startswith("About "), said)

    async def test_a_place_hidden_from_the_players_is_not_answered_for_them(self) -> None:
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.bryn, True, source="dm", dm_chose_secret=True
        )
        # (#4692: the table's view says only that a kept place isn't shown, never what or where)
        self.assertIs(
            await self.ask("how far is Targos from Bryn Shander?", dm=False), words.NOT_SHOWN
        )
        self.assertIsNotNone(await self.ask("how far is Targos from Bryn Shander?", dm=True))

    async def test_a_secret_other_name_is_not_shown_and_a_stranger_is_none(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.one.id, self.bryn, "the Hidden Hold", kind="nickname", source="dm",
            secret=True, status="confirmed",
        )  # fmt: skip
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        self.assertIs(
            await self.ask("how far is Targos from the Hidden Hold?", dm=False), words.NOT_SHOWN
        )
        self.assertIsNone(await self.ask("how far is Targos from Luskan?", dm=False))
        self.assertIsNone(await self.ask("how far is the Hidden Hold from Luskan?", dm=False))

    async def test_a_stated_length_to_a_hidden_place_goes_the_usual_way_to_the_dm(self) -> None:
        # (the table's view says NOT_SHOWN; the DM's private re-ask finds the stated length and
        # leaves it to the usual answer, by its own rules)
        await self.memory.add_relation(
            GUILD_A, self.one.id, self.targos, "route_to", self.bryn, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET length_words = 'two days by cart',"
                " length_from = 'dm' WHERE predicate = 'route_to'"
            )
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.bryn, True, source="dm", dm_chose_secret=True
        )
        self.assertIs(
            await self.ask("how far is Targos from Bryn Shander?", dm=False), words.NOT_SHOWN
        )
        self.assertIsNone(await self.ask("how far is Targos from Bryn Shander?", dm=True))

    async def test_names_that_are_not_kept_places_go_the_usual_way(self) -> None:
        self.assertIsNone(await self.ask("how far is Luskan from Targos?", dm=True))

    async def test_with_a_party_the_knowledge_gate_decides(self) -> None:
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        await self.memory.add_entity(
            GUILD_A, self.one.id, name="Cerric", type="character", role="player_character",
            played_by=4242, status="confirmed", source="dm",
        )  # fmt: skip
        self.assertIsNotNone(await self.ask("how far is Targos from Bryn Shander?", dm=False))
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.bryn, True, source="dm", dm_chose_secret=True
        )
        self.assertIs(
            await self.ask("how far is Targos from Bryn Shander?", dm=False), words.NOT_SHOWN
        )

    async def test_a_stated_length_beats_the_measured_one(self) -> None:
        await self.memory.add_relation(
            GUILD_A, self.one.id, self.targos, "route_to", self.bryn, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET length_words = 'two days by cart',"
                " length_from = 'dm' WHERE predicate = 'route_to'"
            )
        self.assertIsNone(await self.ask("how far is Targos from Bryn Shander?", dm=True))
        self.assertIsNotNone(await self.ask("which way is Targos from Bryn Shander?", dm=True))

    async def test_a_map_the_dm_said_no_to_is_never_used(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaign_maps SET use_ok = FALSE")
        self.assertEqual(
            await self.ask("how far is Targos from Bryn Shander?", dm=True),
            "I can't find Targos on any map yet. You can add it on the Maps page.",
        )


class NoPartyYet(PinsBase):  # (no player character: the table's answer is common knowledge only)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        await self.record()

    async def test_a_player_is_not_answered_from_what_only_the_flags_show(self) -> None:
        # (Supervisor's ruling M on #4537: never "everything shown" with nobody to ask, 5c)
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        asked = measure_phrase.read("how far is Targos from Bryn Shander?")
        assert asked is not None
        said = await self.maps.measure(GUILD_A, self.one.id, asked, dm=False)
        self.assertIs(said, words.NOT_SHOWN)  # (both kept places, neither known: to the DM)

    async def test_what_everyone_knows_is_answered(self) -> None:
        # (fails closed to common knowledge, not to nothing)
        await self.maps.set_audience(GUILD_A, self.one.id, self.map_id, "table")
        await self.memory.add_common_knowledge(
            GUILD_A, self.one.id, "entry", [self.targos, self.bryn], source="dm"
        )
        asked = measure_phrase.read("how far is Targos from Bryn Shander?")
        assert asked is not None
        said = await self.maps.measure(GUILD_A, self.one.id, asked, dm=False)
        assert isinstance(said, str)
        self.assertTrue(said.startswith("About "), said)
