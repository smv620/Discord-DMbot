"""The website's "House rule updates" (#4841 C1, migration 0561): only a DM reads it, it says
nothing when nothing newer waits, it gives core's own lines for what a newer version of the
followed set changed (worked out by `rules.set_diff`, as Discord's note), a stopped set or a follow
that no longer updates gives none, and a share code never leaves. Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.rules import set_diff
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_rule_sharing import SET_A, SharingBase

OLD = {"rules": [{"number": 1, "rule": "Crits double", "instead": None}], "link": None}
NEW = {
    "rules": [
        {"number": 1, "rule": "Crits double the dice", "instead": None},
        {"number": 2, "rule": "No flanking", "instead": None},
    ],
    "link": None,
}


class RuleUpdates(SharingBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.publish(self.two.id, SET_A, "Frost Rules")  # (versions 1 and 2, same words)
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(NEW), SET_A,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, followed_by, followed_at)"
            " VALUES (%s, %s, %s, 'house_rules', 'Frost Rules', 'Two', 1, %s::jsonb, true, %s, 5)",
            THURSDAY.id, self.one.id, SET_A, json.dumps(OLD), ALICE.id,
        )  # fmt: skip

    async def update(self, session: Any = None) -> Any:
        from dmbot.web import rule_updates

        got = await rule_updates.read(self.web, session or self.session, self.cref())
        return got["update"]

    async def test_a_newer_version_gives_core_s_own_lines(self) -> None:
        got = await self.update()
        rules = set_diff.diff(OLD, NEW)
        self.assertEqual(
            got,
            {
                "name": "Frost Rules", "publisher": "Two", "from": 1, "to": 2,
                "words": set_diff.count_words(rules),
                "lines": [set_diff.describe(c) for c in rules], "more": 0,
            },
        )  # fmt: skip
        self.assertEqual(got["words"], "2 rules")

    async def test_nothing_waits_when_the_follow_is_on_the_latest_or_stopped_updating(self) -> None:
        await self.sql("UPDATE rule_set_follows SET version = 2")
        self.assertIsNone(await self.update())
        await self.sql("UPDATE rule_set_follows SET version = 1, updating = false")
        self.assertIsNone(await self.update())

    async def test_a_set_that_stopped_sharing_gives_nothing(self) -> None:
        await self.sql("UPDATE rule_sets SET status = 'unshared' WHERE id = %s", SET_A)
        self.assertIsNone(await self.update())

    async def test_a_campaign_following_nothing_gets_nothing(self) -> None:
        await self.sql("DELETE FROM rule_set_follows")
        self.assertIsNone(await self.update())

    async def test_a_follow_by_share_code_reads_through_the_one_guarded_function(self) -> None:
        await self.code(self.two.id, SET_A)
        await self.sql("UPDATE rule_set_follows SET code = %s", "ABCDEFGHJK")
        got = await self.update()
        assert got is not None
        self.assertEqual((got["from"], got["to"]), (1, 2))
        self.assertNotIn("ABCDEFGHJK", json.dumps(got))  # (the code never leaves)

    async def test_only_a_dm_of_the_campaign_reads_it(self) -> None:
        bob = await self.signed(BOB)
        with self.assertRaises(RecapError) as err:
            await self.update(bob)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_long_list_is_cut_and_the_rest_counted(self) -> None:
        many = {
            "rules": [{"number": n, "rule": f"Rule {n}", "instead": None} for n in range(1, 61)]
        }
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(many), SET_A,
        )  # fmt: skip
        got = await self.update()
        assert got is not None
        self.assertEqual((len(got["lines"]), got["more"]), (50, 10))
