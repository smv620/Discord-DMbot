"""Pack updates (#1527 part C): what changed between two versions, Follow at a session start,
Undo, Pick changes that merge into the campaign's own homebrew, a pack that went away, and that
only a DM changes anything."""

from __future__ import annotations

import unittest

from psycopg.types.json import Jsonb

from dmbot.campaigns import CampaignStore
from dmbot.rules import pack_diff
from dmbot.rules.homebrew import HOMEBREW_MAX, HomebrewStore
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_content import PACKS_MAX
from dmbot.rules.pack_updates import PackUpdateStore
from dmbot.rules.rule_sets import FollowedSetsSection, RuleSetError
from tests.pg import DatabaseTest

GUILD = 1001
DM_A, DM_B, PLAYER = 11, 22, 33


def pack(*entries: tuple[str, str]) -> dict[str, object]:
    return {
        "entries": [
            {"kind": "item", "name": n, "summary": None, "text": t, "known": False}
            for n, t in entries
        ]
    }


class Diff(unittest.TestCase):
    def test_added_changed_removed_by_name_whatever_the_case(self) -> None:
        old = pack(("Lamp", "Glows."), ("Rope", "Long."), ("Gone", "x"))
        new = pack(("lamp", "Glows."), ("Rope", "Very long."), ("New", "y"))
        got = {(c.kind, c.key) for c in pack_diff.diff(old, new)}
        # "Lamp" and "lamp" are the same name, but the casing in the entry itself is a change.
        self.assertEqual(
            got, {("changed", "lamp"), ("changed", "rope"), ("removed", "gone"), ("added", "new")}
        )

    def test_apply_takes_only_the_accepted(self) -> None:
        old = pack(("Rope", "Long."), ("Gone", "x"))
        new = pack(("Rope", "Very long."), ("New", "y"))
        changes = pack_diff.diff(old, new)
        out = pack_diff.apply(old, changes, {"rope"})
        names = {k: e["text"] for k, e in pack_diff.entries_of(out).items()}
        self.assertEqual(names, {"rope": "Very long.", "gone": "x"})  # the rest stays as it was
        everything = pack_diff.apply(old, changes)
        self.assertEqual(set(pack_diff.entries_of(everything)), {"rope", "new"})

    def test_a_line_and_a_count(self) -> None:
        (change,) = pack_diff.diff(pack(), pack(("Lamp", "Glows brightly.")))
        self.assertEqual(pack_diff.describe(change), "New: Lamp, Glows brightly.")
        self.assertEqual(pack_diff.count_words([change]), "1 entry")
        self.assertEqual(pack_diff.count_words([change, change]), "2 entries")


class Updates(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: 1_700_000_000)
        self.store = PackUpdateStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = await self.campaigns.create(GUILD, "Frostmaiden", DM_A)
        self.me = await self.campaigns.create(GUILD, "Sunken City", DM_B)
        for name, text in (("Lamp", "Glows."), ("Rope", "Long.")):
            await self.brew.add(GUILD, self.pub.id, DM_A, kind="item", name=name, text=text)
        await self.store.publish_pack(
            GUILD, self.pub.id, DM_A, "Frost Pack", await self.brew.list(GUILD, self.pub.id)
        )
        (offer,) = await self.store.packs_here(GUILD, self.me.id)
        self.follow = await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)

    async def publish_new(self) -> None:
        """The publisher changes Rope, drops Lamp and adds Torch: version 2."""
        entries = await self.brew.list(GUILD, self.pub.id)
        by = {e.name: e for e in entries}
        await self.brew.edit(
            GUILD, self.pub.id, DM_A, by["Rope"].id, kind="item", name="Rope", text="Very long."
        )
        await self.brew.remove(GUILD, self.pub.id, DM_A, by["Lamp"].id)
        await self.brew.add(GUILD, self.pub.id, DM_A, kind="item", name="Torch", text="Burns.")
        await self.store.publish_pack_changes(
            GUILD, self.pub.id, DM_A, await self.brew.list(GUILD, self.pub.id)
        )

    async def test_nothing_is_pending_until_the_pack_changes(self) -> None:
        self.assertEqual(await self.store.pending_pack_updates(GUILD, self.me.id), [])
        await self.publish_new()
        (update,) = await self.store.pending_pack_updates(GUILD, self.me.id)
        self.assertEqual(update.offer.version, 2)
        self.assertEqual(
            {(c.kind, c.key) for c in update.changes},
            {("changed", "rope"), ("removed", "lamp"), ("added", "torch")},
        )

    async def test_follow_by_dmbot_then_undo_then_dmbot_waits_for_a_newer_one(self) -> None:
        await self.publish_new()
        taken = await self.store.take_pack_update(
            GUILD, self.me.id, None, self.follow.set_id, expected_version=2
        )
        self.assertFalse(taken.merged)
        (now,) = await self.store.followed_packs(GUILD, self.me.id)
        self.assertEqual(now.version, 2)
        back = await self.store.undo_last_pack_take(GUILD, self.me.id, DM_B, now.set_id, 2)
        self.assertEqual(back.version, 2)
        (undone,) = await self.store.followed_packs(GUILD, self.me.id)
        self.assertEqual(undone.version, 1)
        # DMbot does not take the version that was sent back; the DM still sees it to pick.
        self.assertEqual(
            await self.store.pending_pack_updates(GUILD, self.me.id, for_dmbot=True), []
        )
        self.assertEqual(len(await self.store.pending_pack_updates(GUILD, self.me.id)), 1)
        with self.assertRaises(RuleSetError):
            await self.store.take_pack_update(GUILD, self.me.id, None, now.set_id)

    async def test_an_old_undo_cannot_undo_a_newer_update(self) -> None:
        await self.publish_new()
        await self.store.take_pack_update(GUILD, self.me.id, None, self.follow.set_id)
        with self.assertRaises(RuleSetError):
            await self.store.undo_last_pack_take(GUILD, self.me.id, DM_B, self.follow.set_id, 1)

    async def test_pick_with_something_left_out_merges_into_own_homebrew(self) -> None:
        await self.brew.add(GUILD, self.me.id, DM_B, kind="item", name="ROPE", text="Mine.")
        await self.publish_new()
        taken = await self.store.take_pack_update(
            GUILD, self.me.id, DM_B, self.follow.set_id, accepted={"torch"}, expected_version=2
        )
        self.assertTrue(taken.merged)
        self.assertEqual(await self.store.followed_packs(GUILD, self.me.id), [])  # follow ended
        mine = {e.name: e.text for e in await self.brew.list(GUILD, self.me.id)}
        # Its own Rope wins; Lamp (kept, as it was) and Torch (accepted) come in; Rope's change
        # was left out, so the old Rope is not copied over its own.
        self.assertEqual(mine, {"ROPE": "Mine.", "Lamp": "Glows.", "Torch": "Burns."})

    async def test_undo_of_a_merge_removes_what_it_added_and_follows_again(self) -> None:
        await self.publish_new()
        taken = await self.store.take_pack_update(
            GUILD, self.me.id, DM_B, self.follow.set_id, accepted=set(), expected_version=2
        )
        added = await self.brew.list(GUILD, self.me.id)
        self.assertEqual({e.name for e in added}, {"Lamp", "Rope"})
        edited = next(e for e in added if e.name == "Rope")
        await self.brew.edit(
            GUILD, self.me.id, DM_B, edited.id, kind="item", name="Rope", text="I edited it."
        )
        kept = await self.store.undo_pack_take(GUILD, self.me.id, DM_B, taken)
        self.assertEqual(kept, 1)  # the edited one stays
        self.assertEqual([e.name for e in await self.brew.list(GUILD, self.me.id)], ["Rope"])
        (back,) = await self.store.followed_packs(GUILD, self.me.id)
        self.assertEqual(back.version, 1)
        self.assertEqual(
            await self.store.pending_pack_updates(GUILD, self.me.id, for_dmbot=True), []
        )

    async def test_a_changed_pack_since_the_dm_looked_is_refused(self) -> None:
        await self.publish_new()
        with self.assertRaises(RuleSetError):
            await self.store.take_pack_update(
                GUILD, self.me.id, DM_B, self.follow.set_id, accepted=set(), expected_version=1
            )

    async def test_only_a_dm_of_the_campaign_takes_or_undoes(self) -> None:
        await self.publish_new()
        for who in (PLAYER, DM_A):  # DM_A is a DM, but of another campaign
            with self.assertRaises(HouseRuleError):
                await self.store.take_pack_update(
                    GUILD, self.me.id, who, self.follow.set_id, accepted=set()
                )
        taken = await self.store.take_pack_update(GUILD, self.me.id, None, self.follow.set_id)
        with self.assertRaises(HouseRuleError):
            await self.store.undo_pack_take(GUILD, self.me.id, PLAYER, taken)
        with self.assertRaises(HouseRuleError):
            await self.store.undo_last_pack_take(GUILD, self.me.id, PLAYER, taken.before.set_id, 2)

    async def test_a_pack_that_stopped_sharing_is_marked_once(self) -> None:
        self.assertEqual(await self.store.gone_packs(GUILD, self.me.id), [])
        await self.store.stop_sharing_pack(GUILD, self.pub.id, DM_A)
        (gone,) = await self.store.gone_packs(GUILD, self.me.id)
        self.assertTrue(await self.store.stop_updating_pack(GUILD, self.me.id, gone.set_id))
        self.assertFalse(await self.store.stop_updating_pack(GUILD, self.me.id, gone.set_id))
        (kept,) = await self.store.followed_packs(GUILD, self.me.id)
        self.assertFalse(kept.updating)  # it keeps its copy

    async def test_a_merge_adds_rejected_changes_on_and_keeps_a_followers_off(self) -> None:
        mine = await self.brew.add(
            GUILD, self.me.id, DM_B, kind="item", name="Torch", text="My own torch."
        )
        await self.brew.set_enabled(GUILD, self.me.id, DM_B, mine.id, False)
        await self.publish_new()  # Torch is added to the pack too
        await self.store.take_pack_update(
            GUILD, self.me.id, DM_B, self.follow.set_id, accepted=set(), expected_version=2
        )
        entries = {e.name: e for e in await self.brew.list(GUILD, self.me.id)}
        # The old copy's entries come into the campaign's own homebrew, on; the follower's own
        # Torch (turned off) wins by name, is not touched, and stays off.
        self.assertEqual(set(entries), {"Lamp", "Rope", "Torch"})
        self.assertEqual(
            {n: e.enabled for n, e in entries.items()}, {"Lamp": True, "Rope": True, "Torch": False}
        )
        self.assertEqual(entries["Torch"].text, "My own torch.")
        self.assertEqual(entries["Torch"].version, 2)  # (one change: turning it off)

    async def test_dmbot_never_merges(self) -> None:
        await self.publish_new()
        with self.assertRaises(RuleSetError):
            await self.store.take_pack_update(
                GUILD, self.me.id, None, self.follow.set_id, accepted=set()
            )
        self.assertEqual(await self.brew.list(GUILD, self.me.id), [])
        (still,) = await self.store.followed_packs(GUILD, self.me.id)
        self.assertEqual(still.version, 1)

    async def test_a_merge_over_the_cap_changes_nothing(self) -> None:
        await self.publish_new()
        for n in range(HOMEBREW_MAX - 1):  # room for one entry, but the merge needs two
            await self.brew.add(GUILD, self.me.id, DM_B, kind="item", name=f"Own {n}", text="x")
        with self.assertRaises(RuleSetError):
            await self.store.take_pack_update(
                GUILD, self.me.id, DM_B, self.follow.set_id, accepted=set(), expected_version=2
            )
        self.assertEqual(len(await self.brew.list(GUILD, self.me.id)), HOMEBREW_MAX - 1)
        (still,) = await self.store.followed_packs(GUILD, self.me.id)  # the follow stays
        self.assertEqual(still.version, 1)

    async def test_undoing_a_merge_needs_a_free_pack_slot(self) -> None:
        await self.publish_new()
        taken = await self.store.take_pack_update(
            GUILD, self.me.id, DM_B, self.follow.set_id, accepted=set(), expected_version=2
        )
        for n in range(PACKS_MAX):  # the DM follows other packs meanwhile
            other = await self.campaigns.create(GUILD, f"Pub {n}", DM_A)
            await self.brew.add(GUILD, other.id, DM_A, kind="item", name=f"I{n}", text="x")
            await self.store.publish_pack(
                GUILD, other.id, DM_A, f"P{n}", await self.brew.list(GUILD, other.id)
            )
        for offer in await self.store.packs_here(GUILD, self.me.id):
            if offer.name.startswith("P"):
                await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)
        with self.assertRaises(RuleSetError):
            await self.store.undo_pack_take(GUILD, self.me.id, DM_B, taken)

    async def test_a_damaged_copy_is_not_offered_an_update(self) -> None:
        await self.publish_new()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE rule_set_follows SET content = %s WHERE guild_id = %s AND campaign_id = %s",
                (Jsonb({"entries": "broken"}), GUILD, self.me.id),
            )
        self.assertEqual(await self.store.pending_pack_updates(GUILD, self.me.id), [])
