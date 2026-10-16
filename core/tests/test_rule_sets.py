"""Shared rulesets, part 1 (#1190): what a set carries, who may publish, share codes across
servers, following with a copy, and that nothing else of the publisher can be read."""

from __future__ import annotations

import unittest

from dmbot import plan_rules
from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRule, HouseRuleError, HouseRuleStore
from dmbot.rules.rule_sets import RuleSetError, RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B, PLAYER = 11, 22, 33


def rule(number: int, text: str, instead: str | None = None) -> HouseRule:
    return HouseRule(
        number=number, campaign_id="x", rule=text, supersedes=instead, scenario="LEAK: a story",
        session_id="LEAK", created_by=DM_A, created_at=1, updated_at=1, version=1,
    )  # fmt: skip


class Content(unittest.TestCase):
    def test_a_set_carries_rule_text_and_instead_of_and_the_link_only(self) -> None:
        got = rule_sets.content_of(
            [rule(2, "Crits double the dice", "Crits double the damage"), rule(1, "No flanking")],
            "https://example.org/rules",
        )
        self.assertEqual(
            got,
            {
                "rules": [
                    {"number": 1, "rule": "No flanking", "instead": None},
                    {
                        "number": 2,
                        "rule": "Crits double the dice",
                        "instead": "Crits double the damage",
                    },
                ],
                "link": "https://example.org/rules",
                "definitions": [],
            },
        )
        self.assertNotIn("LEAK", repr(got))  # the scenario and the session never travel

    def test_codes_are_long_unguessable_and_forgiving_to_type(self) -> None:
        codes = {rule_sets.new_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)
        for code in codes:
            self.assertEqual(len(code), rule_sets.CODE_LENGTH)
            self.assertTrue(set(code) <= set(rule_sets.CODE_LETTERS))
        self.assertEqual(rule_sets.clean_code(" abcd-efgh 23 "), "ABCDEFGH23")

    def test_sharing_needs_a_paid_plan(self) -> None:
        self.assertEqual(plan_rules.RULE_OF["share"], "backup")
        words = plan_rules.refusal(
            "share", None, is_owner=True, owner_known=True, site_url="https://example.org"
        )
        self.assertIsNotNone(words)
        self.assertNotIn("copies", words or "")


class Sets(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.same = (await self.campaigns.create(GUILD_A, "Sunken City", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(
            GUILD_A,
            self.pub,
            DM_A,
            "Crits double the dice",
            "Crits double the damage",
            scenario="a story",
        )
        await self.houses.add(GUILD_A, self.pub, DM_A, "No flanking")

    async def publish(self) -> rule_sets.SharedSet:
        rules = await self.houses.list(GUILD_A, self.pub)
        return await self.sets.publish(
            GUILD_A, self.pub, DM_A, "Frostmaiden house rules", rules, None
        )

    async def test_only_a_dm_publishes_and_only_with_rules_and_a_name(self) -> None:
        rules = await self.houses.list(GUILD_A, self.pub)
        with self.assertRaises(HouseRuleError):
            await self.sets.publish(GUILD_A, self.pub, PLAYER, "Mine", rules, None)
        with self.assertRaises(RuleSetError):
            await self.sets.publish(GUILD_A, self.pub, DM_A, "  ", rules, None)
        with self.assertRaises(RuleSetError):
            await self.sets.publish(GUILD_A, self.pub, DM_A, "Mine", [], None)
        made = await self.publish()
        self.assertEqual(
            (made.name, made.version, made.shared), ("Frostmaiden house rules", 1, True)
        )
        with self.assertRaises(RuleSetError):
            await self.publish()  # one set for each campaign

    async def test_a_code_opens_the_set_from_another_server_and_following_copies_it(self) -> None:
        await self.publish()
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        offer = await self.sets.by_code(GUILD_B, DM_B, code.lower())
        self.assertEqual(
            (offer.name, offer.publisher, offer.version),
            ("Frostmaiden house rules", "Frostmaiden", 1),
        )
        self.assertEqual(
            [r["rule"] for r in offer.content["rules"]], ["Crits double the dice", "No flanking"]
        )
        self.assertNotIn("a story", repr(offer.content))
        follow = await self.sets.follow(GUILD_B, self.far, DM_B, offer)
        self.assertTrue(follow.updating)
        got = await self.sets.following(GUILD_B, self.far)
        self.assertEqual([f.name for f in got], ["Frostmaiden house rules"])
        with self.assertRaises(RuleSetError):
            await self.sets.follow(GUILD_B, self.far, DM_B, offer)  # one at a time

    async def test_a_wrong_code_refuses_and_too_many_wrong_codes_wait(self) -> None:
        await self.publish()
        for _ in range(rule_sets.GUESSES):
            with self.assertRaises(RuleSetError):
                await self.sets.by_code(GUILD_B, DM_B, "AAAAAAAAAA")
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        with self.assertRaisesRegex(RuleSetError, "Too many"):
            await self.sets.by_code(GUILD_B, DM_B, code)

    async def test_a_turned_off_code_refuses_new_follows_and_keeps_existing_ones(self) -> None:
        await self.publish()
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        offer = await self.sets.by_code(GUILD_B, DM_B, code)
        await self.sets.follow(GUILD_B, self.far, DM_B, offer)
        self.assertTrue(await self.sets.turn_off_code(GUILD_A, self.pub, DM_A, code))
        with self.assertRaises(RuleSetError):
            await self.sets.by_code(GUILD_B, DM_B, code)
        self.assertEqual(len(await self.sets.following(GUILD_B, self.far)), 1)

    async def test_stopping_sharing_stops_new_follows_only(self) -> None:
        await self.publish()
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        await self.sets.follow(
            GUILD_A, self.same, DM_A, (await self.sets.shared_here(GUILD_A, self.same))[0]
        )
        self.assertTrue(await self.sets.stop_sharing(GUILD_A, self.pub, DM_A))
        self.assertEqual(await self.sets.shared_here(GUILD_A, self.same), [])
        with self.assertRaises(RuleSetError):
            await self.sets.by_code(GUILD_B, DM_B, code)
        self.assertEqual(len(await self.sets.following(GUILD_A, self.same)), 1)  # kept

    async def test_the_same_server_picks_from_a_list_and_never_its_own(self) -> None:
        await self.publish()
        self.assertEqual(await self.sets.shared_here(GUILD_A, self.pub), [])
        listed = await self.sets.shared_here(GUILD_A, self.same)
        self.assertEqual([o.name for o in listed], ["Frostmaiden house rules"])
        self.assertEqual(await self.sets.shared_here(GUILD_B, self.far), [])  # another server
        with self.assertRaises(RuleSetError):
            await self.sets.follow(GUILD_A, self.pub, DM_A, listed[0])  # its own

    async def test_unfollow_and_undo(self) -> None:
        await self.publish()
        offer = (await self.sets.shared_here(GUILD_A, self.same))[0]
        await self.sets.follow(GUILD_A, self.same, DM_A, offer)
        gone = await self.sets.unfollow(GUILD_A, self.same, DM_A)
        self.assertEqual(await self.sets.following(GUILD_A, self.same), [])
        await self.sets.restore(GUILD_A, self.same, DM_A, gone)
        self.assertEqual([f.version for f in await self.sets.following(GUILD_A, self.same)], [1])
        with self.assertRaises(RuleSetError):
            await self.sets.restore(GUILD_A, self.same, DM_A, gone)

    async def test_a_follower_reads_the_set_and_nothing_else_of_the_publisher(self) -> None:
        await self.publish()
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        await self.sets.follow(
            GUILD_B, self.far, DM_B, await self.sets.by_code(GUILD_B, DM_B, code)
        )
        async with self.db.guild(GUILD_B) as conn:
            for table in (
                "rule_sets",
                "rule_set_versions",
                "rule_set_codes",
                "house_rules",
                "campaigns",
            ):
                cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
                row = await cur.fetchone()
                self.assertEqual(
                    (table, (row or {})["n"]),
                    (table, 1 if table == "campaigns" else 0),
                )
            # And the door answers nothing for a code that isn't active, in any server.
            cur = await conn.execute("SELECT count(*) AS n FROM dmbot_set_by_code('AAAAAAAAAA')")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
            # The door puts its setting back: nothing stays open after it.
            cur = await conn.execute("SELECT current_setting('dmbot.owner_sync', true) AS s")
            self.assertIn((await cur.fetchone() or {})["s"], (None, ""))

    async def test_stopping_then_sharing_again_gives_a_new_version(self) -> None:
        await self.publish()
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        await self.houses.add(GUILD_A, self.pub, DM_A, "Healing potions are a bonus action")
        again = await self.publish()
        self.assertEqual((again.shared, again.version), (True, 2))
        offer = (await self.sets.shared_here(GUILD_A, self.same))[0]
        self.assertEqual((offer.version, len(offer.content["rules"])), (2, 3))

    async def test_following_reads_the_set_again_so_a_turned_off_set_is_not_copied(self) -> None:
        await self.publish()
        offer = (await self.sets.shared_here(GUILD_A, self.same))[0]
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        with self.assertRaises(RuleSetError):
            await self.sets.follow(GUILD_A, self.same, DM_A, offer)
        self.assertEqual(await self.sets.following(GUILD_A, self.same), [])

    async def test_only_a_dm_makes_codes_stops_sharing_or_follows(self) -> None:
        await self.publish()
        offer = (await self.sets.shared_here(GUILD_A, self.same))[0]
        with self.assertRaises(HouseRuleError):
            await self.sets.make_code(GUILD_A, self.pub, PLAYER)
        with self.assertRaises(HouseRuleError):
            await self.sets.stop_sharing(GUILD_A, self.pub, PLAYER)
        with self.assertRaises(HouseRuleError):
            await self.sets.follow(GUILD_A, self.same, PLAYER, offer)

    async def test_a_copy_that_is_not_a_set_is_refused(self) -> None:
        for bad in ({}, {"rules": "x"}, {"rules": [{"number": 1, "rule": ""}], "link": None},
                    {"rules": [{"number": 1, "rule": "ok", "extra": 1}], "link": 5}):  # fmt: skip
            with self.assertRaises(RuleSetError):
                rule_sets.valid_content(bad)
        got = rule_sets.valid_content(
            {"rules": [{"number": 1, "rule": "ok", "instead": None, "junk": "x"}], "link": None}
        )
        self.assertEqual(got["rules"], [{"number": 1, "rule": "ok", "instead": None}])

    async def test_a_player_pressing_guesses_does_not_lock_out_the_dm(self) -> None:
        await self.publish()
        code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        for _ in range(rule_sets.GUESSES):
            with self.assertRaises(RuleSetError):
                await self.sets.by_code(GUILD_B, PLAYER, "AAAAAAAAAA")
        self.assertEqual(
            (await self.sets.by_code(GUILD_B, DM_B, code)).name, "Frostmaiden house rules"
        )

    async def test_a_campaign_that_does_not_follow_cannot_read_the_set(self) -> None:
        await self.publish()
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rule_set_versions")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        self.assertEqual(await self.sets.following(GUILD_B, self.far), [])
