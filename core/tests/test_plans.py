"""The plan facts (#432, #437): the owner's numbers, and the copy the website uses."""

import json
import re
import unittest
from importlib import resources
from pathlib import Path
from typing import get_args

from dmbot import plans, schema

WEB_COPY = Path(__file__).resolve().parents[2] / "web" / "src" / "content" / "plans.json"


class OwnerNumbers(unittest.TestCase):
    # Owner decisions. If this fails, the change needs the owner's say-so on #432 first.
    def test_prices_hours_and_caps(self) -> None:
        p = plans.load()
        table = [
            (x.id, x.price_cents, x.hours_per_month, x.campaigns)
            for x in (p.by_id[i] for i in p.order)
        ]
        self.assertEqual(
            table,
            [
                ("try-it", 0, 8, 1),
                ("table", 899, 18, 1),
                ("two-tables", 1799, 43, 2),
                ("guild", 3499, 87, 5),
                ("pro", None, 217, 20),
            ],
        )
        self.assertEqual((p.extra_hours, p.extra_hours_price_cents), (10, 499))
        self.assertEqual(p.payment_grace_days, 7)

    def test_retention(self) -> None:
        p = plans.load()
        self.assertEqual(
            [p.by_id[i].keep_after_last_session.days for i in p.order], [60, 180, 365, 365, 365]
        )
        self.assertEqual(p.keep_after_plan_stops_paying.days, 120)
        self.assertEqual(p.deletion_warning_days_before, (14, 3))

    def test_unknown_plan_ids_are_refused(self) -> None:
        self.assertIsNone(plans.load().get("free-forever"))
        self.assertEqual(plans.load().get("guild").name, "Guild")  # type: ignore[union-attr]


class OneListOfPlanIds(unittest.TestCase):
    def test_json_type_and_database_agree(self) -> None:
        ids = list(plans.load().order)
        self.assertEqual(ids, list(get_args(plans.PlanId)))
        check = re.search(
            r"plan\s+TEXT NOT NULL\s+CHECK \(plan IN \(([^)]*)\)\)", schema.WEB_ACCOUNTS
        )
        assert check is not None
        self.assertEqual(re.findall(r"'([^']+)'", check.group(1)), ids)


class SameAsTheWebsite(unittest.TestCase):
    @unittest.skipUnless(WEB_COPY.exists(), "web/ is not in this checkout yet (#446)")
    def test_core_and_website_copies_are_identical(self) -> None:
        core = json.loads(resources.files("dmbot").joinpath("plans.json").read_text("utf-8"))
        web = json.loads(WEB_COPY.read_text("utf-8"))
        self.assertEqual(core, web, "core/src/dmbot/plans.json and the website's copy differ")
