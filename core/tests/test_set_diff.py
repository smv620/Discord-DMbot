"""Changes between two versions of a shared set, and taking some of them (#1191)."""

from __future__ import annotations

import unittest

from dmbot.rules import set_diff


def content(*rules: tuple[int, str, str | None]) -> dict[str, object]:
    return {
        "rules": [{"number": n, "rule": r, "instead": i} for n, r, i in rules],
        "link": "https://example.org/doc",
    }


OLD = content((1, "Flanking gives advantage", None), (2, "No crits on 1s", "Crit fumbles"))
NEW = content(
    (1, "Flanking gives advantage on attacks", None),
    (3, "Potions are a bonus action", "Drinking takes an action"),
)


class Diffing(unittest.TestCase):
    def test_added_changed_and_removed_by_number(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        self.assertEqual([(c.kind, c.number) for c in changes], [
            ("changed", 1), ("removed", 2), ("added", 3),
        ])  # fmt: skip

    def test_the_same_content_has_no_changes(self) -> None:
        self.assertEqual(set_diff.diff(OLD, OLD), [])

    def test_bad_rows_are_skipped(self) -> None:
        bad = {"rules": [{"number": "x"}, {"rule": "no number"}, 5], "link": None}
        self.assertEqual(set_diff.rules_of(bad), {})
        self.assertEqual(set_diff.rules_of("nonsense"), {})


class Applying(unittest.TestCase):
    def test_all_changes_give_the_new_set(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        self.assertEqual(set_diff.apply(OLD, changes), set_diff.apply(OLD, changes, {1, 2, 3}))
        self.assertEqual(set_diff.rules_of(set_diff.apply(OLD, changes)), set_diff.rules_of(NEW))

    def test_every_change_accepted_equals_following(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        everything = {c.number for c in changes}
        self.assertEqual(set_diff.apply(OLD, changes, everything), set_diff.apply(OLD, changes))

    def test_a_rejected_change_stays_as_it_was(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        merged = set_diff.rules_of(set_diff.apply(OLD, changes, {1, 3}))  # keep rule 2
        self.assertEqual(sorted(merged), [1, 2, 3])
        self.assertEqual(merged[1]["rule"], "Flanking gives advantage on attacks")
        self.assertEqual(merged[2]["rule"], "No crits on 1s")

    def test_nothing_accepted_is_the_old_set_and_the_old_is_not_changed(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        self.assertEqual(
            set_diff.rules_of(set_diff.apply(OLD, changes, set())), set_diff.rules_of(OLD)
        )
        self.assertEqual(len(set_diff.rules_of(OLD)), 2)

    def test_the_link_is_kept(self) -> None:
        self.assertEqual(set_diff.apply(OLD, [])["link"], "https://example.org/doc")


class Words(unittest.TestCase):
    def test_lines_are_short_and_plain(self) -> None:
        changes = set_diff.diff(OLD, NEW)
        self.assertEqual(set_diff.describe(changes[1]), "Removed: rule 2, No crits on 1s")
        self.assertTrue(set_diff.describe(changes[0], 20).endswith("…"))
        self.assertEqual(set_diff.count_words(changes[:1]), "1 rule")
        self.assertEqual(set_diff.count_words(changes), "3 rules")


if __name__ == "__main__":
    unittest.main()
