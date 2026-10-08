"""Free access (#771): the free list and grants in the plan rule, without a database."""

from __future__ import annotations

import unittest
from pathlib import Path

from dmbot import entitlements, plans
from dmbot.entitlements import FREE_ACCESS, Entitlement, Grant, access_for
from dmbot.plans import PlanId
from tests.test_entitlements_writer import SRC
from tests.test_entitlements_writer import opens_writer as _opens

OWNER, FRIEND, PAYER = 11, 22, 33
NOW = 1_000_000


def paid(plan: PlanId = "table", hours: int = 18, campaigns: int = 1) -> Entitlement:
    return Entitlement(
        PAYER,
        plan,
        "active",
        hours,
        0,
        campaigns,
        0,
        NOW + 100,
        None,
        None,
        0,
    )


class FreeList(unittest.TestCase):
    def tearDown(self) -> None:
        entitlements.configure_free_users(())

    def test_reading_the_setting(self) -> None:
        self.assertEqual(entitlements.parse_free_users(" 11, 22 ,,"), frozenset({11, 22}))
        self.assertEqual(entitlements.parse_free_users(""), frozenset())
        for bad in ("11 22", "abc", "-5", "0", "1" * 25):
            with self.assertRaises(ValueError) as caught:
                entitlements.parse_free_users(bad)
            self.assertNotIn(bad.strip(), str(caught.exception))  # never echoes the setting

    def test_the_log_says_how_many_never_who(self) -> None:
        with self.assertLogs("dmbot.entitlements", "INFO") as logs:
            entitlements.configure_free_users({OWNER, FRIEND})
        self.assertIn("2 ids", logs.output[0])
        self.assertNotIn(str(OWNER), logs.output[0])

    def test_on_the_list_everything_with_no_caps(self) -> None:
        entitlements.configure_free_users({OWNER})
        access = access_for(OWNER, None, None, NOW)
        self.assertEqual(
            (access.kind, access.plan_name, access.hours_cap, access.campaign_cap, access.backups),
            ("free", FREE_ACCESS, None, None, True),
        )
        self.assertTrue(access.works)
        self.assertFalse(access_for(FRIEND, None, None, NOW).works)  # not on it


class Grants(unittest.TestCase):
    def test_like_guild_gives_guilds_caps(self) -> None:
        guild = plans.load().by_id["guild"]
        access = access_for(FRIEND, None, Grant(FRIEND, "guild", None, None), NOW)
        self.assertEqual(access.kind, "grant")
        self.assertEqual(
            (access.hours_cap, access.campaign_cap), (guild.hours_per_month, guild.campaigns)
        )
        self.assertEqual(access.plan_name, FREE_ACCESS)

    def test_no_limits(self) -> None:
        access = access_for(FRIEND, None, Grant(FRIEND, "unlimited", NOW + 5, None), NOW)
        self.assertEqual(
            (access.hours_cap, access.campaign_cap, access.until), (None, None, NOW + 5)
        )

    def test_ended_or_revoked_falls_back_to_the_paid_plan_or_none(self) -> None:
        for grant in (Grant(PAYER, "guild", NOW, None), Grant(PAYER, "guild", None, NOW - 1)):
            self.assertEqual(access_for(PAYER, None, grant, NOW).kind, "none")
            fallback = access_for(PAYER, paid(), grant, NOW)
            self.assertEqual((fallback.kind, fallback.plan_name), ("paid", "Table"))

    def test_a_grant_and_a_paid_plan_give_the_larger_caps(self) -> None:
        guild = plans.load().by_id["guild"]
        small = access_for(PAYER, paid(), Grant(PAYER, "guild", None, None), NOW)
        self.assertEqual(
            (small.hours_cap, small.campaign_cap), (guild.hours_per_month, guild.campaigns)
        )
        big = access_for(PAYER, paid("pro", 500, 50), Grant(PAYER, "guild", None, None), NOW)
        self.assertEqual((big.hours_cap, big.campaign_cap), (500, 50))
        self.assertEqual(big.kind, "grant")  # still free access shown, with the bigger caps

    def test_a_paid_plan_alone(self) -> None:
        access = access_for(PAYER, paid(), None, NOW)
        self.assertEqual((access.kind, access.hours_cap, access.campaign_cap), ("paid", 18, 1))
        lapsed = paid()
        self.assertFalse(access_for(PAYER, lapsed, None, NOW + 10**9).works)


class OnlyTheAdminApiWritesGrants(unittest.TestCase):
    def opens_grant_writer(self, source: str) -> bool:
        import ast

        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "grant_writer"
            for node in ast.walk(ast.parse(source))
        )

    def test_only_dmbot_web_grants_opens_the_grant_writer(self) -> None:
        allowed = SRC / "web" / "grants.py"
        openers = {
            path.relative_to(SRC).as_posix()
            for path in SRC.rglob("*.py")
            if path != allowed and self.opens_grant_writer(path.read_text("utf-8"))
        }
        self.assertEqual(openers, set(), "only dmbot.web.grants may give or revoke free access")
        self.assertTrue(self.opens_grant_writer(allowed.read_text("utf-8")))
        self.assertFalse(_opens(Path(allowed).read_text("utf-8")))  # and never the plan writer


if __name__ == "__main__":
    unittest.main()
