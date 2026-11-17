"""The wizard's homebrew (#4211): step 8's files and links are read into the new campaign through
the Homebrew page's own paths. A file loads its entries, a file with none is counted and the next
one still loads, a link adds one page (the site approved for the campaign, who and when), a bad
link is counted and the others stand, and nothing is added twice. Needs Postgres."""

from __future__ import annotations

from dmbot import wizard_homebrew
from dmbot.campaigns import CampaignStore
from dmbot.rules.homebrew import HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_import import HomebrewImporter
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from tests.pg import DatabaseTest
from tests.test_homebrew_import import URL, Fetches

GUILD, DM = 111, 7
FILE = "## Hollow King (monster)\nSummary: A crowned skeleton.\n\nIt rules the crypt.\n"
OTHER = "## Frost Blade (item)\n\nCold to the touch.\n"


class WizardHomebrew(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.brew = HomebrewStore(self.db)
        self.sources = SourceStore(self.db)
        self.net = Fetches()
        self.importer = HomebrewImporter(self.brew, self.sources, fetch=self.net)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)

    async def names(self) -> list[str]:
        return sorted(e.name for e in await self.brew.list(GUILD, self.campaign.id))

    async def test_a_file_loads_its_entries_and_a_second_run_adds_nothing(self) -> None:
        docs = [{"text": FILE}, {"text": OTHER}]
        loaded, bad = await wizard_homebrew.read_documents(
            self.brew, GUILD, self.campaign.id, DM, docs
        )
        self.assertEqual((loaded, bad), (2, 0))
        self.assertEqual(await self.names(), ["Frost Blade", "Hollow King"])
        again = await wizard_homebrew.read_documents(self.brew, GUILD, self.campaign.id, DM, docs)
        self.assertEqual(again, (0, 0))
        self.assertEqual(len(await self.names()), 2)

    async def test_a_file_with_no_entry_is_counted_and_the_next_one_still_loads(self) -> None:
        docs = [{"text": "# just a note\n"}, {"text": FILE}]
        loaded, bad = await wizard_homebrew.read_documents(
            self.brew, GUILD, self.campaign.id, DM, docs
        )
        self.assertEqual((loaded, bad), (1, 1))

    async def test_a_link_adds_one_page_and_approves_the_site_with_who_and_when(self) -> None:
        added, failed = await wizard_homebrew.read_links(
            self.importer, self.sources, self.brew, GUILD, self.campaign.id, DM, [URL]
        )
        self.assertEqual((added, failed), (1, 0))
        self.assertEqual(self.net.asked, [URL])
        [approval] = await self.sources.approvals(GUILD, self.campaign.id)
        self.assertEqual(approval.domain, "dndbeyond.com")
        self.assertEqual(approval.approved_by, DM)

    async def test_a_bad_link_is_counted_and_the_good_one_stands(self) -> None:
        added, failed = await wizard_homebrew.read_links(
            self.importer, self.sources, self.brew, GUILD, self.campaign.id, DM,
            ["https://example.com/page", URL, "not a link"],
        )  # fmt: skip
        self.assertEqual((added, failed), (1, 2))

    async def test_the_same_link_twice_adds_one_and_a_retake_does_not_fail(self) -> None:
        added, failed = await wizard_homebrew.read_links(
            self.importer, self.sources, self.brew, GUILD, self.campaign.id, DM, [URL, URL]
        )
        self.assertEqual((added, failed), (1, 0))  # (the second is already there: not a failure)
        self.assertEqual(self.net.asked, [URL])  # (the kept page is re-used, never fetched again)

    async def test_a_retake_with_the_link_already_added_is_not_a_failure(self) -> None:
        args = (self.importer, self.sources, self.brew, GUILD, self.campaign.id, DM, [URL])
        self.assertEqual(await wizard_homebrew.read_links(*args), (1, 0))
        self.assertEqual(await wizard_homebrew.read_links(*args), (0, 0))
