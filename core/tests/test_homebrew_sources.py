"""Where homebrew comes from, and the once-per-site approval (#1633): the database refuses a source
with the wrong fields, an old entry becomes "typed" by its maker, an approval belongs to one
campaign, two campaigns importing one address share one page, a link entry is used only while its
site is approved, and the source and the page travel in a backup."""

from __future__ import annotations

import unittest
from typing import Any

from psycopg import errors

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.rules import pack_content
from dmbot.rules.homebrew import FileEntry, HomebrewError, HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from dmbot.rules.site_urls import normalise_domain, normalise_url
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, PLAYER = 7, 9
URL = "https://www.dndbeyond.com/magic-items/2302039-amulet-of-trueshot"


class Addresses(unittest.TestCase):
    def test_a_site_is_one_name_whatever_its_case_or_www(self) -> None:
        self.assertEqual(normalise_domain("WWW.DNDBeyond.com."), "dndbeyond.com")
        for bad in (
            "", "localhost", "10.0.0.1", "a b.com", "bad_name.com", "-a.com", "x" * 300,
            "0x7f.0.0.1", "127.1", "example.0x7f", "example.123",  # read as an IP address
            "\u212a.com",  # the Kelvin sign: folds to an ASCII k
            "caf\u00e9.com",
        ):  # fmt: skip
            self.assertIsNone(normalise_domain(bad), bad)
        self.assertEqual(normalise_domain("www.com"), "www.com")  # (a site called www)

    def test_an_address_is_https_with_a_plain_host_and_no_fragment(self) -> None:
        self.assertEqual(normalise_url(URL + "/#top"), (URL, "dndbeyond.com"))
        self.assertEqual(
            normalise_url("HTTPS://Example.com:443/a/b/?x=1"),
            ("https://example.com/a/b?x=1", "example.com"),
        )
        for bad in (
            "http://example.com/a",
            "https://user:pw@example.com/a",
            "https://example.com:8443/a",
            "https://127.0.0.1/a",
            "ftp://example.com",
            "https://exa mple.com",
            "javascript:alert(1)",
            "",
            "https://example.com/" + "a" * 2000,
        ):
            self.assertIsNone(normalise_url(bad), bad)


class SourcesTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.brew = HomebrewStore(self.db)
        self.sources = SourceStore(self.db)
        self.one = await self.campaigns.create(GUILD_A, "One", DM)
        self.two = await self.campaigns.create(GUILD_A, "Two", DM)
        self.far = await self.campaigns.create(GUILD_B, "Far", DM)

    async def page(self, url: str = URL, name: str = "Amulet of Trueshot", **kw: Any) -> Any:
        values: dict[str, Any] = {"kind": "item", "text": "Aim better.", "summary": "A bow charm."}
        return await self.sources.save_page(url, name=name, **{**values, **kw})

    async def link(self, campaign: Any, user: int = DM) -> Any:
        page = await self.page()
        return await self.brew.add_link(
            campaign.guild_id, campaign.id, user,
            page_id=page.id, kind=page.kind, name=page.name, domain=page.domain,
        )  # fmt: skip

    async def raw(self, **fields: Any) -> None:
        """Insert one entry row straight into the table (what the CHECK sees)."""
        base: dict[str, Any] = {
            "guild_id": GUILD_A, "campaign_id": self.one.id, "kind": "item", "name": "X",
            "name_key": "x", "text": "words", "created_by": DM, "created_at": 1,
            "updated_at": 1, "added_by": DM,
        }  # fmt: skip
        row = {**base, **fields}
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                f"INSERT INTO homebrew_entries ({', '.join(row)}) VALUES"
                f" ({', '.join(['%s'] * len(row))})",
                list(row.values()),
            )


class TheCheck(SourcesTest):
    async def test_each_source_has_exactly_the_fields_it_needs(self) -> None:
        page = await self.page()
        await self.raw(name="A", name_key="a")  # typed: the default
        await self.raw(  # upload by the uploader, by a website, by a typed name
            name="B", name_key="b", source_kind="upload", credit_kind="user"
        )
        await self.raw(
            name="C", name_key="c", source_kind="upload", credit_kind="website",
            credit_domain="example.com",
        )  # fmt: skip
        await self.raw(
            name="D", name_key="d", source_kind="upload", credit_kind="name", credit_text="Sam"
        )
        await self.raw(  # a link: no words of its own
            name="E", name_key="e", source_kind="link", text=None, page_id=page.id,
            credit_domain="dndbeyond.com",
        )  # fmt: skip
        bad: list[dict[str, Any]] = [
            {"page_id": page.id},  # typed with a page
            {"credit_kind": "user"},  # typed with a credit
            {"source_kind": "upload"},  # upload with no credit
            {"source_kind": "upload", "credit_kind": "website"},  # a website with no domain
            {"source_kind": "upload", "credit_kind": "name"},  # a name with no text
            {"source_kind": "upload", "credit_kind": "user", "credit_text": "Sam"},
            {"source_kind": "upload", "credit_kind": "user", "page_id": page.id},
            {"source_kind": "link", "text": None},  # link with no page
            {"source_kind": "link", "text": None, "page_id": page.id},  # no domain
            {"source_kind": "link", "page_id": page.id, "credit_domain": "dndbeyond.com"},  # text
            {"source_kind": "link", "text": None, "page_id": page.id, "credit_domain": "d.com",
             "credit_kind": "user"},
            {"source_kind": "typed", "text": None},  # typed with no words
            {"source_kind": "other"},
        ]  # fmt: skip
        for n, fields in enumerate(bad):
            with self.subTest(fields=fields), self.assertRaises(errors.CheckViolation):
                await self.raw(name=f"Bad{n}", name_key=f"bad{n}", **fields)

    async def test_the_pages_table_checks_its_content(self) -> None:
        async with self.db.unscoped() as conn:
            with self.assertRaises(errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO homebrew_pages (url, domain, kind, name, text, fetched_at)"
                    " VALUES ('https://a.com/x', 'a.com', 'rule', 'N', 't', 1)"
                )


class Saving(SourcesTest):
    async def test_a_typed_entry_is_typed_by_its_dm_and_a_file_is_an_upload_by_its_loader(
        self,
    ) -> None:
        typed = await self.brew.add(GUILD_A, self.one.id, DM, kind="item", name="Lamp", text="x")
        self.assertEqual((typed.source_kind, typed.added_by), ("typed", DM))
        self.assertEqual((typed.credit_kind, typed.page_id, typed.page_url), (None, None, None))
        await self.brew.load_file(GUILD_A, self.one.id, DM, [FileEntry("item", "Rope", "Long.")])
        rope = await self.brew.find(GUILD_A, self.one.id, "rope")
        assert rope is not None
        self.assertEqual(
            (rope.source_kind, rope.added_by, rope.credit_kind), ("upload", DM, "user")
        )
        self.assertEqual((rope.credit_text, rope.credit_domain), (None, None))

    async def test_a_link_entry_reads_its_words_from_its_page_and_cannot_be_edited(self) -> None:
        entry = await self.link(self.one)
        self.assertEqual(
            (entry.source_kind, entry.text, entry.summary, entry.page_url, entry.credit_domain),
            ("link", "Aim better.", "A bow charm.", URL.replace("/#top", ""), "dndbeyond.com"),
        )
        with self.assertRaisesRegex(HomebrewError, "comes from a link"):
            await self.brew.edit(
                GUILD_A, self.one.id, DM, entry.id, kind="item", name="Other", text="Mine."
            )
        # A file naming it leaves it as it is (its words are the page's, not a file's).
        done = await self.brew.load_file(
            GUILD_A, self.one.id, DM, [FileEntry("item", "Amulet of Trueshot", "Different.")]
        )
        self.assertEqual((done.added, done.updated, done.same), (0, 0, 1))
        again = await self.brew.get(GUILD_A, self.one.id, entry.id)
        assert again is not None
        self.assertEqual(again.text, "Aim better.")

    async def test_removing_and_putting_a_link_entry_back_keeps_its_page(self) -> None:
        entry = await self.link(self.one)
        gone = await self.brew.remove(GUILD_A, self.one.id, DM, entry.id)
        back = await self.brew.put_back(GUILD_A, self.one.id, DM, gone)
        self.assertEqual(
            (back.source_kind, back.page_id, back.text), ("link", entry.page_id, "Aim better.")
        )
        self.assertEqual(back.added_by, DM)

    async def test_only_a_dm_adds_a_link_and_the_name_is_unique(self) -> None:
        page = await self.page()
        with self.assertRaises(HomebrewError):
            await self.brew.add_link(
                GUILD_A, self.one.id, PLAYER, page_id=page.id, kind="item", name="X",
                domain=page.domain,
            )  # fmt: skip
        await self.link(self.one)
        with self.assertRaisesRegex(HomebrewError, "already has homebrew with that name"):
            await self.link(self.one)

    async def test_a_link_travels_in_a_pack_as_a_pointer_and_a_websites_upload_names_its_site(
        self,
    ) -> None:
        entry = await self.link(self.one)
        typed = await self.brew.add(GUILD_A, self.one.id, DM, kind="item", name="Lamp", text="x")
        await self.raw(
            name="Rope", name_key="rope", source_kind="upload", credit_kind="website",
            credit_domain="example.org",
        )  # fmt: skip
        everything = await self.brew.list(GUILD_A, self.one.id)
        content, left = pack_content.content_of(everything)
        by = {e["name"]: e for e in content["entries"]}
        self.assertEqual(left, [])
        self.assertEqual((by[entry.name]["text"], by[entry.name]["page"]), (None, entry.page_url))
        self.assertEqual(by["Rope"]["domain"], "example.org")
        self.assertNotIn("domain", by["Lamp"])
        assert typed.id


class Pages(SourcesTest):
    async def test_two_campaigns_importing_one_address_share_one_page(self) -> None:
        a = await self.link(self.one)
        b = await self.link(self.far)  # another server too
        self.assertEqual(a.page_id, b.page_id)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM homebrew_pages")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)
        # Fetching it again replaces the one copy: both campaigns read the new words.
        await self.page(text="Aim much better.")
        for c, e in ((self.one, a), (self.far, b)):
            now = await self.brew.get(c.guild_id, c.id, e.id)
            assert now is not None
            self.assertEqual(now.text, "Aim much better.")

    async def test_the_same_address_written_two_ways_is_one_page(self) -> None:
        first = await self.page(URL)
        second = await self.page(URL.replace("www.", "WWW.") + "/")
        self.assertEqual(first.id, second.id)
        found = await self.sources.page_for(URL + "#frag")
        assert found is not None
        self.assertEqual(found.id, first.id)
        self.assertIsNone(await self.sources.page_for("https://nowhere.example.com/x"))
        self.assertIsNone(await self.sources.page_for("http://www.dndbeyond.com/x"))

    async def test_a_bad_address_or_content_is_refused(self) -> None:
        with self.assertRaises(HomebrewError):
            await self.page("http://example.com/x")
        with self.assertRaises(HomebrewError):
            await self.page(kind="rule")
        with self.assertRaises(HomebrewError):
            await self.page(text="")

    async def test_the_website_role_has_no_grant_on_the_pages(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT has_table_privilege('dmbot_web', 'homebrew_pages', 'SELECT') AS can"
            )
            row = await cur.fetchone()
        self.assertFalse((row or {}).get("can", False))


class Approvals(SourcesTest):
    async def test_an_approval_belongs_to_one_campaign_and_one_server(self) -> None:
        got = await self.sources.approve(GUILD_A, self.one.id, DM, "dndbeyond.com")
        self.assertEqual((got.domain, got.approved_by), ("dndbeyond.com", DM))
        self.assertTrue(await self.sources.approved(GUILD_A, self.one.id, "dndbeyond.com"))
        self.assertFalse(await self.sources.approved(GUILD_A, self.two.id, "dndbeyond.com"))
        self.assertFalse(await self.sources.approved(GUILD_B, self.far.id, "dndbeyond.com"))
        self.assertEqual(await self.sources.approvals(GUILD_A, self.two.id), [])
        # The other campaign of the same server cannot even see the row.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM site_approvals")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM site_approvals")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_only_a_dm_approves_and_a_second_approval_changes_nothing(self) -> None:
        with self.assertRaises(HomebrewError):
            await self.sources.approve(GUILD_A, self.one.id, PLAYER, "dndbeyond.com")
        first = await self.sources.approve(GUILD_A, self.one.id, DM, "dndbeyond.com")
        again = await self.sources.approve(GUILD_A, self.one.id, DM, "dndbeyond.com")
        self.assertEqual(first, again)
        for bad in ("WWW.dndbeyond.com", "http://dndbeyond.com", "", "10.0.0.1"):
            with self.assertRaises(HomebrewError):
                await self.sources.approve(GUILD_A, self.one.id, DM, bad)

    async def test_a_link_entry_is_used_only_while_its_site_is_approved(self) -> None:
        entry = await self.link(self.one)
        every = [e.name for e in await self.brew.list(GUILD_A, self.one.id)]
        self.assertEqual(every, [entry.name])  # the DM's list always shows it
        self.assertEqual(await self.brew.list(GUILD_A, self.one.id, usable_only=True), [])
        self.assertIsNone(await self.brew.find(GUILD_A, self.one.id, entry.name, usable_only=True))
        await self.sources.approve(GUILD_A, self.one.id, DM, "dndbeyond.com")
        self.assertEqual(len(await self.brew.list(GUILD_A, self.one.id, usable_only=True)), 1)
        self.assertIsNotNone(
            await self.brew.find(GUILD_A, self.one.id, entry.name, usable_only=True)
        )
        await self.brew.set_enabled(GUILD_A, self.one.id, DM, entry.id, False)  # off wins
        self.assertEqual(await self.brew.list(GUILD_A, self.one.id, usable_only=True), [])
        await self.brew.set_enabled(GUILD_A, self.one.id, DM, entry.id, True)
        self.assertTrue(await self.sources.revoke(GUILD_A, self.one.id, DM, "dndbeyond.com"))
        self.assertFalse(await self.sources.revoke(GUILD_A, self.one.id, DM, "dndbeyond.com"))
        self.assertEqual(await self.brew.list(GUILD_A, self.one.id, usable_only=True), [])
        # Another campaign that approved it still uses its own copy of the entry.
        other = await self.link(self.two)
        await self.sources.approve(GUILD_A, self.two.id, DM, "dndbeyond.com")
        usable = await self.brew.list(GUILD_A, self.two.id, usable_only=True)
        self.assertEqual([e.id for e in usable], [other.id])

    async def test_an_upload_credited_to_a_website_needs_that_sites_approval_too(self) -> None:
        await self.raw(
            name="Lamp", name_key="lamp", source_kind="upload", credit_kind="website",
            credit_domain="example.org",
        )  # fmt: skip
        await self.raw(
            name="Rope", name_key="rope", source_kind="upload", credit_kind="name",
            credit_text="Sam",
        )  # fmt: skip
        usable = await self.brew.list(GUILD_A, self.one.id, usable_only=True)
        self.assertEqual(sorted(e.name for e in usable), ["Rope"])  # a typed name needs nothing
        await self.sources.approve(GUILD_A, self.one.id, DM, "example.org")
        usable = await self.brew.list(GUILD_A, self.one.id, usable_only=True)
        self.assertEqual(sorted(e.name for e in usable), ["Lamp", "Rope"])

    async def test_a_campaign_cannot_approve_without_limit(self) -> None:
        for n in range(50):
            await self.sources.approve(GUILD_A, self.one.id, DM, f"site{n}.example.com")
        with self.assertRaisesRegex(HomebrewError, "as many sites"):
            await self.sources.approve(GUILD_A, self.one.id, DM, "one-more.example.com")
        await self.sources.approve(
            GUILD_A, self.one.id, DM, "site3.example.com"
        )  # a repeat is fine

    async def test_approvals_go_with_the_campaign(self) -> None:
        await self.sources.approve(GUILD_A, self.two.id, DM, "dndbeyond.com")
        await self.campaigns.delete(GUILD_A, self.two.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM site_approvals")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)


class Backups(SourcesTest):
    async def test_a_link_entry_and_its_approval_round_trip_and_share_the_existing_page(
        self,
    ) -> None:
        link = await self.link(self.one)
        await self.brew.add(GUILD_A, self.one.id, DM, kind="item", name="Lamp", text="x")
        await self.brew.load_file(GUILD_A, self.one.id, DM, [FileEntry("item", "Rope", "Long.")])
        await self.sources.approve(GUILD_A, self.one.id, DM, "dndbeyond.com")
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        rows = {r["name"]: r for r in backup["sections"]["homebrew"]}
        self.assertEqual(rows["Lamp"]["source"], "typed")
        self.assertEqual(rows["Rope"]["credit"], {"kind": "user", "text": None, "domain": None})
        self.assertEqual(rows[link.name]["page"]["url"], link.page_url)
        self.assertEqual(rows[link.name]["text"], "Aim better.")  # the backup is complete
        self.assertEqual(
            backup["sections"]["site_approvals"],
            [
                {
                    "domain": "dndbeyond.com",
                    "by": str(DM),
                    "at": backup["sections"]["site_approvals"][0]["at"],
                }
            ],
        )
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        back = {e.name: e for e in await self.brew.list(GUILD_B, restored.id)}
        self.assertEqual(back[link.name].page_id, link.page_id)  # re-pointed to the page we have
        self.assertEqual(back[link.name].text, "Aim better.")
        self.assertEqual((back["Rope"].source_kind, back["Rope"].credit_kind), ("upload", "user"))
        self.assertTrue(await self.sources.approved(GUILD_B, restored.id, "dndbeyond.com"))
        self.assertEqual(len(await self.brew.list(GUILD_B, restored.id, usable_only=True)), 3)
        again = await self.campaigns.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"]["homebrew"], backup["sections"]["homebrew"])
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM homebrew_pages")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)  # no second copy

    async def test_a_missing_page_is_recreated_from_the_backup(self) -> None:
        await self.link(self.one)
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        await self.campaigns.delete(GUILD_A, self.one.id)
        async with self.db.unscoped() as conn:
            await conn.execute("DELETE FROM homebrew_pages")
        restored = await self.campaigns.import_backup(GUILD_A, backup, DM)
        (entry,) = await self.brew.list(GUILD_A, restored.id)
        self.assertEqual((entry.text, entry.summary), ("Aim better.", "A bow charm."))

    async def test_a_renamed_link_entry_and_a_refreshed_page_still_restore(self) -> None:
        entry = await self.link(self.one)
        async with self.db.guild(GUILD_A) as conn:  # the DM's own name and kind for it
            await conn.execute(
                "UPDATE homebrew_entries SET name = 'My Amulet', name_key = 'my amulet',"
                " kind = 'other' WHERE id = %s",
                (entry.id,),
            )
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        (back,) = await self.brew.list(GUILD_B, restored.id)
        self.assertEqual((back.name, back.kind, back.text), ("My Amulet", "other", "Aim better."))

    async def test_words_planted_in_a_backup_never_become_a_page_others_rely_on(self) -> None:
        link = await self.link(self.one)
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        forged_url = "https://www.dndbeyond.com/magic-items/999-forged"
        row = backup["sections"]["homebrew"][0]
        page = {**row["page"], "url": forged_url, "text": "PLANTED", "fetched_at": 4_000_000_000}
        row = {**row, "text": "PLANTED", "page": page}
        planted = {**backup, "sections": {**backup["sections"], "homebrew": [row]}}
        restored = await self.campaigns.import_backup(GUILD_B, planted, DM)
        (back,) = await self.brew.list(GUILD_B, restored.id)
        self.assertEqual(back.text, "PLANTED")  # (that campaign's own entry shows it)
        # Another campaign importing that address does not get the planted words as a page.
        self.assertIsNone(await self.sources.page_for(forged_url))
        kept = await self.sources.page_for(forged_url, include_restored=True)
        assert kept is not None
        self.assertEqual((kept.fetch_status, kept.fetched_at), ("restored", 0))
        fetched = await self.sources.save_page(
            forged_url, kind="item", name="Real", text="The real words."
        )
        self.assertEqual((fetched.fetch_status, fetched.text), ("ok", "The real words."))
        self.assertEqual(fetched.id, kept.id)
        self.assertEqual(link.page_id, (await self.page()).id)  # the real page is untouched

    async def test_an_older_backup_loads_as_typed_by_the_maker(self) -> None:
        await self.brew.add(GUILD_A, self.one.id, DM, kind="item", name="Lamp", text="x")
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        row = {k: v for k, v in backup["sections"]["homebrew"][0].items()
               if k not in ("source", "added_by")}  # fmt: skip
        old = {**backup, "sections": {"homebrew": [row]}}
        restored = await self.campaigns.import_backup(GUILD_B, old, PLAYER)
        (lamp,) = await self.brew.list(GUILD_B, restored.id)
        self.assertEqual((lamp.source_kind, lamp.added_by), ("typed", DM))

    async def test_a_damaged_source_is_refused_before_anything_is_written(self) -> None:
        link = await self.link(self.one)
        await self.brew.add(GUILD_A, self.one.id, DM, kind="item", name="Lamp", text="x")
        good = await self.campaigns.export(GUILD_A, self.one.id)
        rows = {r["name"]: r for r in good["sections"]["homebrew"]}
        page = rows[link.name]["page"]
        bad: list[dict[str, Any]] = [
            {**rows["Lamp"], "source": "other"},
            {**rows["Lamp"], "page": page},  # a typed entry with a page
            {**rows["Lamp"], "credit": {"kind": "user", "text": None, "domain": None}},
            {**rows["Lamp"], "added_by": "0"},
            {**rows[link.name], "page": {**page, "url": "http://example.com/x"}},
            {**rows[link.name], "page": {**page, "domain": "evil.com"}},
            {**rows[link.name], "page": {**page, "text": "Other words."}},
            {**rows[link.name], "page": {**page, "status": "gone"}},
            {**rows[link.name], "page": {k: v for k, v in page.items() if k != "kind"}},
            {k: v for k, v in rows[link.name].items() if k != "page"},
            {**rows[link.name], "from_book": True},
            {**rows["Lamp"], "source": "upload",
             "credit": {"kind": "website", "text": None, "domain": "WWW.Example.com"}},
            {**rows["Lamp"], "source": "upload",
             "credit": {"kind": "name", "text": "  Sam  ", "domain": None}},
            {**rows["Lamp"], "source": "upload",
             "credit": {"kind": "user", "text": "x", "domain": None}},
        ]  # fmt: skip
        for row in bad:
            damaged = {**good, "sections": {**good["sections"], "homebrew": [row]}}
            with self.subTest(row=row), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, damaged, PLAYER)
        approvals = [
            [{"domain": "WWW.dndbeyond.com", "by": "7", "at": 1}],
            [{"domain": "dndbeyond.com", "by": "0", "at": 1}],
            [{"domain": "dndbeyond.com", "by": "7", "at": -1}],
            [{"domain": "dndbeyond.com", "by": "7", "at": 1, "extra": 1}],
            [{"domain": "a.com", "by": "7", "at": 1}, {"domain": "a.com", "by": "7", "at": 2}],
            [{"domain": f"s{n}.example.com", "by": "7", "at": 1} for n in range(51)],
        ]
        for rows_ in approvals:
            damaged = {**good, "sections": {**good["sections"], "site_approvals": rows_}}
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, damaged, PLAYER)
        self.assertEqual([c.name for c in await self.campaigns.list_campaigns(GUILD_B)], ["Far"])


class Migration(DatabaseTest):
    async def test_existing_entries_become_typed_by_their_maker_and_the_table_is_closed_again(
        self,
    ) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0113"]
        await self.db.close()  # start again from a schema as it was before 0113
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        one = await CampaignStore(self.db).create(GUILD_A, "One", DM)
        async with self.db.guild(GUILD_A) as conn:
            for name, maker in (("Hollow King", 7), ("Moonblade", 8)):
                await conn.execute(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, created_by, created_at, updated_at)"
                    " VALUES (%s, %s, 'monster', %s, %s, 'words', %s, 1, 1)",
                    (GUILD_A, one.id, name, name.casefold(), maker),
                )
        await self.db.migrate()
        got = {
            e.name: (e.source_kind, e.added_by)
            for e in await HomebrewStore(self.db).list(GUILD_A, one.id)
        }
        self.assertEqual(got, {"Hollow King": ("typed", 7), "Moonblade": ("typed", 8)})
        async with self.db.unscoped() as conn:  # the opening is gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
