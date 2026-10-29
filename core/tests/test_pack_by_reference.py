"""Packs carry imported entries by reference (#1635): a pointer to the page and nothing of its
words, an upload credited to a website carries its site, a follower uses neither until it has
approved the site, an update with a new site waits the same way, and a declined change that holds
a pointer lands in the campaign's own homebrew as the same page."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.rules import pack_content, pack_diff
from dmbot.rules.homebrew import Homebrew, HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_packs import HomebrewReader
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from dmbot.rules.pack_updates import PackUpdateStore
from dmbot.rules.rule_sets import FollowedSetsSection
from tests.pg import DatabaseTest

GUILD = 1001
DM_A, DM_B = 11, 22
URL = "https://www.dndbeyond.com/magic-items/1-moonlit-charm"
WORDS = "Words only the page holds."


def entry(name: str = "Charm", **kw: Any) -> Homebrew:
    values: dict[str, Any] = {
        "id": 1, "campaign_id": "c", "kind": "item", "name": name, "text": "x", "summary": None,
        "created_by": 1, "created_at": 1, "updated_at": 1,
    }  # fmt: skip
    return Homebrew(**{**values, **kw})


class Shapes(unittest.TestCase):
    def test_a_link_entry_is_a_pointer_with_none_of_its_words(self) -> None:
        link = entry("Charm", source_kind="link", page_url=URL, credit_domain="dndbeyond.com",
                     text=WORDS, summary="A summary.")  # fmt: skip
        site = entry("Rope", source_kind="upload", credit_kind="website", credit_domain="a.org")
        plain = entry("Lamp")
        book = entry("Booked", from_book=True)
        content, left = pack_content.content_of([link, site, plain, book])
        by = {e["name"]: e for e in content["entries"]}
        self.assertEqual(left, ["Booked"])
        self.assertEqual(
            by["Charm"],
            {"kind": "item", "name": "Charm", "summary": None, "text": None, "known": True,
             "page": URL, "domain": "dndbeyond.com"},
        )  # fmt: skip
        self.assertNotIn(WORDS, json.dumps(content))
        self.assertNotIn("A summary.", json.dumps(content))
        self.assertEqual((by["Rope"]["text"], by["Rope"]["domain"]), ("x", "a.org"))
        self.assertNotIn("domain", by["Lamp"])
        self.assertNotIn("page", by["Lamp"])

    def test_the_shape_is_checked_for_pointers_and_sites(self) -> None:
        good, _ = pack_content.content_of(
            [entry("Charm", source_kind="link", page_url=URL, credit_domain="dndbeyond.com")]
        )
        self.assertEqual(pack_content.valid_content(good), good)
        pointer = good["entries"][0]
        bad: list[dict[str, Any]] = [
            {**pointer, "text": "words with a pointer"},
            {**pointer, "summary": "a summary with a pointer"},
            {**pointer, "page": "http://www.dndbeyond.com/a"},
            {**pointer, "page": "https://dndbeyond.com/not/normal/"},
            {**pointer, "domain": "evil.example"},
            {**pointer, "domain": None},
            {**pointer, "page": 5},
            {"kind": "item", "name": "X", "summary": None, "text": "t", "domain": "WWW.A.org"},
            {"kind": "item", "name": "X", "summary": None, "text": "t", "domain": 7},
        ]
        for entry_ in bad:
            with self.subTest(entry_=entry_), self.assertRaises(pack_content.BadPack):
                pack_content.valid_content({"entries": [entry_]})

    def test_a_link_entry_without_a_usable_pointer_is_left_out_not_sent_as_words(self) -> None:
        broken = entry("Charm", source_kind="link", page_url=None, credit_domain="dndbeyond.com",
                       text=WORDS)  # fmt: skip
        content, left = pack_content.content_of([broken, entry("Lamp")])
        self.assertEqual(left, ["Charm"])
        self.assertNotIn(WORDS, json.dumps(content))
        self.assertEqual([e["name"] for e in content["entries"]], ["Lamp"])

    def test_a_pack_cannot_name_a_long_list_of_sites(self) -> None:
        many = {
            "entries": [
                {"kind": "item", "name": f"E{n}", "summary": None, "text": "t",
                 "domain": f"s{n}.example.com"}
                for n in range(pack_content.SITES_MAX + 1)
            ]
        }  # fmt: skip
        with self.assertRaises(pack_content.BadPack):
            pack_content.valid_content(many)
        many["entries"].pop()
        pack_content.valid_content(many)  # (five sites are fine)

    def test_an_older_pack_without_sites_still_reads(self) -> None:
        old = {
            "entries": [
                {"kind": "item", "name": "Lamp", "summary": None, "text": "x", "known": False}
            ]
        }
        out = pack_content.valid_content(old)["entries"][0]
        self.assertNotIn("domain", out)
        self.assertNotIn("page", out)

    def test_the_diff_keeps_pointers_and_describes_them(self) -> None:
        old, _ = pack_content.content_of([])
        new, _ = pack_content.content_of(
            [entry("Charm", source_kind="link", page_url=URL, credit_domain="dndbeyond.com")]
        )
        (change,) = pack_diff.diff(old, new)
        self.assertEqual(change.kind, "added")
        self.assertEqual(change.entry["page"], URL)
        self.assertIsNone(change.entry["text"])
        self.assertEqual(pack_diff.describe(change), "New: Charm, from dndbeyond.com")
        applied = pack_diff.apply(old, [change])
        self.assertEqual(pack_diff.entries_of(applied)["charm"]["page"], URL)


class ByReference(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: 1_700_000_000)
        self.sources = SourceStore(self.db, clock=lambda: 1_700_000_000)
        self.store = PackUpdateStore(self.db, clock=lambda: 1_700_000_000)
        self.reader = HomebrewReader(self.brew, self.store, self.sources)
        self.pub = await self.campaigns.create(GUILD, "Publisher", DM_A)
        self.me = await self.campaigns.create(GUILD, "Follower", DM_B)
        self.page = await self.sources.save_page(
            URL, kind="item", name="Moonlit Charm", text=WORDS, summary="A charm."
        )
        await self.sources.approve(GUILD, self.pub.id, DM_A, "dndbeyond.com")
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=self.page.id, kind="item",
            name=self.page.name, domain="dndbeyond.com",
        )  # fmt: skip
        await self.brew.add(GUILD, self.pub.id, DM_A, kind="item", name="Lamp", text="Own words.")

    async def publish(self) -> None:
        await self.store.publish_pack(
            GUILD, self.pub.id, DM_A, "Frost Pack", await self.brew.list(GUILD, self.pub.id)
        )

    async def follow(self) -> Any:
        (offer,) = await self.store.packs_here(GUILD, self.me.id)
        return await self.store.follow_pack(GUILD, self.me.id, DM_B, offer)

    async def test_the_pack_holds_a_pointer_not_the_words_and_a_follower_waits_for_the_site(
        self,
    ) -> None:
        await self.publish()
        stored = await self.store.pack_content(GUILD, self.pub.id)
        assert stored is not None
        self.assertNotIn(WORDS, json.dumps(stored))
        follow = await self.follow()
        self.assertNotIn(WORDS, json.dumps(follow.content))  # the follower's copy has none either
        self.assertEqual([e.name for e in await self.reader.list(GUILD, self.me.id)], ["Lamp"])
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {"dndbeyond.com": 1})
        # The publisher is unaffected (it approved the site itself).
        self.assertEqual(await self.reader.waiting(GUILD, self.pub.id), {})
        # Approving the site turns the entry on, with no other step, and it reads from the page.
        await self.sources.approve(GUILD, self.me.id, DM_B, "dndbeyond.com")
        got = {e.name: e for e in await self.reader.list(GUILD, self.me.id)}
        self.assertEqual(set(got), {"Lamp", "Moonlit Charm"})
        charm = got["Moonlit Charm"]
        self.assertEqual(
            (charm.text, charm.summary, charm.credit_domain, charm.page_url),
            (WORDS, "A charm.", "dndbeyond.com", URL),
        )
        self.assertEqual(charm.source, "Frost Pack homebrew, v1")
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {})
        found = await self.reader.find(GUILD, self.me.id, "moonlit charm")
        assert found is not None
        self.assertEqual(found.text, WORDS)
        # Taking the approval back stops the entry again.
        await self.sources.revoke(GUILD, self.me.id, DM_B, "dndbeyond.com")
        self.assertEqual([e.name for e in await self.reader.list(GUILD, self.me.id)], ["Lamp"])

    async def test_a_pointer_takes_its_name_and_kind_from_the_page_it_points_to(self) -> None:
        await self.publish()
        await self.follow()
        await self.sources.approve(GUILD, self.me.id, DM_B, "dndbeyond.com")
        async with self.db.guild(GUILD) as conn:  # a publisher who relabels the entry
            await conn.execute(
                "UPDATE rule_set_versions SET content = replace(content::text, 'Moonlit Charm',"
                " 'Fireball')::jsonb"
            )
            await conn.execute(
                "UPDATE rule_set_follows SET content = replace(content::text, 'Moonlit Charm',"
                " 'Fireball')::jsonb"
            )
        names = sorted(e.name for e in await self.reader.list(GUILD, self.me.id))
        self.assertEqual(names, ["Lamp", "Moonlit Charm"])  # the page says what it is

    async def test_a_page_the_pack_points_to_that_is_not_kept_is_skipped_not_fatal(self) -> None:
        await self.publish()
        await self.follow()
        await self.sources.approve(GUILD, self.me.id, DM_B, "dndbeyond.com")
        async with self.db.unscoped() as conn:  # a page recreated from a backup is not trusted
            await conn.execute("UPDATE homebrew_pages SET fetch_status = 'restored'")
        self.assertEqual([e.name for e in await self.reader.list(GUILD, self.me.id)], ["Lamp"])

    async def test_an_upload_credited_to_a_website_waits_for_that_site_too(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE homebrew_entries SET source_kind = 'upload', credit_kind = 'website',"
                " credit_domain = 'example.org' WHERE name = 'Lamp' AND campaign_id = %s",
                (self.pub.id,),
            )
        await self.sources.approve(GUILD, self.pub.id, DM_A, "example.org")
        await self.publish()
        await self.follow()
        self.assertEqual(
            await self.reader.waiting(GUILD, self.me.id), {"dndbeyond.com": 1, "example.org": 1}
        )
        self.assertEqual(await self.reader.list(GUILD, self.me.id), [])
        await self.sources.approve(GUILD, self.me.id, DM_B, "example.org")
        (lamp,) = await self.reader.list(GUILD, self.me.id)
        self.assertEqual(
            (lamp.name, lamp.text, lamp.credit_domain), ("Lamp", "Own words.", "example.org")
        )

    async def test_an_update_with_a_new_site_waits_and_an_approved_site_asks_nothing(self) -> None:
        await self.publish()
        await self.follow()
        await self.sources.approve(GUILD, self.me.id, DM_B, "dndbeyond.com")
        page2 = await self.sources.save_page(
            "https://www.dndbeyond.com/spells/2-spark", kind="spell", name="Spark", text="Sparks."
        )
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=page2.id, kind="spell", name="Spark",
            domain="dndbeyond.com",
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:  # a new site: an upload credited to a website
            await conn.execute(
                "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key, text,"
                " created_by, added_by, created_at, updated_at, source_kind, credit_kind,"
                " credit_domain) VALUES (%s, %s, 'item', 'Rope', 'rope', 'Rope.', %s, %s, 2, 2,"
                " 'upload', 'website', 'other.example')",
                (GUILD, self.pub.id, DM_A, DM_A),
            )
        await self.sources.approve(GUILD, self.pub.id, DM_A, "other.example")
        await self.store.publish_pack_changes(
            GUILD, self.pub.id, DM_A, await self.brew.list(GUILD, self.pub.id)
        )
        (update,) = await self.store.pending_pack_updates(GUILD, self.me.id)
        await self.store.take_pack_update(
            GUILD,
            self.me.id,
            None,
            update.follow.set_id,
            None,
            expected_version=update.offer.version,
        )
        names = sorted(e.name for e in await self.reader.list(GUILD, self.me.id))
        self.assertEqual(
            names, ["Lamp", "Moonlit Charm", "Spark"]
        )  # the approved site is on at once
        self.assertEqual(await self.reader.waiting(GUILD, self.me.id), {"other.example": 1})

    async def test_a_declined_change_with_a_pointer_lands_as_the_same_page(self) -> None:
        await self.publish()
        await self.follow()
        page2 = await self.sources.save_page(
            "https://www.dndbeyond.com/spells/2-spark", kind="spell", name="Spark", text="Sparks."
        )
        await self.brew.add_link(
            GUILD, self.pub.id, DM_A, page_id=page2.id, kind="spell", name="Spark",
            domain="dndbeyond.com",
        )  # fmt: skip
        await self.store.publish_pack_changes(
            GUILD, self.pub.id, DM_A, await self.brew.list(GUILD, self.pub.id)
        )
        (update,) = await self.store.pending_pack_updates(GUILD, self.me.id)
        taken = await self.store.take_pack_update(
            GUILD, self.me.id, DM_B, update.follow.set_id, accepted=set(),
            expected_version=update.offer.version,
        )  # fmt: skip
        self.assertTrue(taken.merged)
        mine = {e.name: e for e in await self.brew.list(GUILD, self.me.id)}
        # The old copy's entries, now the campaign's own (the unfollowed pack's copy goes).
        self.assertEqual(set(mine), {"Lamp", "Moonlit Charm"})
        charm = mine["Moonlit Charm"]
        self.assertEqual(
            (charm.source_kind, charm.page_id, charm.text), ("link", self.page.id, WORDS)
        )
        self.assertEqual(charm.credit_domain, "dndbeyond.com")


if __name__ == "__main__":
    unittest.main()
