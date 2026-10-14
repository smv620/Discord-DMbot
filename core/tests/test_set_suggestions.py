"""Suggest to <set> (#1259): a following campaign sends one of its own rules to the publisher.
Needs Postgres, like the other store tests; CI runs them. The point of most of these is what
does NOT cross: the publisher's side never holds who sent a suggestion."""

from __future__ import annotations

import asyncio
from unittest import mock

from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRuleError, HouseRuleStore
from dmbot.rules.rule_sets import RuleSetError, RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B, GUILD_C = 1001, 2002, 3003
DM_A, DM_B, DM_C, PLAYER = 11, 22, 55, 33


class _Base(DatabaseTest):
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

    async def send(
        self, guild: int, campaign: str, user: int, number: int, version: int | None = None
    ) -> tuple[str, int, str]:
        """Suggest a rule as the DM was shown it (its current version unless one is given)."""
        if version is None:
            rules = {r.number: r for r in await self.houses.list(guild, campaign)}
            version = rules[number].version if number in rules else 1
        return await self.sets.suggest(guild, campaign, user, number, version)

    async def own_rule(self, text: str, instead: str | None = None) -> int:
        rule = await self.houses.add(GUILD_B, self.far, DM_B, text, instead)
        return rule.number


class Suggestions(_Base):
    async def test_a_suggestion_lands_on_the_publisher_and_names_nobody(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, guild, campaign = await self.send(GUILD_B, self.far, DM_B, number)
        self.assertEqual((guild, campaign), (GUILD_A, self.pub))
        got = await self.sets.suggestion(GUILD_A, self.pub, sid)
        assert got is not None
        self.assertEqual(got.rule, "Potions are a bonus action")
        self.assertEqual(got.status, "open")
        # Nothing on the publisher's side says who sent it: not the server, campaign or person.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM rule_set_suggestions")
            rows = await cur.fetchall()
        values = list(rows[0].values())
        for secret in (GUILD_B, self.far, DM_B, "Far Away"):
            self.assertNotIn(secret, values)
        self.assertFalse(any(isinstance(v, str) and "Far Away" in v for v in values))
        # And the publisher's server can't see the follower's own record.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rule_set_suggestions_sent")
            self.assertEqual((await cur.fetchone())["n"], 0)  # type: ignore[index]

    async def test_accept_makes_the_next_version_and_a_replacement_keeps_its_number(self) -> None:
        added = await self.own_rule("Potions are a bonus action")
        swap = await self.own_rule("Crits max the dice", "Crits double the dice")
        a, _, _ = await self.send(GUILD_B, self.far, DM_B, added)
        b, _, _ = await self.send(GUILD_B, self.far, DM_B, swap)
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
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        self.assertTrue(await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertFalse(await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertEqual((await self.sets.published(GUILD_A, self.pub)).version, 1)  # type: ignore[union-attr]
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)

    async def test_only_the_publishers_dms_answer_and_the_plan_must_be_paid(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
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
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.accept_suggestion(
                GUILD_A, self.pub, DM_A, sid, plan_ok=True, expected_version=7
            )
        self.assertEqual(str(err.exception), rule_sets.CHANGED_AGAIN)

    async def test_a_player_cannot_suggest_and_only_a_following_campaign_can(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        with self.assertRaises(HouseRuleError):
            await self.send(GUILD_B, self.far, PLAYER, number)
        other = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        await self.houses.add(GUILD_C, other, DM_C, "A rule")
        with self.assertRaises(RuleSetError) as err:
            await self.send(GUILD_C, other, DM_C, 1)
        self.assertEqual(str(err.exception), rule_sets.NOT_FOLLOWING)

    async def test_the_door_refuses_a_set_the_caller_does_not_follow(self) -> None:
        other = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        published = await self.sets.published(GUILD_A, self.pub)
        assert published is not None
        async with self.db.guild(GUILD_C) as conn:
            cur = await conn.execute(
                "SELECT outcome FROM dmbot_suggest_rule(%s, %s, %s, %s, NULL, 1, 20, 2592000)",
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
            await self.send(GUILD_B, self.far, DM_B, number)
        self.assertEqual(str(err.exception), rule_sets.NOT_YOURS_TO_SEND)

    async def test_limits_per_follower_per_set_and_for_the_same_rule(self) -> None:
        numbers = [await self.own_rule(f"Rule number {n}") for n in range(6)]
        for number in numbers[:5]:
            await self.send(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError) as err:
            await self.send(GUILD_B, self.far, DM_B, numbers[5])
        self.assertEqual(str(err.exception), rule_sets.TOO_MANY_SUGGESTIONS)
        # The same words twice, while the first still waits, are refused (checked first).
        with self.assertRaises(RuleSetError) as err2:
            await self.send(GUILD_B, self.far, DM_B, numbers[0])
        self.assertEqual(str(err2.exception), rule_sets.ALREADY_SUGGESTED)

    async def test_the_set_holds_only_so_many_open_suggestions(self) -> None:
        second = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        await self.sets.follow(
            GUILD_C, second, DM_C, await self.sets.by_code(GUILD_C, DM_C, self.code)
        )
        first_number = await self.own_rule("From the first table")
        await self.houses.add(GUILD_C, second, DM_C, "From the second table")
        with mock.patch.object(rule_sets, "SUGGEST_SET_MAX", 1):
            await self.send(GUILD_B, self.far, DM_B, first_number)
            with self.assertRaises(RuleSetError) as err:
                await self.send(GUILD_C, second, DM_C, 1)
        self.assertEqual(str(err.exception), rule_sets.SET_HAS_ENOUGH)

    async def test_the_follower_hears_the_answer_once_and_the_slot_frees(self) -> None:
        a = await self.own_rule("Potions are a bonus action")
        b = await self.own_rule("No help from the bard")
        c = await self.own_rule("Still waiting")
        sid_a, _, _ = await self.send(GUILD_B, self.far, DM_B, a)
        sid_b, _, _ = await self.send(GUILD_B, self.far, DM_B, b)
        await self.send(GUILD_B, self.far, DM_B, c)
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
        await self.send(GUILD_B, self.far, DM_B, number)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM rule_sets WHERE guild_id = %s", (GUILD_A,))
        answers = await self.sets.sent_answers(GUILD_B, self.far)
        self.assertEqual([s.status for s in answers], ["gone"])


class AtTheSameTime(_Base):
    """Two presses or two tables at once take turns (the lock is on the campaign / the set)."""

    async def test_the_same_suggestion_accepted_twice_is_added_once(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        results = await asyncio.gather(
            self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True),
            self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, rule_sets.SharedSet) for r in results), 1)
        self.assertEqual(sum(isinstance(r, RuleSetError) for r in results), 1)
        got = await self.sets.current_content(GUILD_A, self.pub)
        assert got is not None
        self.assertEqual(got[0].version, 2)
        words = [r["rule"] for r in got[1]["rules"]]
        self.assertEqual(words.count("Potions are a bonus action"), 1)

    async def test_two_different_suggestions_make_two_versions(self) -> None:
        a = await self.own_rule("Rule from a")
        b = await self.own_rule("Rule from b")
        sa, _, _ = await self.send(GUILD_B, self.far, DM_B, a)
        sb, _, _ = await self.send(GUILD_B, self.far, DM_B, b)
        await asyncio.gather(
            self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sa, plan_ok=True),
            self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sb, plan_ok=True),
        )
        got = await self.sets.current_content(GUILD_A, self.pub)
        assert got is not None
        self.assertEqual(got[0].version, 3)
        words = {r["rule"] for r in got[1]["rules"]}
        self.assertTrue({"Rule from a", "Rule from b"} <= words)

    async def test_accept_and_no_thanks_together_leave_one_answer(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        await asyncio.gather(
            self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True),
            self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sid),
            return_exceptions=True,
        )
        got = await self.sets.suggestion(GUILD_A, self.pub, sid)
        assert got is not None
        current = await self.sets.current_content(GUILD_A, self.pub)
        assert current is not None
        added = any(r["rule"] == "Potions are a bonus action" for r in current[1]["rules"])
        self.assertEqual(got.status == "accepted", added)  # what it says is what happened

    async def test_two_tables_at_once_cannot_pass_the_set_cap(self) -> None:
        second = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        await self.sets.follow(
            GUILD_C, second, DM_C, await self.sets.by_code(GUILD_C, DM_C, self.code)
        )
        first_number = await self.own_rule("From the first table")
        await self.houses.add(GUILD_C, second, DM_C, "From the second table")
        with mock.patch.object(rule_sets, "SUGGEST_SET_MAX", 1):
            results = await asyncio.gather(
                self.send(GUILD_B, self.far, DM_B, first_number),
                self.send(GUILD_C, second, DM_C, 1),
                return_exceptions=True,
            )
        self.assertEqual(sum(isinstance(r, tuple) for r in results), 1)
        self.assertEqual(sum(isinstance(r, RuleSetError) for r in results), 1)


class Edges(_Base):
    async def test_a_third_server_cannot_read_answers_it_did_not_ask_for(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        await self.send(GUILD_B, self.far, DM_B, number)
        async with self.db.guild(GUILD_C) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rule_set_suggestions")
            self.assertEqual((await cur.fetchone())["n"], 0)  # type: ignore[index]
        # Anyone who holds the id can ask for its answer (it is the key), but gets only that.
        async with self.db.guild(GUILD_C) as conn:
            cur = await conn.execute("SELECT * FROM dmbot_suggestion_answers(%s)", ("0" * 32,))
            self.assertEqual(await cur.fetchall(), [])

    async def test_an_unanswered_suggestion_is_told_after_thirty_days(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        await self.send(GUILD_B, self.far, DM_B, number)
        later = RuleSetStore(self.db, clock=lambda: 1_700_000_000 + 31 * 86400)
        self.assertEqual(
            [s.status for s in await later.sent_answers(GUILD_B, self.far)], ["expired"]
        )

    async def test_a_slot_frees_once_the_answer_is_told(self) -> None:
        numbers = [await self.own_rule(f"Rule number {n}") for n in range(6)]
        sids = [(await self.send(GUILD_B, self.far, DM_B, n))[0] for n in numbers[:5]]
        await self.sets.decline_suggestion(GUILD_A, self.pub, DM_A, sids[0])
        await self.sets.mark_told(GUILD_B, self.far, [sids[0]])
        await self.send(GUILD_B, self.far, DM_B, numbers[5])  # no longer refused

    async def test_a_suggestion_the_set_already_holds_is_closed(self) -> None:
        number = await self.own_rule("No flanking")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        self.assertEqual(str(err.exception), rule_sets.ALREADY_IN_SET)
        closed = await self.sets.suggestion(GUILD_A, self.pub, sid)
        assert closed is not None
        self.assertEqual(closed.status, "present")  # no dead button, no slot held

    async def test_a_changed_set_leaves_the_suggestion_open(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(
                GUILD_A, self.pub, DM_A, sid, plan_ok=True, expected_version=9
            )
        still = await self.sets.suggestion(GUILD_A, self.pub, sid)
        assert still is not None
        self.assertEqual(still.status, "open")

    async def test_a_rule_removed_before_sending_is_said(self) -> None:
        with self.assertRaises(RuleSetError) as err:
            await self.send(GUILD_B, self.far, DM_B, 99)
        self.assertEqual(str(err.exception), rule_sets.NO_SUCH_RULE)


class Review(_Base):
    async def test_what_goes_out_is_what_the_dm_confirmed(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        shown = (await self.houses.list(GUILD_B, self.far))[0]
        await self.houses.edit(GUILD_B, self.far, DM_B, number, "A different rule")
        with self.assertRaises(RuleSetError) as err:
            await self.send(GUILD_B, self.far, DM_B, number, shown.version)
        self.assertEqual(str(err.exception), rule_sets.RULE_CHANGED)

    async def test_a_suggestion_past_thirty_days_cannot_be_added(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, number)
        later = RuleSetStore(self.db, clock=lambda: 1_700_000_000 + 31 * 86400)
        with self.assertRaises(RuleSetError) as err:
            await later.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        self.assertEqual(str(err.exception), rule_sets.SUGGESTION_GONE)
        old = await later.suggestion(GUILD_A, self.pub, sid)
        assert old is not None
        self.assertEqual(old.status, "expired")
        self.assertEqual(await later.unnoted_suggestions(GUILD_A, self.pub), [])

    async def test_a_note_is_not_posted_for_a_set_that_is_not_shared(self) -> None:
        number = await self.own_rule("Potions are a bonus action")
        await self.send(GUILD_B, self.far, DM_B, number)
        self.assertEqual(len(await self.sets.unnoted_suggestions(GUILD_A, self.pub)), 1)
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        self.assertEqual(await self.sets.unnoted_suggestions(GUILD_A, self.pub), [])

    async def test_each_outcome_has_its_own_status(self) -> None:
        held = await self.own_rule("No flanking")
        number = await self.own_rule("Potions are a bonus action")
        sid, _, _ = await self.send(GUILD_B, self.far, DM_B, held)
        with self.assertRaises(RuleSetError):
            await self.sets.accept_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        await self.send(GUILD_B, self.far, DM_B, number)
        answers = {s.rule: s.status for s in await self.sets.sent_answers(GUILD_B, self.far)}
        self.assertEqual(answers, {"No flanking": "present", "Potions are a bonus action": "open"})
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM rule_sets WHERE guild_id = %s", (GUILD_A,))
        gone = {s.status for s in await self.sets.sent_answers(GUILD_B, self.far)}
        self.assertEqual(gone, {"gone"})  # the set (and with it the question) is gone
