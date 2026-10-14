"""Shared rulesets, part 2 (#1191): new versions by the publisher and named co-editors, and a
follower taking an update (Follow, or Cherry pick that merges into its own rules). Needs
Postgres, like the other store tests; CI runs them."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRuleError, HouseRuleStore
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

    async def follow_far(self) -> rule_sets.Follow:
        offer = await self.sets.by_code(GUILD_B, DM_B, self.code)
        return await self.sets.follow(GUILD_B, self.far, DM_B, offer)

    async def test_who_may_write_the_set_and_the_log(self) -> None:
        with self.assertRaises(RuleSetError):
            await self.sets.save_version(GUILD_A, self.pub, PLAYER, V2)
        with self.assertRaises(RuleSetError):
            await self.sets.save_version(
                GUILD_A, self.pub, DM_A, content((1, "x", None)) | {"rules": "no"}
            )
        await self.sets.add_editor(GUILD_A, self.pub, DM_A, EDITOR)
        with self.assertRaises(HouseRuleError):  # naming co-editors is the publisher's
            await self.sets.add_editor(GUILD_A, self.pub, EDITOR, PLAYER)
        made = await self.sets.save_version(GUILD_A, self.pub, EDITOR, V2)
        self.assertEqual(made.version, 2)
        with self.assertRaises(RuleSetError):  # nothing differs
            await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        log = await self.sets.version_log(GUILD_A, self.pub)
        self.assertEqual(
            [(v.version, v.by, v.source) for v in log],
            [(2, EDITOR, "publisher"), (1, DM_A, "publisher")],
        )
        self.assertTrue(await self.sets.remove_editor(GUILD_A, self.pub, DM_A, EDITOR))
        with self.assertRaises(RuleSetError):
            await self.sets.save_version(GUILD_A, self.pub, EDITOR, content((9, "z", None)))

    async def test_an_update_waits_and_the_follower_keeps_its_copy_until_it_takes_it(self) -> None:
        follow = await self.follow_far()
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
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
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B)
        self.assertFalse(taken.merged)
        mine = (await self.sets.following(GUILD_B, self.far))[0]
        self.assertEqual(mine.version, 2)
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        await self.sets.undo_take(GUILD_B, self.far, DM_B, taken)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)

    async def test_cherry_pick_with_everything_accepted_is_follow(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        taken = await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={1, 3})
        self.assertFalse(taken.merged)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 2)
        self.assertEqual(await self.houses.list(GUILD_B, self.far), [])

    async def test_cherry_pick_with_a_rejection_merges_into_own_rules_and_ends_the_follow(
        self,
    ) -> None:
        await self.houses.add(GUILD_B, self.far, DM_B, "Own rule from before")
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
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
        self.assertEqual(merged.scenario, "From Frostmaiden house rules, v2")
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
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
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
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        update = await self.sets.pending_update(GUILD_A, self.same)
        assert update is not None
        self.assertEqual(update.offer.version, 2)

    async def test_undo_leaves_a_merged_rule_the_dm_has_edited(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
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
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        await self.sets.save_version(GUILD_A, self.pub, DM_A, content((7, "Another", None)))
        with self.assertRaises(RuleSetError) as err:
            await self.sets.take_update(GUILD_B, self.far, DM_B, {1}, expected_version=2)
        self.assertEqual(str(err.exception), rule_sets.CHANGED_AGAIN)
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)

    async def test_two_takes_in_a_row_give_nothing_new_the_second_time(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        await self.sets.take_update(GUILD_B, self.far, DM_B)
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, DM_B)

    async def test_a_follow_marked_no_longer_updated_takes_nothing(self) -> None:
        await self.follow_far()
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        await self.sets.stop_updating(GUILD_B, self.far)
        with self.assertRaises(RuleSetError):
            await self.sets.take_update(GUILD_B, self.far, DM_B)

    async def test_two_editors_saving_at_once_make_two_versions(self) -> None:
        import asyncio

        await self.sets.add_editor(GUILD_A, self.pub, DM_A, EDITOR)
        results = await asyncio.gather(
            self.sets.save_version(GUILD_A, self.pub, EDITOR, content((5, "One", None))),
            self.sets.save_version(GUILD_A, self.pub, DM_A, content((6, "Two", None))),
            return_exceptions=True,
        )
        self.assertEqual(
            sorted(r.version for r in results if not isinstance(r, BaseException)), [2, 3]
        )

    async def test_a_full_campaign_gets_a_clear_message_and_nothing_changes(self) -> None:
        await self.follow_far()
        for i in range(rule_sets.HOUSE_RULES_MAX):
            await self.houses.add(GUILD_B, self.far, DM_B, f"Own rule {i}")
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        with self.assertRaises(RuleSetError) as err:
            await self.sets.take_update(GUILD_B, self.far, DM_B, accepted={3})
        self.assertIn("more than", str(err.exception))
        self.assertEqual(len(await self.sets.following(GUILD_B, self.far)), 1)

    async def test_a_code_turned_off_ends_updates_but_the_copy_stays(self) -> None:
        await self.follow_far()
        await self.sets.turn_off_code(GUILD_A, self.pub, DM_A, self.code)
        await self.sets.save_version(GUILD_A, self.pub, DM_A, V2)
        self.assertIsNone(await self.sets.pending_update(GUILD_B, self.far))
        self.assertEqual((await self.sets.following(GUILD_B, self.far))[0].version, 1)
