"""Two campaigns, one server: the stores that hold a DM's book text (#1873).

CLAUDE.md, Hard rules, Intellectual property: shared content "stays in that one campaign (never
shared between campaigns or servers) ... It can never go into a shared ruleset." Part one and two
(#1871, #1872) left out the adventure store, the story plan and its money ledger. Two campaigns of
ONE server are the hard case: the row-level security keeps servers apart, so only the queries'
own campaign filter separates them. Each test gives A and C their own book and asserts nothing of
C's comes out of A, with the positive control through C. Needs Postgres; the AI is canned.
"""

from __future__ import annotations

from typing import Any

from dmbot import usage
from dmbot.campaigns import CampaignStore
from dmbot.memory import story_plan as sp
from dmbot.memory.adventure import AdventureError
from dmbot.rules.homebrew import HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_packs import HomebrewReader
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from tests.pg import DatabaseTest
from tests.test_story_plan import (
    BOOK,
    DM,
    GUILD_A,
    Canned,
    Setup,
    answer,
    entry,
)
from tests.test_usage import GUILD_A as USAGE_GUILD
from tests.test_usage import OTHER, OWNER, START, UsageTest

VAULT = "The vault of Tessaly opens only at the third bell of the drowned chapel."
OTHER_BOOK = (
    "# Chapter 1: The Vault\n\n"
    + " ".join([VAULT] * 8)
    + "\n\n# Chapter 2: The Chapel\n\n"
    + " ".join(["The drowned chapel tolls three bells for Tessaly."] * 8)
)
VAULT_READ = answer(
    chapters=[entry("Chapter 1: The Vault", "Chapter 1: The Vault")],
    places=[entry("The vault of Tessaly", "The vault of Tessaly opens only")],
)


class TwoBooks(Setup):
    """`self.a` and `self.c` are two campaigns of GUILD_A; `self.b` is in another server."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.c = await self.campaigns.create(GUILD_A, "Sunken City", DM)
        self.doc_a = await self.book(self.a, BOOK, "keep.txt")
        self.doc_c = await self.book(self.c, OTHER_BOOK, "vault.txt")


class TheAdventureStore(TwoBooks):
    async def test_a_campaign_lists_and_reads_only_its_own_book(self) -> None:
        self.assertEqual(
            [d.id for d in await self.adventure.documents(GUILD_A, self.a.id)], [self.doc_a.id]
        )
        self.assertEqual(await self.adventure.chunks(GUILD_A, self.a.id, self.doc_c.id), [])
        held = await self.adventure.chunks(GUILD_A, self.c.id, self.doc_c.id)  # the control
        self.assertIn("Tessaly", " ".join(chunk.text for chunk in held))

    async def test_removing_through_the_wrong_campaign_removes_nothing(self) -> None:
        with self.assertRaises(AdventureError):
            await self.adventure.remove(GUILD_A, self.a.id, DM, self.doc_c.id)
        self.assertEqual(len(await self.adventure.documents(GUILD_A, self.c.id)), 1)
        await self.adventure.remove(GUILD_A, self.c.id, DM, self.doc_c.id)  # the control
        self.assertEqual(await self.adventure.documents(GUILD_A, self.c.id), [])
        self.assertEqual(len(await self.adventure.documents(GUILD_A, self.a.id)), 1)

    async def test_the_same_file_in_two_campaigns_is_two_copies(self) -> None:
        again = await self.book(self.c, BOOK, "keep.txt")  # C was never given A's file
        self.assertNotEqual(again.id, self.doc_a.id)
        self.assertEqual(len(await self.adventure.documents(GUILD_A, self.c.id)), 2)
        self.assertEqual(len(await self.adventure.documents(GUILD_A, self.a.id)), 1)


class TheStoryPlan(TwoBooks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ai_c = Canned(VAULT_READ, answer(), answer())
        await self.read(self.ai_c, self.doc_c, self.c)
        self.ai_a = Canned(answer(), answer(), answer())
        await self.read(self.ai_a, self.doc_a, self.a)

    async def test_what_was_read_for_one_campaign_is_not_listed_for_another(self) -> None:
        listed, total = await self.plan.listing(GUILD_A, self.a.id, 0, 50)
        self.assertEqual((listed, total), ([], 0))
        self.assertEqual(await self.plan.entries(GUILD_A, self.a.id, self.doc_c.id, 0, 50), ([], 0))
        self.assertEqual(await self.plan.names(GUILD_A, self.a.id, self.doc_c.id, 50), [])
        self.assertEqual(list((await self.plan.counts(GUILD_A, self.a.id)).values()), [])
        self.assertEqual(list(await self.plan.states(GUILD_A, self.a.id)), [self.doc_a.id])

    async def test_the_same_reads_of_the_campaign_that_has_them_do_list_them(self) -> None:
        listed, total = await self.plan.listing(GUILD_A, self.c.id, 0, 50)
        self.assertIn("The vault of Tessaly", [x.title for x in listed])
        self.assertGreater(total, 0)
        self.assertIn(
            "The vault of Tessaly", await self.plan.names(GUILD_A, self.c.id, self.doc_c.id, 50)
        )

    async def test_a_document_of_another_campaign_cannot_be_read_or_started(self) -> None:
        self.assertEqual(await self.plan.chunks_from(GUILD_A, self.a.id, self.doc_c.id, 0, 10), [])
        with self.assertRaises(AdventureError):
            await self.plan.begin(GUILD_A, self.a.id, DM, self.doc_c.id)
        with self.assertRaises(AdventureError):
            await self.plan.reserve(GUILD_A, self.a.id, self.doc_c.id, 1)

    async def test_what_the_reader_sent_the_ai_is_the_one_books(self) -> None:
        sent_for_a = " ".join(text for _, text in self.ai_a.calls)
        self.assertNotIn("Tessaly", sent_for_a)
        self.assertIn("Tessaly", " ".join(text for _, text in self.ai_c.calls))  # the control


class TheMoneyLedger(TwoBooks):
    """The cost of reading is counted per campaign and per file, never across them."""

    async def test_one_campaigns_spend_does_not_count_against_another(self) -> None:
        # Both campaigns hold the same file (`keep.txt`) so the per-file ledger key is the same.
        same_in_c = await self.book(self.c, BOOK, "keep.txt")
        held = await self.plan.reserve(GUILD_A, self.c.id, same_in_c.id, 2_000_000)
        await self.plan.settle(GUILD_A, self.c.id, held.id, 2_000_000)
        spent_c = await self.plan.spent(GUILD_A, self.c.id, same_in_c.id)
        self.assertEqual(spent_c, sp.Spent(2_000_000, 2_000_000))  # the control: it was counted
        self.assertEqual(await self.plan.spent(GUILD_A, self.a.id, self.doc_a.id), sp.Spent(0, 0))

    async def test_a_published_ruleset_never_carries_book_text(self) -> None:
        from dmbot.rules.house import HouseRuleStore
        from dmbot.rules.rule_sets import RuleSetStore

        houses = HouseRuleStore(self.db, clock=lambda: self.now)
        sets = RuleSetStore(self.db, clock=lambda: self.now)
        await houses.add(GUILD_A, self.c.id, DM, "No flanking")
        await sets.publish(
            GUILD_A, self.c.id, DM, "Sunken rules", await houses.list(GUILD_A, self.c.id), None
        )
        (offer,) = await sets.shared_here(GUILD_A, self.a.id)
        for leak in ("Tessaly", "drowned chapel", VAULT[:30]):
            self.assertNotIn(leak, repr(offer.content))
        self.assertEqual([r["rule"] for r in offer.content["rules"]], ["No flanking"])


class HomebrewSetup(DatabaseTest):
    """Three campaigns (two in one server) that each imported the same published page."""

    URL = "https://www.dndbeyond.com/magic-items/2302039-amulet-of-trueshot"
    SITE = "dndbeyond.com"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.brew = HomebrewStore(self.db)
        self.sources = SourceStore(self.db, clock=lambda: 1_700_000_000)
        self.a = await self.campaigns.create(GUILD_A, "Frozen Wastes", DM)
        self.c = await self.campaigns.create(GUILD_A, "Sunken City", DM)
        self.far = await self.campaigns.create(222, "Far Away", DM)
        page = await self.sources.save_page(
            self.URL, name="Amulet of Trueshot", kind="item", text="Aim better.", summary="A charm."
        )
        self.entries = {}
        for campaign in (self.a, self.c, self.far):
            self.entries[campaign.id] = await self.brew.add_link(
                campaign.guild_id, campaign.id, DM,
                page_id=page.id, kind=page.kind, name=page.name, domain=page.domain,
            )  # fmt: skip

    async def usable(self, campaign: Any) -> list[str]:
        found = await self.brew.list(campaign.guild_id, campaign.id, usable_only=True)
        return [entry.name for entry in found]


class SharedHomebrewByLink(HomebrewSetup):
    """Published homebrew by link is the one allowed sharing (owner, 2026-10-16): one copy per
    address, which each campaign points to. What stays the campaign's own: whether it approved the
    site (and who said yes, and when), its edits to the entry, and whether it turned the entry off.
    """

    async def test_the_address_is_shared_but_an_approval_is_one_campaigns_own(self) -> None:
        self.assertEqual(
            {e.page_id for e in self.entries.values()}, {self.entries[self.a.id].page_id}
        )  # one page for all three
        await self.sources.approve(GUILD_A, self.a.id, DM, self.SITE)
        self.assertTrue(await self.sources.approved(GUILD_A, self.a.id, self.SITE))
        for other in (self.c, self.far):
            self.assertFalse(await self.sources.approved(other.guild_id, other.id, self.SITE))
            self.assertEqual(await self.sources.approvals(other.guild_id, other.id), [])
            self.assertEqual(await self.usable(other), [])  # not used where it was not approved
        self.assertEqual(await self.usable(self.a), ["Amulet of Trueshot"])  # the control

    async def test_revoking_through_another_campaign_revokes_nothing(self) -> None:
        for campaign in (self.a, self.c):
            await self.sources.approve(campaign.guild_id, campaign.id, DM, self.SITE)
        self.assertFalse(await self.sources.revoke(self.far.guild_id, self.far.id, DM, self.SITE))
        self.assertTrue(await self.sources.revoke(GUILD_A, self.c.id, DM, self.SITE))
        self.assertTrue(await self.sources.approved(GUILD_A, self.a.id, self.SITE))  # A keeps it
        self.assertFalse(await self.sources.approved(GUILD_A, self.c.id, self.SITE))

    async def test_who_approved_and_when_is_not_shown_to_another_campaign(self) -> None:
        await self.sources.approve(GUILD_A, self.a.id, DM, self.SITE)
        (mine,) = await self.sources.approvals(GUILD_A, self.a.id)
        self.assertEqual((mine.domain, mine.approved_by), (self.SITE, DM))
        self.assertEqual(await self.sources.approvals(GUILD_A, self.c.id), [])

    async def test_one_campaigns_edit_or_switch_off_does_not_reach_the_other(self) -> None:
        for campaign in (self.a, self.c):
            await self.sources.approve(campaign.guild_id, campaign.id, DM, self.SITE)
        mine = self.entries[self.c.id]
        await self.brew.set_enabled(GUILD_A, self.c.id, DM, mine.id, False)
        self.assertEqual(await self.usable(self.c), [])  # C turned it off
        self.assertEqual(await self.usable(self.a), ["Amulet of Trueshot"])  # A did not
        await self.brew.set_enabled(GUILD_A, self.c.id, DM, mine.id, True)
        # C's own typed entry, and an edit of it, never show in A.
        own = await self.brew.add(GUILD_A, self.c.id, DM, kind="item", name="Charm", text="C only")
        await self.brew.edit(
            GUILD_A, self.c.id, DM, own.id, kind="item", name="Charm", text="C only, edited"
        )
        self.assertEqual(await self.usable(self.a), ["Amulet of Trueshot"])
        self.assertIsNone(await self.brew.get(GUILD_A, self.a.id, own.id))  # nor reachable by id
        theirs = await self.brew.get(GUILD_A, self.a.id, self.entries[self.a.id].id)
        assert theirs is not None
        self.assertEqual((theirs.name, theirs.text), ("Amulet of Trueshot", "Aim better."))


class TheHoursMeter(UsageTest):
    """Listening minutes are an owner's: another owner's minutes in the same month, even in the
    same server and campaign list, never count against them."""

    async def test_one_owners_minutes_do_not_count_against_another(self) -> None:
        theirs = await self.campaigns.create(USAGE_GUILD, "Elsewhere", OTHER)
        month = await self.add(USAGE_GUILD, self.a.id, OWNER, 50)
        await self.add(USAGE_GUILD, theirs.id, OTHER, 500)
        self.assertEqual(await self.owner_minutes(OWNER, month), 50)  # the control
        self.assertEqual(await self.owner_minutes(OTHER, month), 500)
        self.assertEqual(await usage.session_minutes(self.db, USAGE_GUILD, self.a.id, START), 50)
        self.assertEqual(await usage.session_minutes(self.db, USAGE_GUILD, theirs.id, START), 500)


class FollowingAHomebrewPack(HomebrewSetup):
    """A pack is shared homebrew. A campaign that follows one reads the publisher's typed entries,
    and entries pointing to a published page only once it has approved that site itself: the
    publisher's approval does not travel with the pack."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.rules.homebrew_packs import HomebrewPackStore

        self.packs = HomebrewPackStore(self.db, clock=lambda: 1_700_000_000)
        self.reader = HomebrewReader(self.brew, self.packs, self.sources)
        self.d = await self.campaigns.create(GUILD_A, "Follower", DM)
        await self.sources.approve(GUILD_A, self.a.id, DM, self.SITE)  # the publisher approves
        await self.brew.add(
            GUILD_A, self.a.id, DM, kind="monster", name="Hollow King", text="A crowned skeleton."
        )
        mine = await self.brew.list(GUILD_A, self.a.id)
        await self.packs.publish_pack(GUILD_A, self.a.id, DM, "Frost Pack", mine)
        (offer,) = [
            o for o in await self.packs.packs_here(GUILD_A, self.d.id) if o.name == "Frost Pack"
        ]
        await self.packs.follow_pack(GUILD_A, self.d.id, DM, offer)

    async def names(self, campaign: Any) -> set[str]:
        return {e.name for e in await self.reader.list(campaign.guild_id, campaign.id)}

    async def test_the_followers_gets_the_typed_entries_but_not_the_publishers_approval(
        self,
    ) -> None:
        names = await self.names(self.d)
        self.assertIn("Hollow King", names)
        self.assertNotIn("Amulet of Trueshot", names)  # the publisher approved the site, not it
        self.assertEqual(await self.sources.approvals(GUILD_A, self.d.id), [])
        self.assertFalse(await self.sources.approved(GUILD_A, self.d.id, self.SITE))

    async def test_after_it_approves_the_site_itself_the_linked_entry_is_read(self) -> None:
        await self.sources.approve(GUILD_A, self.d.id, DM, self.SITE)
        self.assertIn("Amulet of Trueshot", await self.names(self.d))

    async def test_a_campaign_that_does_not_follow_the_pack_reads_none_of_it(self) -> None:
        self.assertNotIn("Hollow King", await self.names(self.c))
        self.assertNotIn("Hollow King", await self.names(self.far))
