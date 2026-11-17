"""When the DM's word and a map disagree (#4670, MapBot slice 4c; docs/design/mapbot_layers.md
§9): the 20% and 45° limits, a straight line flagged only when the stated distance is shorter, north
only when read or set, and the map an answer would use. Invented names; no database."""

from __future__ import annotations

import unittest

from dmbot.maps import disagree, measure
from tests.test_map_measure import a_map, pins


class TheLimits(unittest.TestCase):
    def test_the_sixteen_points(self) -> None:
        self.assertEqual(disagree.angle_of("north"), 0.0)
        for said in ("north-east", "north east", "Northeast", "NORTH-EAST"):
            self.assertEqual(disagree.angle_of(said), 45.0, said)
        self.assertEqual(disagree.angle_of("east-north-east"), 67.5)
        self.assertEqual(disagree.angle_of("north-north-west"), 337.5)
        self.assertIsNone(disagree.angle_of("up"))
        self.assertIsNone(disagree.angle_of("northward bound"))

    def test_only_lengths_are_compared(self) -> None:
        self.assertEqual(disagree.in_miles(20, "miles"), 20)
        self.assertEqual(disagree.in_miles(5280, "feet"), 1.0)
        self.assertIsNone(disagree.in_miles(2, "days"))  # (a time is never a conflict)
        self.assertIsNone(disagree.in_miles(3, "hours"))

    def test_a_straight_line_flags_only_a_shorter_stated_distance(self) -> None:
        self.assertTrue(disagree.distance_off(20, 35, straight=True))
        self.assertFalse(disagree.distance_off(29, 35, straight=True))  # (within 20%)
        self.assertFalse(disagree.distance_off(28, 35, straight=True))  # (exactly 20%: silence)
        self.assertFalse(disagree.distance_off(60, 35, straight=True))  # (a road is longer)

    def test_a_traced_path_flags_either_way(self) -> None:
        self.assertTrue(disagree.distance_off(20, 35, straight=False))
        self.assertTrue(disagree.distance_off(50, 35, straight=False))
        self.assertFalse(disagree.distance_off(40, 35, straight=False))

    def test_45_degrees_or_more_is_flagged(self) -> None:
        # (the design's example: the map says east-north-east, 67.5°)
        self.assertFalse(disagree.direction_off(45.0, 67.5))  # north-east: fine
        self.assertFalse(disagree.direction_off(90.0, 67.5))  # east: fine
        self.assertTrue(disagree.direction_off(0.0, 67.5))  # north: flagged
        self.assertTrue(disagree.direction_off(0.0, 45.0))  # exactly 45°: flagged
        self.assertFalse(disagree.direction_off(350.0, 20.0))  # (across north: 30° apart)
        self.assertEqual(disagree.apart(350.0, 20.0), 30.0)


class TheMaps(unittest.TestCase):
    def test_a_stated_distance_far_shorter_than_the_map(self) -> None:
        # (the map makes a to b 40 miles in a straight line)
        found = disagree.check_distance([a_map()], pins(), "a", "b", 20)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.map.id, "m1")
        self.assertAlmostEqual(found.measured_miles, 40.0)
        self.assertEqual(found.stated_miles, 20)
        self.assertIsNone(disagree.check_distance([a_map()], pins(), "a", "b", 35))
        self.assertIsNone(disagree.check_distance([a_map()], pins(), "a", "b", 90))

    def test_no_scale_or_no_shared_map_is_silence(self) -> None:
        self.assertIsNone(disagree.check_distance([a_map(per_px=None)], pins(), "a", "b", 1))
        apart = [measure.Pin("m1", "a", 0.5, 0.1), measure.Pin("m2", "b", 0.5, 0.5)]
        two = [a_map(), a_map(id="m2")]
        self.assertIsNone(disagree.check_distance(two, apart, "a", "b", 1))

    def test_a_feet_map_is_compared_in_miles(self) -> None:
        town = a_map(kind="settlement", per_px=10.0, unit="feet")  # (4,000 feet: about 0.76 mile)
        self.assertIsNotNone(disagree.check_distance([town], pins(), "a", "b", 0.5))
        self.assertIsNone(disagree.check_distance([town], pins(), "a", "b", 0.7))

    def test_the_map_an_answer_would_use(self) -> None:
        # (the town map is more detailed: it answers, and it agrees with the stated distance)
        town = a_map(id="t", kind="settlement", per_px=0.05)  # (20 miles on this one)
        both = pins() + pins("t")
        self.assertIsNone(disagree.check_distance([a_map(), town], both, "a", "b", 19))

    def test_a_direction_far_off(self) -> None:
        # (a is due north of b on the map)
        found = disagree.check_direction([a_map()], pins(), "a", "b", 90.0)  # "a is east of b"
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.map_way, "north")
        self.assertIsNone(disagree.check_direction([a_map()], pins(), "a", "b", 22.5))

    def test_a_north_assumed_is_never_compared(self) -> None:
        self.assertIsNone(
            disagree.check_direction([a_map(north_known=False)], pins(), "a", "b", 180.0)
        )
