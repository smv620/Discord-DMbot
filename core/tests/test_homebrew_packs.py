"""Homebrew packs (#1527): what a pack carries (never an entry from a book), who may publish,
share codes across servers, following several in the DM's order, that a pack is read only through
a follow or a code, and that a backup keeps a follower's copy (never the code)."""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.rules import pack_content
from dmbot.rules.homebrew import FileEntry, Homebrew, HomebrewStore
from dmbot.rules.homebrew_packs import HomebrewPackStore, HomebrewReader
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_content import PACKS_MAX
from dmbot.rules.rule_card import homebrew_citation
from dmbot.rules.rule_sets import Follow, FollowedSetsSection, RuleSetError
from dmbot.sidebar.context import homebrew_source, relevant_homebrew
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B, PLAYER = 11, 22, 33


def entry(name: str, *, book: bool = False) -> Homebrew:
    return Homebrew(
        id=1, campaign_id="x", kind="monster", name=name, text=f"{name} text", summary=None,
        created_by=DM_A, created_at=1, updated_at=1, from_book=book,
    )  # fmt: skip


class Content(unittest.TestCase):
    def test_a_pack_carries_entries_only_and_leaves_a_book_out_by_name(self) -> None:
        content, left = pack_content.content_of([entry("Hollow King"), entry("Booked", book=True)])
        self.assertEqual(
            content,
            {
                "entries": [
                    {
                        "kind": "monster",
                        "name": "Hollow King",
                        "summary": None,
                        "text": "Hollow King text",
                        "known": True,
                    }
                ]
            },
        )
        self.assertEqual(left, ["Booked"])
        self.assertNotIn("Booked", repr(content))

    def test_a_pack_carries_the_new_kinds(self) -> None:
        mine = [
            dataclasses.replace(entry(f"A {k}"), kind=k)
            for k in ("species", "subclass", "background")
        ]
        content, _ = pack_content.content_of(mine)
        self.assertEqual(
            [e["kind"] for e in pack_content.valid_content(content)["entries"]],
            ["species", "subclass", "background"],
        )

    def test_the_shape_is_checked_and_a_pack_is_not_house_rules(self) -> None:
        good, _ = pack_content.content_of([entry("A")])
        self.assertEqual(pack_content.valid_content(good), good)
        bads: list[Any] = [
            {},
            {"entries": "x"},
            {"entries": [{"kind": "monster"}]},
            {"entries": [{**good["entries"][0], "kind": "nonsense"}]},
            {"entries": [good["entries"][0], {**good["entries"][0], "name": "a"}]},  # same name
            {"rules": []},
        ]
        for bad in bads:
            with self.assertRaises(pack_content.BadPack, msg=repr(bad)):
                pack_content.valid_content(bad)
        # Extra keys are dropped, not carried.
        extra = {"entries": [{**good["entries"][0], "secret": "LEAK"}], "link": "LEAK"}
        self.assertNotIn("LEAK", repr(pack_content.valid_content(extra)))


class Packs(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: 1_700_000_000)
        self.packs = HomebrewPackStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)
        self.same = await self.campaigns.create(GUILD_A, "Sunken City", DM_A)
        self.far = await self.campaigns.create(GUILD_B, "Far Away", DM_B)
        await self.brew.add(GUILD_A, self.pub.id, DM_A, kind="monster", name="Hollow King",
                            text="A crowned skeleton.")  # fmt: skip
        await self.brew.add(GUILD_A, self.pub.id, DM_A, kind="spell", name="Ember Step",
                            text="Step through fire.")  # fmt: skip
        await self.brew.load_file(
            GUILD_A, self.pub.id, DM_A,
            [FileEntry("feat", "Booked Rule", "Words from a book.")], from_book=True,
        )  # fmt: skip

    async def mine(self, campaign: Campaign) -> list[Homebrew]:
        return await self.brew.list(campaign.guild_id, campaign.id)

    async def publish(self, name: str = "Frost Pack") -> None:
        await self.packs.publish_pack(GUILD_A, self.pub.id, DM_A, name, await self.mine(self.pub))

    async def test_publishing_leaves_book_entries_out_and_says_which(self) -> None:
        done = await self.packs.publish_pack(
            GUILD_A, self.pub.id, DM_A, "Frost Pack", await self.mine(self.pub)
        )
        self.assertEqual(done.left_out, ("Booked Rule",))
        got = await self.packs.pack_content(GUILD_A, self.pub.id)
        assert got is not None
        self.assertEqual(
            {e["name"]: e["known"] for e in got["entries"]},
            {"Hollow King": True, "Ember Step": True},  # (always true now: nothing is hidden)
        )

    async def test_only_a_dm_publishes_and_only_once_and_not_nothing(self) -> None:
        with self.assertRaises(HouseRuleError):
            await self.packs.publish_pack(
                GUILD_A, self.pub.id, PLAYER, "X", await self.mine(self.pub)
            )
        with self.assertRaises(RuleSetError):  # only book entries: nothing to share
            await self.packs.publish_pack(
                GUILD_A, self.pub.id, DM_A, "X", [entry("Booked", book=True)]
            )
        await self.publish()
        with self.assertRaises(RuleSetError):
            await self.publish()

    async def test_following_in_the_same_server_takes_a_copy_in_order(self) -> None:
        await self.publish()
        offers = await self.packs.packs_here(GUILD_A, self.same.id)
        self.assertEqual([o.name for o in offers], ["Frost Pack"])
        await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offers[0])
        with self.assertRaises(RuleSetError):
            await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offers[0])
        with self.assertRaises(RuleSetError):  # a campaign can't follow its own pack
            await self.packs.follow_pack(GUILD_A, self.pub.id, DM_A, offers[0])
        mine = await self.packs.followed_packs(GUILD_A, self.same.id)
        self.assertEqual([(f.name, f.version, f.publisher) for f in mine],
                         [("Frost Pack", 1, "Frostmaiden")])  # fmt: skip
        # It is not offered to its own publisher, and another server sees none.
        self.assertEqual(await self.packs.packs_here(GUILD_A, self.pub.id), [])
        self.assertEqual(await self.packs.packs_here(GUILD_B, self.far.id), [])

    async def test_a_share_code_reaches_another_server_and_stopping_closes_it(self) -> None:
        await self.publish()
        code = await self.packs.make_pack_code(GUILD_A, self.pub.id, DM_A)
        offer = await self.packs.by_code(GUILD_B, DM_B, code)
        self.assertEqual(offer.kind, "homebrew")
        await self.packs.follow_pack(GUILD_B, self.far.id, DM_B, offer)
        self.assertTrue(await self.packs.stop_sharing_pack(GUILD_A, self.pub.id, DM_A))
        with self.assertRaises(RuleSetError):
            await self.packs.by_code(GUILD_B, DM_B, code)
        kept = await self.packs.followed_packs(GUILD_B, self.far.id)
        self.assertEqual(len(kept), 1)  # the follower keeps its copy

    async def test_house_codes_and_pack_codes_stay_apart(self) -> None:
        await self.publish()
        code = await self.packs.make_pack_code(GUILD_A, self.pub.id, DM_A)
        self.assertEqual(await self.packs.codes(GUILD_A, self.pub.id), [])  # house list: none
        self.assertEqual(await self.packs.pack_codes(GUILD_A, self.pub.id), [(code, True)])

    async def test_a_pack_is_not_a_house_rules_offer_and_the_order_can_change(self) -> None:
        await self.publish("One")
        other = await self.campaigns.create(GUILD_A, "Other", DM_A)
        await self.brew.add(GUILD_A, other.id, DM_A, kind="item", name="Lamp", text="Glows.")
        await self.packs.publish_pack(GUILD_A, other.id, DM_A, "Two", await self.mine(other))
        for offer in await self.packs.packs_here(GUILD_A, self.same.id):
            await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offer)
        names = [f.name for f in await self.packs.followed_packs(GUILD_A, self.same.id)]
        self.assertEqual(names, ["One", "Two"])
        two = (await self.packs.followed_packs(GUILD_A, self.same.id))[1]
        moved = await self.packs.move_pack(GUILD_A, self.same.id, DM_A, two.set_id, -1)
        self.assertEqual([f.name for f in moved], ["Two", "One"])
        again = await self.packs.move_pack(GUILD_A, self.same.id, DM_A, two.set_id, -1)
        self.assertEqual([f.name for f in again], ["Two", "One"])  # already first
        gone, at = await self.packs.unfollow_pack(GUILD_A, self.same.id, DM_A, two.set_id)
        self.assertEqual(at, 0)
        await self.packs.restore_pack(GUILD_A, self.same.id, DM_A, gone, at)
        names = [f.name for f in await self.packs.followed_packs(GUILD_A, self.same.id)]
        self.assertEqual(names, ["Two", "One"])

    async def test_there_is_a_limit_on_packs_followed(self) -> None:
        for n in range(PACKS_MAX + 1):
            other = await self.campaigns.create(GUILD_A, f"Pub {n}", DM_B)
            await self.brew.add(GUILD_A, other.id, DM_B, kind="item", name=f"I{n}", text="x")
            await self.packs.publish_pack(GUILD_A, other.id, DM_B, f"P{n}", await self.mine(other))
        offers = await self.packs.packs_here(GUILD_A, self.same.id)
        offers = [o for o in offers if o.name.startswith("P")]
        for offer in offers[:PACKS_MAX]:
            await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offer)
        with self.assertRaises(RuleSetError):
            await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offers[PACKS_MAX])

    async def test_a_new_version_reaches_followers_only_when_something_changed(self) -> None:
        await self.publish()
        with self.assertRaises(RuleSetError):
            await self.packs.publish_pack_changes(
                GUILD_A, self.pub.id, DM_A, await self.mine(self.pub)
            )
        await self.brew.add(GUILD_A, self.pub.id, DM_A, kind="item", name="Lamp", text="Glows.")
        done = await self.packs.publish_pack_changes(
            GUILD_A, self.pub.id, DM_A, await self.mine(self.pub)
        )
        self.assertEqual(done.pack.version, 2)
        old = await self.packs.pack_content(GUILD_A, self.pub.id, 1)
        assert old is not None
        self.assertEqual(len(old["entries"]), 2)

    async def test_a_backup_keeps_the_copy_but_never_the_code(self) -> None:
        await self.publish()
        code = await self.packs.make_pack_code(GUILD_A, self.pub.id, DM_A)
        offer = await self.packs.by_code(GUILD_A, DM_A, code)
        await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offer)
        data = await self.campaigns.export(GUILD_A, self.same.id)
        self.assertNotIn(code, repr(data))
        restored = await self.campaigns.import_backup(GUILD_B, data, DM_B)
        kept = await self.packs.followed_packs(GUILD_B, restored.id)
        self.assertEqual([f.name for f in kept], ["Frost Pack"])
        self.assertFalse(kept[0].updating)  # no code: no updates
        self.assertEqual(len(kept[0].content["entries"]), 2)

    async def follow_all(self, campaign: Campaign) -> None:
        for offer in await self.packs.packs_here(campaign.guild_id, campaign.id):
            await self.packs.follow_pack(campaign.guild_id, campaign.id, DM_A, offer)

    async def test_a_follower_reads_its_own_first_then_the_packs_in_order(self) -> None:
        await self.publish("Frost Pack")
        other = await self.campaigns.create(GUILD_A, "Other", DM_A)
        await self.brew.add(GUILD_A, other.id, DM_A, kind="monster", name="hollow king",
                            text="Other's king.")  # fmt: skip
        await self.brew.add(GUILD_A, other.id, DM_A, kind="item", name="Lamp", text="Glows.")
        await self.packs.publish_pack(GUILD_A, other.id, DM_A, "Lamp Pack", await self.mine(other))
        await self.brew.add(GUILD_A, self.same.id, DM_A, kind="spell", name="EMBER STEP",
                            text="My own fire step.")  # fmt: skip
        await self.follow_all(self.same)
        reader = HomebrewReader(self.brew, self.packs)
        got = {e.name.casefold(): e for e in await reader.list(GUILD_A, self.same.id)}
        # Its own entry beats the pack's of the same name; an earlier pack beats a later one.
        self.assertIsNone(got["ember step"].source)
        self.assertEqual(got["ember step"].text, "My own fire step.")
        self.assertEqual(got["hollow king"].source, "Frost Pack homebrew, v1")
        self.assertEqual(got["lamp"].source, "Lamp Pack homebrew, v1")
        names = [e.name for e in await reader.list(GUILD_A, self.same.id)]
        self.assertEqual(names[0], "EMBER STEP")  # its own come first
        found = await reader.find(GUILD_A, self.same.id, "  LAMP ")
        assert found is not None
        self.assertEqual(found.text, "Glows.")
        self.assertIsNone(await reader.find(GUILD_A, self.same.id, "Booked Rule"))

    async def test_a_shared_books_entry_ranks_below_a_followed_packs(self) -> None:
        await self.publish()
        # The follower has a book entry named like one in the pack, and its own homebrew too.
        await self.brew.load_file(
            GUILD_A, self.same.id, DM_A,
            [FileEntry("monster", "Hollow King", "The book's version.")],
            from_book=True, book_edition="2014",
        )  # fmt: skip
        await self.follow_all(self.same)
        reader = HomebrewReader(self.brew, self.packs)
        found = await reader.find(GUILD_A, self.same.id, "Hollow King")
        assert found is not None
        self.assertEqual(found.source, "Frost Pack homebrew, v1")  # the pack answers
        listed = {e.name: e for e in await reader.list(GUILD_A, self.same.id)}
        self.assertEqual(listed["Hollow King"].source, "Frost Pack homebrew, v1")
        # A book entry no pack names is still read from the campaign's own.
        await self.brew.load_file(
            GUILD_A, self.same.id, DM_A,
            [FileEntry("feat", "Only In The Book", "x")], from_book=True, book_edition="other",
        )  # fmt: skip
        only = await reader.find(GUILD_A, self.same.id, "Only In The Book")
        assert only is not None
        self.assertEqual((only.from_book, only.book_edition), (True, "other"))

    async def test_a_book_entry_with_an_edition_still_never_goes_into_a_pack(self) -> None:
        await self.brew.load_file(
            GUILD_A, self.pub.id, DM_A, [FileEntry("feat", "Edition Book", "x")],
            from_book=True, book_edition="2024",
        )  # fmt: skip
        done = await self.packs.publish_pack(
            GUILD_A, self.pub.id, DM_A, "Frost Pack", await self.mine(self.pub)
        )
        self.assertIn("Edition Book", done.left_out)
        got = await self.packs.pack_content(GUILD_A, self.pub.id)
        assert got is not None
        self.assertNotIn("Edition Book", str(got))

    async def test_every_entry_is_read_and_the_source_names_the_pack(self) -> None:
        await self.publish()
        await self.follow_all(self.same)
        reader = HomebrewReader(self.brew, self.packs)
        entries = await reader.list(GUILD_A, self.same.id)
        known = relevant_homebrew(entries, "what does the Hollow King and Ember Step do")
        self.assertEqual([e.name for e in known], ["Hollow King", "Ember Step"])
        self.assertEqual(
            homebrew_source(known[0], "Sunken City"),
            "Frost Pack homebrew, v1: Hollow King [Homebrew]",
        )
        self.assertEqual(
            homebrew_citation(known[0], "Sunken City"), "Frost Pack homebrew, v1: Hollow King"
        )

    async def test_a_campaign_that_follows_nothing_reads_nothing_of_a_pack(self) -> None:
        await self.publish()
        reader = HomebrewReader(self.brew, self.packs)
        self.assertEqual(await reader.list(GUILD_A, self.same.id), [])
        self.assertEqual(await reader.list(GUILD_B, self.far.id), [])
        self.assertIsNone(await reader.find(GUILD_B, self.far.id, "Hollow King"))

    async def test_the_house_rules_follow_refuses_a_pack(self) -> None:
        await self.publish()
        code = await self.packs.make_pack_code(GUILD_A, self.pub.id, DM_A)
        offer = await self.packs.by_code(GUILD_A, DM_A, code)
        with self.assertRaises(RuleSetError):
            await self.packs.follow(GUILD_A, self.same.id, DM_A, offer)
        self.assertEqual(await self.packs.following(GUILD_A, self.same.id), [])
        await self.packs.follow_pack(GUILD_A, self.same.id, DM_A, offer)
        (follow,) = await self.packs.following(GUILD_A, self.same.id)
        with self.assertRaises(RuleSetError):
            await self.packs.restore(GUILD_A, self.far.id, DM_A, follow)

    async def test_a_backup_with_too_many_packs_is_refused(self) -> None:
        section = FollowedSetsSection()
        content, _ = pack_content.content_of([entry("A")])
        rows = [
            {"set_id": f"{n:032x}", "kind": "homebrew", "name": f"P{n}", "publisher": "x",
             "version": 1, "content": content, "position": n, "by": "7", "at": 1}
            for n in range(PACKS_MAX + 1)
        ]  # fmt: skip
        section.check(rows[:PACKS_MAX])
        with self.assertRaises(CampaignError):
            section.check(rows)

    async def test_a_restore_at_the_limit_and_a_freed_slot(self) -> None:
        for n in range(PACKS_MAX):
            other = await self.campaigns.create(GUILD_A, f"Pub {n}", DM_B)
            await self.brew.add(GUILD_A, other.id, DM_B, kind="item", name=f"I{n}", text="x")
            await self.packs.publish_pack(GUILD_A, other.id, DM_B, f"P{n}", await self.mine(other))
        await self.follow_all(self.same)
        first = (await self.packs.followed_packs(GUILD_A, self.same.id))[0]
        gone, at = await self.packs.unfollow_pack(GUILD_A, self.same.id, DM_A, first.set_id)
        await self.packs.restore_pack(GUILD_A, self.same.id, DM_A, gone, at)
        with self.assertRaises(RuleSetError):  # full again: nothing more fits
            await self.packs.restore_pack(GUILD_A, self.same.id, DM_A, gone, at)

    async def test_a_pack_read_by_the_reader_is_not_republished(self) -> None:
        await self.publish()
        await self.follow_all(self.same)
        await self.brew.add(GUILD_A, self.same.id, DM_A, kind="item", name="Mine", text="x")
        read = await HomebrewReader(self.brew, self.packs).list(GUILD_A, self.same.id)
        self.assertEqual(len(read), 3)  # its own and the pack's two
        content, _ = pack_content.content_of(read)
        self.assertEqual([e["name"] for e in content["entries"]], ["Mine"])

    async def test_the_reader_without_packs_or_with_a_broken_one(self) -> None:
        await self.brew.add(GUILD_A, self.same.id, DM_A, kind="item", name="Mine", text="x")
        plain = HomebrewReader(self.brew, None)
        self.assertEqual([e.name for e in await plain.list(GUILD_A, self.same.id)], ["Mine"])
        self.assertIsNone(await plain.find(GUILD_A, self.same.id, "Hollow King"))

        class Broken(HomebrewPackStore):
            async def pack_copies(self, guild_id: int, campaign_id: str) -> list[Follow]:
                raise RuntimeError("down")

        reader = HomebrewReader(self.brew, Broken(self.db))
        self.assertEqual([e.name for e in await reader.list(GUILD_A, self.same.id)], ["Mine"])
        self.assertIsNone(await reader.find(GUILD_A, self.same.id, "Hollow King"))
        found = await reader.find(GUILD_A, self.same.id, "mine")
        assert found is not None
