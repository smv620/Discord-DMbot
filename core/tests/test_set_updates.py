"""Shared rulesets, part 2 (#1191): new versions by the publisher and named co-editors, and a
follower taking an update (Follow, or Cherry pick that merges into its own rules). Needs
Postgres, like the other store tests; CI runs them."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.house import HOUSE_RULES_MAX, HouseRuleError, HouseRuleStore
from dmbot.rules.rule_sets import RuleSetError, RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B, EDITOR, PLAYER = 11, 22, 44, 33


def content(*rules: tuple[int, str, str | None]) -> dict[str, Any]:
    return {"rules": [{"number": n, "rule": r, "instead": i} for n, r, i in rules], "link": None}


V2 = content(
    (1, "Crits double the dice, always", "Crits double the damage"),
    (2, "No flanking", None),
    (3, "Potions are a bonus action", None),
)


class Updates(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.same = (await self.campaigns.create(GUILD_A, "Sunken City", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(
            GUILD_A, self.pub, DM_A, "Crits double the dice", "Crits double the damage"
        )
        await self.houses.add(GUILD_A, self.pub, DM_A, "No flanking")
        rules = await self.houses.list(GUILD_A, self.pub)
        await self.sets.publish(GUILD_A, self.pub, DM_A, "Frostmaiden", rules, None)
        self.code = await self.sets.make_code(GUILD_A, self.pub, DM_A)

    async def make_editor(self) -> None:
        """EDITOR becomes a DM of the publishing campaign, then a named co-editor."""
        campaigns = CampaignStore(self.db)
        await campaigns.add_dm(GUILD_A, self.pub, EDITOR)
        await self.sets.add_editor(GUILD_A, self.pub, DM_A, EDITOR)

    async def follow_far(self) -> rule_sets.Follow:
        offer = await self.sets.by_code(GUILD_B, DM_B, self.code)
        return await self.sets.follow(GUILD_B, self.far, DM_B, offer)

    async def test_who_may_write_the_set_and_the_log(self) -> None:
        with self.assertRaises(RuleSetError):
            await self.sets.save_version(GUILD_A, self.pub, PLAYER, V2, plan_ok=True)
        with self.assertRaises(RuleSetError):
            await self.sets.save_version(
                GUILD_A, self.pub, DM_A, content((1, "x", None)) | {"rules": "no"}, plan_ok=True
            )
        await self.make_editor()
        with self.assertRaises(HouseRuleError):  # naming co-editors is the publisher's
            await self.sets.add_editor(GUILD_A, self.pub, EDITOR, PLAYER)
        made = await self.sets.save_version(GUILD_A, self.pub, EDITOR, V2, plan_ok=True)
        self.assertEqual(made.version, 2)
        with self.assertRaises(RuleSetError):  # nothing differs
            await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        log = await self.sets.version_log(GUILD_A, self.pub)
        self.assertEqual(
            [(v.version, v.by, v.source) for v in log],
            [(2, EDITOR, "publisher"), (1, DM_A, "publisher")],
        )
        self.assertTrue(await self.sets.remove_editor(GUILD_A, self.pub, DM_A, EDITOR))
        self.assertEqual(await self.sets.editors(GUILD_A, self.pub), [])
        # A co-editor has to be a DM of the publishing campaign; and the plan must be paid.
        with self.assertRaises(RuleSetError):
            await self.sets.add_editor(GUILD_A, self.pub, DM_A, PLAYER)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.save_version(
                GUILD_A, self.pub, DM_A, content((9, "z", None)), plan_ok=False
            )
        self.assertEqual(str(err.exception), rule_sets.NOT_PAID)

    async def test_an_update_waits_and_the_follower_keeps_its_copy_until_it_takes_it(self) -> None:
        follow = await self.follow_far()
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        self.assertEqual(update.offer.version, 2)
        self.assertEqual(
            [(c.kind, c.number) for c in update.changes], [("changed", 1), ("added", 3)]
        )
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual(
            (mine.version, mine.content), (1, follow.content)
        )  # not changed by looking

    async def test_follow_takes_the_new_version_and_undo_puts_it_back(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B)
        self.assertFalse(taken.merged)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual(mine.version, 2)
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)

    async def test_cherry_pick_with_everything_accepted_is_follow(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={1, 3})
        self.assertFalse(taken.merged)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 2)
        self.assertEqual(await self.houses.list(GUILD_B, self.far), [])

    async def test_cherry_pick_with_a_rejection_merges_into_own_rules_and_ends_the_follow(
        self,
    ) -> None:
        await self.houses.add(GUILD_B, self.far, DM_B, "Own rule from before")
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(
            GUILD_B, self.far, DM_B, accepted={3}
        )  # rule 1 rejected
        self.assertTrue(taken.merged)
        self.assertEqual(await self.sets.following(GUILD_B, self.far), [])
        own = {r.rule: r for r in await self.houses.list(GUILD_B, self.far)}
        self.assertIn("Own rule from before", own)
        self.assertEqual(own["Own rule from before"].number, 1)  # earlier rules keep their numbers
        self.assertIn("Crits double the dice", own)  # the set as it was (rule 1 rejected)
        self.assertNotIn("Crits double the dice, always", own)
        self.assertIn("Potions are a bonus action", own)  # the accepted one
        merged = own["Potions are a bonus action"]
        self.assertEqual(merged.from_set, "Frostmaiden house rules, v2")
        # The publisher is untouched by the follower.
        self.assertEqual(len(await self.houses.list(GUILD_A, self.pub)), 2)
        # One Undo puts the follow back and removes the merged rules.
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        self.assertEqual(
            [r.rule for r in await self.houses.list(GUILD_B, self.far)], ["Own rule from before"]
        )
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)
        # Following again later is allowed.
        await self.sets.unfollow(GUILD_B, self.far, DM_B)
        await self.follow_far()

    async def test_only_a_dm_takes_an_update_and_a_set_that_stopped_gives_none(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        with self.assertRaises(HouseRuleError):
            await self.sets.take_update(GUILD_B, self.far, PLAYER)
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        self.assertTrue(await self.sets.is_gone(GUILD_B, self.far))
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, DM_B)
        self.assertEqual(await self.sets.stop_updating(GUILD_B, self.far), 1)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertFalse(mine.updating)
        self.assertEqual(mine.version, 1)  # the copy stays

    async def test_a_follower_in_the_same_server_sees_updates_without_a_code(self) -> None:
        shared = (await self.sets.shared_here(GUILD_A, self.same))[0]
        await self.sets.follow(GUILD_A, self.same, DM_A, shared)
        self.assertIsNone((await self.sets.following(GUILD_A, self.same))[0].code)
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        update = await self.sets.pending_update(GUILD_A, self.same)
        assert update is not None
        self.assertEqual(update.offer.version, 2)

    async def test_undo_leaves_a_merged_rule_the_dm_has_edited(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        potion = next(r for r in await self.houses.list(GUILD_B, self.far) if "Potions" in r.rule)
        await self.houses.edit(
            GUILD_B, self.far, DM_B, potion.number, "Potions take a bonus action"
        )
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        left = [r.rule for r in await self.houses.list(GUILD_B, self.far)]
        self.assertEqual(left, ["Potions take a bonus action"])  # only the untouched ones went

    async def test_a_take_made_on_older_changes_is_refused(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.save_version(
            GUILD_A, self.pub, DM_A, content((7, "Another", None)), plan_ok=True
        )
        with self.assertRaises(RuleSetError) as err:
            await self.sets.take_update(GUILD_B, self.far, DM_B, {1}, expected_version=2)
        self.assertEqual(str(err.exception), rule_sets.CHANGED_AGAIN)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)

    async def test_two_takes_in_a_row_give_nothing_new_the_second_time(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.take_update(GUILD_B, self.far, DM_B)
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, DM_B)

    async def test_a_follow_marked_no_longer_updated_takes_nothing(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.stop_updating(GUILD_B, self.far)
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, DM_B)

    async def test_two_editors_saving_at_once_make_two_versions(self) -> None:
        import asyncio

        await self.make_editor()
        results = await asyncio.gather(
            self.sets.save_version(
                GUILD_A, self.pub, EDITOR, content((5, "One", None)), plan_ok=True
            ),
            self.sets.save_version(
                GUILD_A, self.pub, DM_A, content((6, "Two", None)), plan_ok=True
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sorted(r.version for r in results if not isinstance(r, BaseException)), [2, 3]
        )

    async def test_a_full_campaign_gets_a_clear_message_and_nothing_changes(self) -> None:
        await self.follow_far()
        for i in range(HOUSE_RULES_MAX):
            await self.houses.add(GUILD_B, self.far, DM_B, f"Own rule {i}")
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        self.assertIn("more than", str(err.exception))
        self.assertEqual(len(await self.sets.following(GUILD_B, self.far)), 1)

    async def test_a_code_turned_off_stops_new_followers_but_not_the_ones_who_followed(
        self,
    ) -> None:
        await self.follow_far()
        await self.sets.turn_off_code(GUILD_A, self.pub, DM_A, self.code)
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        update = await self.sets.pending_update(GUILD_B, self.far)  # still gets v2
        assert update is not None
        self.assertEqual(update.offer.version, 2)
        self.assertFalse(await self.sets.is_gone(GUILD_B, self.far))
        # A new follow with that code is refused.
        other = (await self.campaigns.create(GUILD_B, "Another", DM_B)).id
        with self.assertRaises(RuleSetError):
            await self.sets.by_code(GUILD_B, DM_B, self.code)
        self.assertEqual(await self.sets.following(GUILD_B, other), [])
        # Only Stop sharing ends updates.
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        self.assertTrue(await self.sets.is_gone(GUILD_B, self.far))

    async def test_a_code_turned_off_before_the_follow_gives_no_updates(self) -> None:
        # (A follow always needs a live code, so this is the order: follow, then off, then a
        # re-follow can't happen; the door compares the time the code was turned off.)
        await self.follow_far()
        follows = await self.sets.following(GUILD_B, self.far)
        later = follows[0].followed_at + 10
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM dmbot_set_update_by_code(%s, %s)", (self.code, later)
            )
            row = await cur.fetchone()
        self.assertEqual((row or {})["n"], 1)  # live code: answers whenever it is asked
        await self.sets.turn_off_code(GUILD_A, self.pub, DM_A, self.code)
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM dmbot_set_update_by_code(%s, %s)",
                (self.code, 0),  # a follow older than the switch-off
            )
            older = await cur.fetchone()
            cur = await conn.execute(
                "SELECT count(*) AS n FROM dmbot_set_update_by_code(%s, %s)",
                (self.code, 4_000_000_000),  # a follow made after it was off
            )
            newer = await cur.fetchone()
        self.assertEqual((older or {})["n"], 1)
        self.assertEqual((newer or {})["n"], 0)

    async def test_a_follow_from_another_server_with_no_code_reads_as_unknown_not_gone(
        self,
    ) -> None:
        await self.follow_far()
        async with self.db.guild(GUILD_B) as conn:
            await conn.execute("UPDATE rule_set_follows SET code = NULL")
        self.assertFalse(await self.sets.is_gone(GUILD_B, self.far))
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))

    async def test_an_update_taken_by_dmbot_is_credited_to_dmbot_and_can_be_undone(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, None, expected_version=2)
        self.assertFalse(taken.merged)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT taken_by, taken_source, version FROM rule_set_follows")
            row = await cur.fetchone()
        self.assertEqual(
            ((row or {})["taken_by"], (row or {})["taken_source"], (row or {})["version"]),
            (None, "auto", 2),
        )
        # Undo needs nothing in memory (it works after a restart), and only for that version.
        with self.assertRaises(RuleSetError):
            await self.sets.undo_last_take(GUILD_B, self.far, DM_B, expected_version=9)
        back = await self.sets.undo_last_take(GUILD_B, self.far, DM_B, expected_version=2)
        self.assertEqual(back.previous_version, 1)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual((mine.version, mine.previous_version), (1, None))
        with self.assertRaises(RuleSetError):
            await self.sets.undo_last_take(GUILD_B, self.far, DM_B, expected_version=2)  # once

    async def test_undo_keeps_the_earlier_version_until_a_newer_one_comes(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.take_update(GUILD_B, self.far, None, expected_version=2)
        await self.sets.undo_last_take(GUILD_B, self.far, DM_B, expected_version=2)
        # DMbot does not take v2 again at the next session start...
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far, for_dmbot=True))
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, None, expected_version=2)
        # ...but the DM still sees it and can choose it.
        shown = await self.sets.pending_update(GUILD_B, self.far)
        assert shown is not None
        self.assertEqual(shown.offer.version, 2)
        # A newer version is taken by DMbot as before.
        v3 = content((1, "Crits double the dice, always", None), (2, "No flanking", None))
        await self.sets.save_version(GUILD_A, self.pub, DM_A, v3, plan_ok=True)
        newer = await self.sets.pending_update(GUILD_B, self.far, for_dmbot=True)
        assert newer is not None
        self.assertEqual(newer.offer.version, 3)
        taken = await self.sets.take_update(GUILD_B, self.far, None, expected_version=3)
        self.assertEqual(taken.after_version, 3)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertIsNone(mine.declined_version)

    async def test_pick_reject_undo_does_not_retake_the_rejected_version(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        self.assertTrue(taken.merged)
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual((mine.version, mine.declined_version), (1, 2))
        # DMbot waits for a newer version; the DM can still choose this one.
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far, for_dmbot=True))
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, None, expected_version=2)
        self.assertIsNotNone(await self.sets.pending_update(GUILD_B, self.far))

    async def test_the_other_undo_path_also_clears_what_it_undid(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, expected_version=2)
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual(
            (mine.version, mine.previous_version, mine.previous_content, mine.declined_version),
            (1, None, None, 2),
        )
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT taken_by, taken_source FROM rule_set_follows")
            row = await cur.fetchone()
        self.assertEqual((row or {})["taken_by"], None)
        self.assertEqual((row or {})["taken_source"], None)

    async def test_a_publish_made_on_an_old_version_is_refused_in_the_store(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)  # now version 2
        v3 = content((1, "Crits double the dice, always", None))
        with self.assertRaises(RuleSetError):  # the DM was looking at version 1
            await self.sets.save_version(
                GUILD_A, self.pub, DM_A, v3, plan_ok=True, expected_version=1
            )
        made = await self.sets.save_version(
            GUILD_A, self.pub, DM_A, v3, plan_ok=True, expected_version=2
        )
        self.assertEqual(made.version, 3)

    async def test_current_content_is_the_newest_version_and_none_when_not_shared(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        got = await self.sets.current_content(GUILD_A, self.pub)
        assert got is not None
        self.assertEqual((got[0].version, got[1]["rules"][0]["number"]), (2, 1))
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        self.assertIsNone(await self.sets.current_content(GUILD_A, self.pub))
        self.assertIsNone(await self.sets.current_content(GUILD_A, "none"))

    async def test_dmbot_cannot_merge_for_a_dm(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, None, {3}, expected_version=2)

    async def test_merged_rules_rank_below_the_campaigns_own(self) -> None:
        from dmbot.rules.followed import own_first

        await self.houses.add(GUILD_B, self.far, DM_B, "Own rule, wins")
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        await self.houses.add(GUILD_B, self.far, DM_B, "An own rule written after the merge")
        rules = await self.houses.list(GUILD_B, self.far)
        merged = [r for r in rules if r.from_set]
        self.assertTrue(merged)
        self.assertTrue(all(r.from_set == "Frostmaiden house rules, v2" for r in merged))
        ordered = own_first(rules, [])
        own_positions = [i for i, r in enumerate(ordered) if not r.from_set]
        merged_positions = [i for i, r in enumerate(ordered) if r.from_set]
        self.assertLess(
            max(own_positions), min(merged_positions)
        )  # own first, whatever the numbers

    async def test_undo_after_a_merge_says_how_many_edited_rules_stayed(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        potion = next(r for r in await self.houses.list(GUILD_B, self.far) if "Potions" in r.rule)
        await self.houses.edit(
            GUILD_B, self.far, DM_B, potion.number, "Potions take a bonus action"
        )
        kept = await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        self.assertEqual(kept, 1)

    async def test_a_backup_keeps_where_a_merged_rule_came_from(self) -> None:
        from dmbot.rules.house import HouseRulesSection

        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2, plan_ok=True)
        await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        section = HouseRulesSection()
        async with self.db.guild(GUILD_B) as conn:
            rows = await section.dump(conn, GUILD_B, self.far)
        self.assertTrue(any(isinstance(r, dict) and r.get("from_set") for r in rows))
        section.check(rows)  # and it checks out as a backup row
