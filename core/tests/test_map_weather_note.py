"""The after-session weather note (#4260, MapBot layers slice 10c): only weather that plainly can't
fit a kept climate is noted, and the DM's words. Invented names."""

from __future__ import annotations

import unittest
import uuid

from dmbot.maps import weather_note as wn
from dmbot.memory import map_facts as mf


def climate(
    value: str, status: str = mf.CONFIRMED, *, part: str = "", layer: str = mf.CLIMATE
) -> mf.Fact:
    return mf.Fact(
        id=uuid.uuid4().hex, place_id="p" * 32, layer=layer, value=value, words="", how="",
        part=part, status=status, source="dm", table_knows=False,
    )  # fmt: skip


class Fits(unittest.TestCase):
    def test_always_wrong_whatever_the_season(self) -> None:
        for c, kind in (("arctic", wn.HEAT), ("hot", wn.SNOW)):
            for season in (None, *wn.SEASONS):
                with self.subTest(c=c, kind=kind, season=season):
                    self.assertFalse(wn.fits(c, kind, season))

    def test_season_bound_pairs_need_the_season(self) -> None:
        self.assertTrue(wn.fits("temperate", wn.SNOW, None))  # (no season: never noted)
        self.assertTrue(wn.fits("temperate", wn.SNOW, "winter"))
        self.assertFalse(wn.fits("temperate", wn.SNOW, "summer"))
        self.assertFalse(wn.fits("temperate", wn.HEAT, "winter"))
        self.assertTrue(wn.fits("cold", wn.HEAT, "summer"))
        self.assertFalse(wn.fits("cold", wn.HEAT, "winter"))
        self.assertTrue(wn.fits("warm", wn.SNOW, "winter"))
        self.assertFalse(wn.fits("warm", wn.SNOW, "spring"))
        self.assertTrue(wn.fits("warm", wn.FROST, "spring"))  # (Mediterranean frosts)
        self.assertTrue(wn.fits("warm", wn.FROST, "autumn"))
        self.assertFalse(wn.fits("warm", wn.FROST, "summer"))
        self.assertTrue(wn.fits("hot", wn.FROST, "winter"))  # (a desert's winter nights)
        self.assertFalse(wn.fits("hot", wn.FROST, "summer"))

    def test_cold_fits_cold_and_unknown_words_fit(self) -> None:
        self.assertTrue(wn.fits("arctic", wn.SNOW, "summer"))
        self.assertTrue(wn.fits("arctic", "rain", None))
        self.assertTrue(wn.fits("somewhere", wn.HEAT, "winter"))


class Check(unittest.TestCase):
    def test_heat_in_a_freezing_place(self) -> None:
        note = wn.check("Bryn Shander", wn.HEAT, [(climate("arctic"), "Icewind Dale")], "winter")
        self.assertEqual(note, wn.Note("Bryn Shander", wn.HEAT, "arctic", None, "Icewind Dale"))

    def test_the_season_is_kept_when_it_decided(self) -> None:
        note = wn.check("Waterdeep", wn.SNOW, [(climate("temperate"), None)], "summer")
        self.assertEqual(note, wn.Note("Waterdeep", wn.SNOW, "temperate", "summer", None))

    def test_an_unkept_climate_never_makes_a_note(self) -> None:
        for status in (mf.READ, mf.REJECTED):
            with self.subTest(status=status):
                only = [(climate("arctic", status), None)]
                self.assertIsNone(wn.check("Bryn Shander", wn.HEAT, only))

    def test_another_layers_row_is_not_a_climate(self) -> None:
        land = [(climate("arctic", layer=mf.TERRAIN), None)]
        self.assertIsNone(wn.check("Bryn Shander", wn.HEAT, land))

    def test_two_different_kept_climates_are_silence(self) -> None:
        for other in ("hot", "temperate"):  # (one fits, or neither does: DMbot doesn't pick)
            with self.subTest(other=other):
                both = [(climate("arctic"), "Icewind Dale"), (climate(other), None)]
                self.assertIsNone(wn.check("Bryn Shander", wn.HEAT, both, "winter"))

    def test_the_same_climate_twice_is_one_note(self) -> None:
        twice = [(climate("arctic"), None), (climate("arctic"), "Icewind Dale")]
        note = wn.check("Bryn Shander", wn.HEAT, twice)
        self.assertEqual(note, wn.Note("Bryn Shander", wn.HEAT, "arctic", None, None))

    def test_the_dms_own_words_are_silence(self) -> None:
        own = [(climate("arctic"), "Icewind Dale"), (climate(mf.OTHER), None)]
        self.assertIsNone(wn.check("Bryn Shander", wn.HEAT, own))

    def test_a_climate_for_part_of_the_place_is_silence(self) -> None:
        peaks = [(climate("arctic", part="the northern peaks"), None)]
        self.assertIsNone(wn.check("The Spine", wn.HEAT, peaks))

    def test_rain_no_climate_and_an_unknown_season_say_nothing(self) -> None:
        self.assertIsNone(wn.check("Bryn Shander", "rain", [(climate("hot"), None)]))
        self.assertIsNone(wn.check("Bryn Shander", wn.HEAT, []))
        mild = [(climate("temperate"), None)]
        self.assertIsNone(wn.check("Waterdeep", wn.SNOW, mild, "monsoon"))
        self.assertIsNone(wn.check("Waterdeep", wn.SNOW, mild, "Summer"))  # (the calendar's words)


class Words(unittest.TestCase):
    def test_read_up_from_a_region(self) -> None:
        note = wn.Note("Bryn Shander", wn.HEAT, "arctic", None, "Icewind Dale")
        self.assertEqual(
            wn.words(note),
            "🌦️ Weather check: hot weather in Bryn Shander this session, but it's in Icewind Dale,"
            " whose climate is freezing. If that's on purpose, like magic or a story turn, you can"
            " ignore this.",
        )

    def test_its_own_climate_and_the_season(self) -> None:
        note = wn.Note("Baldur's Gate", wn.SNOW, "temperate", "summer", None)
        self.assertEqual(
            wn.words(note),
            "🌦️ Weather check: snow in Baldur's Gate this session, but the climate there is mild,"
            " and it's summer there by your calendar. If that's on purpose, like magic or a story"
            " turn, you can ignore this.",
        )

    def test_every_kind_and_climate_has_words(self) -> None:
        self.assertEqual(set(wn.SAID), set(wn.KINDS))
        self.assertEqual(set(wn.CLIMATE_SAID), set(mf.VALUES[mf.CLIMATE]))
