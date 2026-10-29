"""Homebrew follow-ups (#1646): a name credit on the card and the entry view, the daily clean-up of
pages nothing uses, waiting counts that skip entries whose page isn't kept, and a pack that is
malformed raising nothing in the follow note."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.rules.homebrew import Homebrew, HomebrewSection, HomebrewStore, credit_label
from dmbot.rules.homebrew_packs import HomebrewReader
from dmbot.rules.homebrew_sources import PAGE_KEEP_S, SiteApprovalsSection, SourceStore
from dmbot.rules.pack_updates import PackUpdateStore
from dmbot.rules.rule_card import homebrew_citation
from dmbot.rules.rule_sets import FollowedSetsSection
from dmbot.ui import homebrew as brew_ui
from dmbot.ui import shared_packs
from tests.pg import DatabaseTest

GUILD = 1001
DM_A, DM_B = 11, 22
NOW = 1_700_000_000
OLD = NOW + PAGE_KEEP_S + 10  # when the pages above are older than the keep time
URLS = [f"https://www.dndbeyond.com/magic-items/{n}-thing" for n in range(1, 6)]


def entry(**kw: Any) -> Homebrew:
    values: dict[str, Any] = {
        "id": 1, "campaign_id": "c", "kind": "item", "name": "Rope", "text": "x", "summary": None,
        "created_by": 1, "created_at": 1, "updated_at": 1,
    }  # fmt: skip
    return Homebrew(**{**values, **kw})


class NameCredit(unittest.TestCase):
    def test_an_upload_credited_to_a_person_shows_by_their_name_escaped(self) -> None:
        e = entry(source_kind="upload", credit_kind="name", credit_text="Jo *the* Bold")
        self.assertEqual(credit_label(e), "by Jo *the* Bold")
        self.assertIn("(by Jo *the* Bold)", homebrew_citation(e, "Camp"))
        view = brew_ui.entry_text(e)
        self.assertIn("by Jo \\*the\\* Bold", view)  # (escaped for Discord)

    def test_the_other_credits_and_no_credit(self) -> None:
        self.assertEqual(credit_label(entry()), "")
        site = entry(source_kind="upload", credit_kind="website", credit_domain="a.org")
        self.assertEqual(credit_label(site), "credited to a.org")
        link = entry(source_kind="link", credit_domain="dndbeyond.com")
        self.assertEqual(credit_label(link), "from dndbeyond.com")
        self.assertNotIn("(by", homebrew_citation(entry(), "Camp"))


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: NOW)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: NOW)
        self.sources = SourceStore(self.db, clock=lambda: NOW)
        self.later = SourceStore(self.db, clock=lambda: OLD)
        self.store = PackUpdateStore(self.db, clock=lambda: NOW)
        self.reader = HomebrewReader(self.brew, self.store, self.sources)
        self.pub = await self.campaigns.create(GUILD, "Publisher", DM_A)
        self.me = await self.campaigns.create(GUILD, "Follower", DM_B)
        await self.sources.approve(GUILD, self.pub.id, DM_A, "dndbeyond.com")
        self.pages = [
            await self.sources.save_page(u, kind="item", name=f"Thing {n}", text="Words.")
            for n, u in enumerate(URLS)
        ]

    async def urls_left(self) -> set[str]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT url FROM homebrew_pages")
            return {r["url"] for r in await cur.fetchall()}


class Cleanup(Base):
    async def test_old_orphans_go_and_pages_in_use_stay(self) -> None:
        # 0: an entry points to it. 1: the publisher's pack names it. 2: a follower's copy names
        # it only. 3: nothing uses it. 4: nothing uses it, but it is new.
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=self.pages[0].id, kind="item",
            name="Thing 0", domain="dndbeyond.com",
        )  # fmt: skip
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=self.pages[1].id, kind="item",
            name="Thing 1", domain="dndbeyond.com",
        )  # fmt: skip
        await self.store.publish_pack(
            GUILD, self.pub.id, DM_A, "Pack", await self.brew.list(GUILD, self.pub.id)
        )
        await self.brew.remove(
            GUILD, self.pub.id, DM_A, (await self.brew.list(GUILD, self.pub.id))[1].id
        )
        (offer,) = await self.store.packs_here(GUILD, self.me.id)
        await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetched_at = %s", (NOW,))
            await conn.execute(
                "UPDATE homebrew_pages SET fetched_at = %s WHERE url = %s", (OLD, URLS[4])
            )
        # Clean up "tomorrow": pages 0..3 are old, 4 was fetched just now.
        self.assertEqual(await self.later.sweep_pages([GUILD]), 2)  # 2 and 3
        self.assertEqual(await self.urls_left(), {URLS[0], URLS[1], URLS[4]})
        self.assertEqual(await self.later.sweep_pages([GUILD]), 0)

    async def test_a_page_only_the_undo_copy_names_is_kept(self) -> None:
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=self.pages[2].id, kind="item",
            name="Thing 2", domain="dndbeyond.com",
        )  # fmt: skip
        await self.store.publish_pack(
            GUILD, self.pub.id, DM_A, "Pack", await self.brew.list(GUILD, self.pub.id)
        )
        (offer,) = await self.store.packs_here(GUILD, self.me.id)
        await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)
        async with self.db.guild(GUILD) as conn:  # the copy moved to "before the last update"
            await conn.execute(
                "UPDATE rule_set_follows SET previous_content = content,"
                " content = '{\"entries\": []}'::jsonb WHERE campaign_id = %s",
                (self.me.id,),
            )
            await conn.execute("DELETE FROM rule_set_versions")
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetched_at = %s", (NOW,))
        await self.later.sweep_pages([GUILD])
        self.assertIn(URLS[2], await self.urls_left())

    async def test_nothing_goes_while_the_pages_are_young_and_it_logs_only_a_count(self) -> None:
        with self.assertLogs("dmbot.rules.homebrew_sources", "INFO") as logged:
            self.assertEqual(await self.sources.sweep_pages([GUILD]), 0)
        self.assertEqual(len(await self.urls_left()), 5)
        self.assertIn("count=0", logged.output[0])
        self.assertNotIn("dndbeyond", logged.output[0])

    async def test_a_page_used_in_another_server_is_kept(self) -> None:
        other = await self.campaigns.create(GUILD + 1, "Elsewhere", DM_A)
        await self.sources.approve(GUILD + 1, other.id, DM_A, "dndbeyond.com")
        await self.brew.add_link(
            GUILD + 1, other.id, DM_A, page_id=self.pages[3].id, kind="item",
            name="Thing 3", domain="dndbeyond.com",
        )  # fmt: skip
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetched_at = 0")
        self.assertEqual(await self.later.sweep_pages([GUILD, GUILD + 1]), 4)
        self.assertEqual(await self.urls_left(), {URLS[3]})


class Waiting(Base):
    async def test_an_entry_whose_page_is_not_kept_here_is_not_waiting(self) -> None:
        for n in (0, 1):
            await self.brew.add_link(
                GUILD, self.pub.id, DM_A, page_id=self.pages[n].id, kind="item",
                name=f"Thing {n}", domain="dndbeyond.com",
            )  # fmt: skip
        await self.store.publish_pack(
            GUILD, self.pub.id, DM_A, "Pack", await self.brew.list(GUILD, self.pub.id)
        )
        (offer,) = await self.store.packs_here(GUILD, self.me.id)
        await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {"dndbeyond.com": 2})
        async with self.db.unscoped() as conn:  # one page is gone, one is only from a backup
            await conn.execute(
                "UPDATE homebrew_pages SET fetch_status = 'restored' WHERE url = %s", (URLS[0],)
            )
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {"dndbeyond.com": 1})
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetch_status = 'restored'")
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {})


class SitesNote(unittest.IsolatedAsyncioTestCase):
    async def test_a_malformed_pack_raises_nothing_and_a_good_one_names_its_sites(self) -> None:
        sources = SimpleNamespace(approved=lambda g, c, s: _false())
        it = SimpleNamespace(client=SimpleNamespace(sources=sources))
        campaign = SimpleNamespace(guild_id=GUILD, id="c")
        for bad in (
            {"entries": "nope"},
            {"entries": [5, {"domain": 3}]},
            {},
            {"entries": [{"kind": "item", "name": "A", "text": "x", "domain": 7}]},
        ):
            self.assertEqual(await shared_packs._sites_note(it, campaign, bad), "")  # type: ignore[arg-type]
        good = {"entries": [{"kind": "item", "name": "A", "text": "x", "summary": None,
                             "known": True, "domain": "a.org"}]}  # fmt: skip
        note = await shared_packs._sites_note(it, campaign, good)  # type: ignore[arg-type]
        self.assertIn("1 entry from a.org will wait", note)


async def _false() -> bool:
    return False
