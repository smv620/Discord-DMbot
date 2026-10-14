"""Suggest to <set> (#1259): a following campaign sends one of its own rules to the publisher.
Needs Postgres, like the other store tests; CI runs them. The point of most of these is what
does NOT cross: the publisher's side never holds who sent a suggestion."""

from __future__ import annotations

from unittest import mock

from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRuleError, HouseRuleStore
from dmbot.rules.rule_sets import RuleSetError, RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B, GUILD_C = 1001, 2002, 3003
DM_A, DM_B, DM_C, PLAYER = 11, 22, 55, 33


class Suggestions(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(GUILD_A, self.pub, DM_A, "Crits double the dice")
        await self.houses.add(GUILD_A, self.pub, DM_A, "No flanking")
        rules = await self.houses.list(GUILD_A, self.pub)
        await self.sets.publish(GUILD_A, self.pub, DM_A, "Frostmaiden", rules, None)
        self.code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        offer = await self.sets.by_code(GUILD_B, DM_B, self.code)
        await self.sets.follow(GUILD_B, self.far, DM_B, offer)

    async def own_rule(self, text: str, instead: str | None = None) -> int:
        rule = await self.houses.add(GUILD_B, self.far, DM_B, text, instead)
        return rule.number

    async def test_a_suggestion_lands_on_the_publisher_and_names_nobody(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, guild, campaign = await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        self.assertEqual((guild, campaign), (GUILD_A, self.pub))
        got = await self.sets.suggestion(GUILD_A, self.pub, sid)
        assert got is not None
        self.assertEqual(got.rule, "Potions are a bonus action")
        self.assertEqual(got.status, "open")
        # Nothing on the publisher's side says who sent it: not the server, campaign or person.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM rule_set_suggestions")
            rows = await cur.fetchall()
        text = repr(rows)
        for secret in (str(GUILD_B), self.far, str(DM_B), "Far Away"):
            self.assertNotIn(secret, text)
        # And the publisher's server can't see the follower's own record.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rule_set_suggestions_sent")
            self.assertEqual((await cur.fetchone())["n"], 0)  # type: ignore[index]

    async def test_accept_makes_the_next_version_and_a_replacement_keeps_its_number(self) -> None:
        added = await self.own_rule("Potions are a bonus action")
        swap = await self.own_rule("Crits max the dice", "Crits double the dice")
        a, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, added)
        b, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, swap)
        made = await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, a, plan_ok=True)
        self.assertEqual(made.version, 2)
        made = await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, b, plan_ok=True)
        self.assertEqual(made.version, 3)
        got = await self.sets.current_content(GUILD_A, self.pub)
        assert got is not None
        rules = {r["number"]: r["rule"] for r in got[1]["rules"]}
        self.assertEqual(rules[3], "Potions are a bonus action")  # added as the next number
        self.assertEqual(rules[1], "Crits max the dice")  # replaced in place
        log = await self.sets.version_log(GUILD_A, self.pub)
        self.assertEqual(
            [(v.version, v.source) for v in log[:2]], [(3, "suggestion"), (2, "suggestion")]
        )
        answered = await self.sets.suggestion(GUILD_A, self.pub, a)
        assert answered is not None
        self.assertEqual(answered.status, "accepted")
        with self.assertRaises(RuleSetError) as err:  # answered once only
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, a, plan_ok=True)
        self.assertEqual(str(err.exception), rule_sets.SUGGESTION_GONE)

    async def test_no_thanks_changes_nothing(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        self.assertTrue(await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertFalse(await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertEqual((await self.sets.published(GUILD_A, self.pub)).version, 1)  # type: ignore[union-attr]
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)

    async def test_only_the_publishers_dms_answer_and_the_plan_must_be_paid(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(GUILD_A, self.pub, PLAYER, sid, plan_ok=True)
        with self.assertRaises(HouseRuleError):
            await self.sets.decline_suggestion(GUILD_A, self.pub, PLAYER, sid)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=False)
        self.assertEqual(str(err.exception), rule_sets.NOT_PAID)
        # The follower's own DM can't answer it either: its server holds no such row.
        self.assertIsNone(await self.sets.suggestion(GUILD_B, self.far, sid))
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(GUILD_B, self.far, DM_B, sid, plan_ok=True)

    async def test_the_version_the_publisher_saw_is_checked(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.accept_suggestion(
                GUILD_A, self.pub, DM_A, sid, plan_ok=True, expected_version=7
            )
        self.assertEqual(str(err.exception), rule_sets.CHANGED_AGAIN)

    async def test_a_player_cannot_suggest_and_only_a_following_campaign_can(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        with self.assertRaises(HouseRuleError):
            await self.sets.suggest(GUILD_B, self.far, PLAYER, number)
        other = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        await self.houses.add(GUILD_C, other, DM_C, "A rule")
        with self.assertRaises(RuleSetError) as err:
            await self.sets.suggest(GUILD_C, other, DM_C, 1)
        self.assertEqual(str(err.exception), rule_sets.NOT_FOLLOWING)

    async def test_the_door_refuses_a_set_the_caller_does_not_follow(self) -> None:
        other = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        published = await self.sets.published(GUILD_A, self.pub)
        assert published is not None
        async with self.db.guild(GUILD_C) as conn:
            cur = await conn.execute(
                "SELECT outcome FROM dmbot_suggest_rule(%s, %s, %s, %s, NULL, 1, 20)",
                ("a" * 32, published.id, other, "Sneaky rule"),
            )
            self.assertEqual((await cur.fetchone())["outcome"], "gone")  # type: ignore[index]

    async def test_a_rule_that_came_from_a_set_is_not_sent_back(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        async with self.db.guild(GUILD_B) as conn:
            await conn.execute(
                "UPDATE house_rules SET from_set = 'Frostmaiden house rules, v1'"
                " WHERE guild_id = %s AND number = %s",
                (GUILD_B, number),
            )
        with self.assertRaises(RuleSetError) as err:
            await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        self.assertEqual(str(err.exception), rule_sets.NOT_YOURS_TO_SEND)

    async def test_limits_per_follower_per_set_and_for_the_same_rule(self) -> None:
        numbers = [await self.own_rule(f"Rule number {n}") for n in range(6)]
        for number in numbers[:5]:
            await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.suggest(GUILD_B, self.far, DM_B, numbers[5])
        self.assertEqual(str(err.exception), rule_sets.TOO_MANY_SUGGESTIONS)
        # The same words twice, while the first still waits, are refused (checked first).
        with self.assertRaises(RuleSetError) as err2:
            await self.sets.suggest(GUILD_B, self.far, DM_B, numbers[0])
        self.assertEqual(str(err2.exception), rule_sets.ALREADY_SUGGESTED)

    async def test_the_set_holds_only_so_many_open_suggestions(self) -> None:
        second = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        await self.sets.follow(
            GUILD_C, second, DM_C, await self.sets.by_code(GUILD_C, DM_C, self.code)
        )
        first_number = await self.own_rule("From the first table")
        await self.houses.add(GUILD_C, second, DM_C, "From the second table")
        with mock.patch.object(rule_sets, "SUGGEST_SET_MAX", 1):
            await self.sets.suggest(GUILD_B, self.far, DM_B, first_number)
            with self.assertRaises(RuleSetError) as err:
                await self.sets.suggest(GUILD_C, second, DM_C, 1)
        self.assertEqual(str(err.exception), rule_sets.SET_HAS_ENOUGH)

    async def test_the_follower_hears_the_answer_once_and_the_slot_frees(self) -> None:
        a = await self.own_rule("Potions are a bonus action")
        b = await self.own_rule("No help from the bard")
        c = await self.own_rule("Still waiting")
        sid_a, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, a)
        sid_b, _, _ = await self.sets.suggest(GUILD_B, self.far, DM_B, b)
        await self.sets.suggest(GUILD_B, self.far, DM_B, c)
        await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid_a, plan_ok=True)
        await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid_b)
        answers = {s.rule: s for s in await self.sets.sent_answers(GUILD_B, self.far)}
        self.assertEqual(answers["Potions are a bonus action"].status, "accepted")
        self.assertEqual(answers["Potions are a bonus action"].version, 2)
        self.assertEqual(answers["No help from the bard"].status, "declined")
        self.assertEqual(answers["Still waiting"].status, "open")
        await self.sets.mark_told(GUILD_B, self.far, [sid_a, sid_b])
        left = await self.sets.sent_answers(GUILD_B, self.far)
        self.assertEqual([s.rule for s in left], ["Still waiting"])

    async def test_a_set_that_is_gone_ends_the_wait(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        await self.sets.suggest(GUILD_B, self.far, DM_B, number)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM rule_sets WHERE guild_id = %s", (GUILD_A,))
        answers = await self.sets.sent_answers(GUILD_B, self.far)
        self.assertEqual([s.status for s in answers], ["declined"])
