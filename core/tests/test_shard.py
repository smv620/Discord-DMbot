"""The CI split (#1984) loses no test and runs none twice."""

import pathlib
import unittest

from tests import shard

TESTS = pathlib.Path(__file__).parent


class ParseTest(unittest.TestCase):
    def test_unset_is_the_whole_run(self) -> None:
        self.assertIsNone(shard.parse(None))
        self.assertIsNone(shard.parse(""))

    def test_a_part_and_a_total(self) -> None:
        self.assertEqual(shard.parse("2/3"), (2, 3))

    def test_a_typo_is_an_error_not_a_smaller_run(self) -> None:
        for bad in ("3", "0/3", "4/3", "a/b", "1/0", "1/"):
            with self.subTest(bad), self.assertRaises(ValueError):
                shard.parse(bad)


class SplitTest(unittest.TestCase):
    def test_every_test_file_is_in_exactly_one_part(self) -> None:
        files = sorted(p.name for p in TESTS.glob("test_*.py"))
        self.assertGreater(len(files), 100)  # the real suite, not an empty glob
        for total in (1, 2, 3, 4, 8, 12):
            parts = [
                {f for f in files if shard.part_of(f, total) == n} for n in range(1, total + 1)
            ]
            self.assertEqual(sum(len(p) for p in parts), len(files))
            self.assertEqual(set().union(*parts), set(files))

    def test_four_parts_are_each_a_fair_share(self) -> None:
        files = [p.name for p in TESTS.glob("test_*.py")]
        sizes = [sum(shard.part_of(f, 4) == n for f in files) for n in (1, 2, 3, 4)]
        self.assertGreater(min(sizes), len(files) // 8)

    def test_the_answer_does_not_depend_on_the_path_style(self) -> None:
        self.assertEqual(shard.part_of("a\\b.py", 3), shard.part_of("a/b.py", 3))


class BalanceTest(unittest.TestCase):
    def test_every_measured_file_still_exists(self) -> None:
        missing = sorted(n for n in shard._times() if not (TESTS / n).exists())
        self.assertEqual(missing, [], "renamed or deleted: refresh tests/shard_times.json")

    def test_the_parts_carry_about_the_same_measured_time(self) -> None:
        loads = [0.0] * 12
        for name, seconds in shard._times().items():
            loads[shard.part_of(name, 12) - 1] += seconds
        mean = sum(loads) / 12
        self.assertGreater(mean, 100)  # (the table is the real measurement, not an empty file)
        self.assertLess(max(loads), mean * 1.1)

    def test_a_file_the_table_does_not_know_is_still_in_exactly_one_part(self) -> None:
        part = shard.part_of("test_a_brand_new_file.py", 8)
        self.assertTrue(1 <= part <= 8)
        self.assertEqual(part, shard.part_of("tests/test_a_brand_new_file.py", 8))

    def test_the_log_reader_sums_a_files_seconds(self) -> None:
        from tests import shard_times

        text = (
            "2.50s call     tests/test_a.py::Class::test_one\n"
            "1.25s setup    tests/test_a.py::Class::test_one\n"
            "3.00s call     tests/test_b.py::test_two\n"
        )
        self.assertEqual(shard_times.sum_logs([text]), {"test_a.py": 3.8, "test_b.py": 3.0})
