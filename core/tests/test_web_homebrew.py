"""A campaign's homebrew on the website (#1525), run as the website's own database role: anyone in
the server reads every entry with its on or off state (nothing is hidden from players any more,
"Homebrew (part 2)"), with no plan rule; a person outside the server gets "not found"; and the
download is the bot's own file for those entries, byte for byte."""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg

from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import Homebrew, HomebrewStore
from dmbot.rules.homebrew_sources import SourceStore
from dmbot.web import homebrew as pages
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, QUILLON, THURSDAY, FakeDiscord, settings

CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all
SECRET = "A lich waits under the chapel"


class HomebrewBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"
        self.store_b = HomebrewStore(self.db)
        g, c = THURSDAY.id, self.one.id
        await self.store_b.add(
            g,
            c,
            ALICE.id,
            kind="spell",
            name="Zap",
            text="A bolt of blue light.",
        )
        await self.store_b.add(
            g, c, ALICE.id, kind="item", name="Moon Lamp", text="Glows at night.",
            summary="A lamp that glows",
        )  # fmt: skip
        await self.store_b.add(g, c, ALICE.id, kind="spell", name="Arc", text="A small spark.")
        lich = await self.store_b.add(  # turned off: shown on the page with its state
            g, c, ALICE.id, kind="monster", name="The Lich", text=SECRET, summary="Foe"
        )
        await self.store_b.set_enabled(g, c, ALICE.id, lich.id, False)
        await self.store_b.add(
            THURSDAY.id, self.two.id, BOB.id, kind="feat", name="Other", text="Another table's",
        )  # fmt: skip
        self.carol = await self.session_of(CAROL)
        self.bob = await self.session_of(BOB)

    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def shown_entries(self) -> list[Any]:
        return await self.store_b.list(THURSDAY.id, self.one.id)


class ThePage(HomebrewBase):
    async def test_any_member_reads_every_entry_by_kind_then_name(self) -> None:
        for who in (
            self.session,
            self.bob,
            self.carol,
        ):  # Alice DMs it, Bob DMs another, Carol none
            got = await pages.read_homebrew(self.web, who, self.cref)
            self.assertEqual(got["name"], "One")
            self.assertEqual(
                [(e["kind"], e["name"]) for e in got["entries"]],
                [
                    ("spell", "Arc"),
                    ("spell", "Zap"),
                    ("monster", "The Lich"),
                    ("item", "Moon Lamp"),
                ],
            )
        by = {e["name"]: e for e in got["entries"]}
        self.assertEqual(by["Moon Lamp"]["summary"], "A lamp that glows")
        self.assertIsNone(by["Zap"]["summary"])
        self.assertEqual(by["Zap"]["text"], "A bolt of blue light.")

    async def test_an_entry_that_is_off_is_shown_with_its_state(self) -> None:
        for who in (self.session, self.carol):
            got = await pages.read_homebrew(self.web, who, self.cref)
            states = {e["name"]: e["enabled"] for e in got["entries"]}
            self.assertEqual(
                states, {"Arc": True, "Zap": True, "The Lich": False, "Moon Lamp": True}
            )

    async def test_an_imported_entry_shows_the_words_of_its_page(self) -> None:
        from dmbot.rules.homebrew_sources import SourceStore

        page = await SourceStore(self.db).save_page(
            "https://www.dndbeyond.com/magic-items/1-lamp", kind="item", name="Page Lamp",
            text="Words from the page.", summary="A page summary.",
        )  # fmt: skip
        await self.store_b.add_link(
            THURSDAY.id, self.one.id, ALICE.id, page_id=page.id, kind="item", name=page.name,
            domain=page.domain,
        )  # fmt: skip
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        by = {e["name"]: e for e in got["entries"]}
        self.assertEqual(
            (by["Page Lamp"]["text"], by["Page Lamp"]["summary"]),
            ("Words from the page.", "A page summary."),
        )
        _name, text = await pages.render(self.web, self.carol, self.cref)
        self.assertIn("Words from the page.", text)

    async def test_it_is_this_campaigns_entries_only(self) -> None:
        got = await pages.read_homebrew(self.web, self.carol, self.oref)
        self.assertEqual([e["name"] for e in got["entries"]], ["Other"])

    async def test_someone_outside_the_server_or_a_missing_campaign_gets_not_found(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{THURSDAY.id}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await pages.read_homebrew(self.web, who, ref)
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await pages.render(self.web, elsewhere, self.cref)

    async def test_only_the_wanted_fields_leave(self) -> None:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        self.assertEqual(set(got), {"name", "fileName", "entries", "packs"})
        self.assertEqual(
            set(got["entries"][0]),
            {"kind", "name", "summary", "text", "fromBook", "legacy", "enabled", "source"},
        )
        self.assertEqual(got["fileName"], homebrew_file.filename("One"))
        self.assertNotIn(str(ALICE.id), json.dumps(got))

    async def test_the_campaign_list_has_campaigns_with_an_entry(self) -> None:
        got = await pages.list_campaigns(self.web, self.carol)
        self.assertEqual(
            [(c["ref"], c["name"], c["entries"]) for c in got],
            [(self.cref, "One", 4), (self.oref, "Two", 1)],
        )
        self.assertEqual({c["serverName"] for c in got}, {THURSDAY.name})
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)
        self.assertEqual(len(await pages.list_campaigns(self.web, self.carol)), 2)  # (no entry)
        await self.store_b.add(THURSDAY.id, three.id, ALICE.id, kind="feat", name="Last", text="x")
        self.assertEqual(len(await pages.list_campaigns(self.web, self.carol)), 3)
        elsewhere = await self.session_of(CAROL, [QUILLON])
        self.assertEqual(await pages.list_campaigns(self.web, elsewhere), [])

    async def test_the_list_filters_before_it_limits(self) -> None:
        for n in range(101):
            await self.campaigns.create(THURSDAY.id, f"Aaa {n:03d}", ALICE.id)
        zed = await self.campaigns.create(THURSDAY.id, "Zed", ALICE.id)
        await self.store_b.add(THURSDAY.id, zed.id, ALICE.id, kind="feat", name="Last", text="x")
        names = [c["name"] for c in await pages.list_campaigns(self.web, self.carol)]
        self.assertEqual(names, ["One", "Two", "Zed"])


class BookTags(HomebrewBase):
    """A shared book's entries are tagged like the Discord players' card (#1579)."""

    async def mark(self, name: str, edition: str | None, *, target: str | None = None) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE homebrew_entries SET from_book = TRUE, book_edition = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND name = %s",
                (edition, THURSDAY.id, self.one.id, name),
            )
            if target is not None:
                await conn.execute(
                    "UPDATE campaigns SET target_ruleset = %s WHERE guild_id = %s AND id = %s",
                    (target, THURSDAY.id, self.one.id),
                )

    async def tags(self) -> dict[str, tuple[bool, bool]]:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        return {e["name"]: (e["fromBook"], e["legacy"]) for e in got["entries"]}

    async def test_a_2014_book_in_a_newer_campaign_is_legacy_a_2024_book_is_not(self) -> None:
        await self.mark("Zap", "2014", target="2024")
        await self.mark("Arc", "2024")
        self.assertEqual(
            await self.tags(),
            {
                "Arc": (True, False),
                "Zap": (True, True),
                "The Lich": (False, False),
                "Moon Lamp": (False, False),
            },
        )

    async def test_the_same_test_as_the_bots(self) -> None:
        from dmbot.rules.book_rank import book_is_legacy

        for edition, target in (
            ("2014", "2024"),
            ("2014", "2014"),
            ("2024", "2024"),
            (None, "2024"),
        ):
            with self.subTest(edition=edition, target=target):
                await self.mark("Zap", edition, target=target)
                entries = await self.store_b.list(THURSDAY.id, self.one.id)
                zap = next(e for e in entries if e.name == "Zap")
                self.assertEqual((await self.tags())["Zap"][1], book_is_legacy(zap, target))

    async def test_a_book_entry_that_is_off_is_tagged_and_no_book_name_or_author_leaves(
        self,
    ) -> None:
        await self.mark("The Lich", "2014", target="2024")
        self.assertEqual((await self.tags())["The Lich"], (True, True))
        shown = json.dumps(await pages.read_homebrew(self.web, self.carol, self.cref))
        self.assertNotIn(str(ALICE.id), shown)

    async def test_a_non_member_gets_nothing_and_the_file_carries_the_tags(self) -> None:
        await self.mark("Zap", "2014", target="2024")
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await pages.read_homebrew(self.web, elsewhere, self.cref)
        _name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.store_b.list(THURSDAY.id, self.one.id)
        self.assertEqual(text.encode(), homebrew_file.write("One", shown, "2024").encode())

    async def test_the_download_is_byte_equal_to_the_bots_file_with_the_tags(self) -> None:
        """A 2014 book in a 2024 campaign, a 2024 book and an own entry (#1593)."""
        await self.mark("Zap", "2014", target="2024")
        await self.mark("Arc", "2024")
        _name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.shown_entries()
        self.assertEqual(text.encode(), homebrew_file.write("One", shown, "2024").encode())
        lines = text.split("\n")
        self.assertIn("[Shared book] [Legacy 2014]", lines)
        self.assertEqual(lines.count("[Shared book]"), 1)  # the 2024 book
        self.assertEqual(lines.count("[Shared book] [Legacy 2014]"), 1)  # only the 2014 one
        # The tag sits right under the heading of the entry it belongs to.
        zap = next(i for i, line in enumerate(lines) if line.startswith("## Zap"))
        self.assertEqual(lines[zap + 1], "[Shared book] [Legacy 2014]")
        moon = next(i for i, line in enumerate(lines) if line.startswith("## Moon Lamp"))
        self.assertFalse(lines[moon + 1].startswith("[Shared book]"))

    async def test_a_2014_book_in_a_2014_campaign_is_a_plain_shared_book_in_both_files(
        self,
    ) -> None:
        await self.mark("Zap", "2014", target="2014")
        _name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.shown_entries()
        self.assertEqual(text.encode(), homebrew_file.write("One", shown, "2014").encode())
        self.assertNotIn("[Legacy 2014]", text.replace(homebrew_file.HELP, ""))
        self.assertIn("[Shared book]", text.replace(homebrew_file.HELP, ""))

    async def test_an_older_door_without_the_flags_gives_an_untagged_file(self) -> None:
        from unittest.mock import patch

        async def older(*args: Any, **kw: Any) -> tuple[str, dict[str, Any]]:
            campaign_id, found = await original(*args, **kw)
            for e in found["entries"]:
                e.pop("fromBook", None)
                e.pop("legacy", None)
            return campaign_id, found

        original = pages._read
        await self.mark("Zap", "2014", target="2024")
        with patch.object(pages, "_read", older):
            _name, text = await pages.render(self.web, self.carol, self.cref)
        self.assertNotIn("[Shared book]", text.replace(homebrew_file.HELP, ""))


PUBLISHER = "Publisher Campaign Name"


class FollowedPacks(HomebrewBase):
    """The page shows the packs the table follows and every entry of each (#1586)."""

    async def follow(self, campaign_id: str, name: str = "Frost Pack", version: int = 2) -> None:
        content = {
            "entries": [
                {"kind": "spell", "name": "Ice Lance", "summary": "A cold bolt",
                 "text": "Cold damage.", "known": True},
                {"kind": "monster", "name": "Rime Wight", "summary": None,
                 "text": "Hidden in the pack.", "known": False},
                {"kind": "item", "name": "Aurora Cloak", "summary": None,
                 "text": "Shimmers.", "known": True},
            ]
        }  # fmt: skip
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'homebrew', %s, %s, %s, %s::jsonb, TRUE, %s, 5)",
                (THURSDAY.id, campaign_id, "p" * 32, name, PUBLISHER, version,
                 json.dumps(content), ALICE.id),
            )  # fmt: skip

    async def test_every_entry_shows_with_the_packs_name_and_version(self) -> None:
        await self.follow(self.one.id)
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        (pack,) = got["packs"]
        self.assertEqual((pack["name"], pack["version"]), ("Frost Pack", 2))
        self.assertEqual(
            [(e["kind"], e["name"]) for e in pack["entries"]],
            [
                ("spell", "Ice Lance"),
                ("monster", "Rime Wight"),
                ("item", "Aurora Cloak"),
            ],  # by kind
        )
        shown = json.dumps(got)
        for private in (PUBLISHER, "p" * 32, str(ALICE.id)):
            self.assertNotIn(private, shown)
        self.assertEqual(set(pack), {"name", "version", "entries"})

    async def test_no_packs_is_an_empty_list_and_a_homebrew_follow_only(self) -> None:
        self.assertEqual((await pages.read_homebrew(self.web, self.carol, self.cref))["packs"], [])
        async with self.db.guild(THURSDAY.id) as conn:  # a house-rules follow is not a pack
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'house_rules', 'S', 'p', 1, '{\"rules\": []}'::jsonb,"
                " TRUE, %s, 5)",
                (THURSDAY.id, self.one.id, "h" * 32, ALICE.id),
            )
        self.assertEqual((await pages.read_homebrew(self.web, self.carol, self.cref))["packs"], [])

    async def test_a_pack_that_does_not_fit_is_skipped_not_fatal(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'homebrew', 'Odd', 'p', 1,"
                ' \'{"entries": ["junk", {"kind": "feat", "name": "Ok",'
                ' "summary": null, "text": "t", "known": true}]}\'::jsonb, TRUE, %s, 5)',
                (THURSDAY.id, self.one.id, "o" * 32, ALICE.id),
            )
        (pack,) = (await pages.read_homebrew(self.web, self.carol, self.cref))["packs"]
        self.assertEqual([e["name"] for e in pack["entries"]], ["Ok"])

    async def test_a_campaign_with_only_a_followed_pack_is_listed_and_counts_packs(self) -> None:
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)
        await self.follow(three.id)
        got = await pages.list_campaigns(self.web, self.carol)
        self.assertEqual(
            [(c["name"], c["entries"], c["packs"]) for c in got],
            [("One", 4, 0), ("Three", 3, 1), ("Two", 1, 0)],  # 3: the pack's entries
        )
        await self.follow(self.one.id, "Other Pack")  # own 4 + pack's 3
        again = {
            c["name"]: (c["entries"], c["packs"])
            for c in await pages.list_campaigns(self.web, self.carol)
        }
        self.assertEqual(again["One"], (7, 1))

    async def test_a_pack_with_no_entries_does_not_list_a_campaign(self) -> None:
        four = await self.campaigns.create(THURSDAY.id, "Four", ALICE.id)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'homebrew', 'Secret', 'p', 1,"
                " '{\"entries\": []}'::jsonb, TRUE, %s, 5)",
                (THURSDAY.id, four.id, "s" * 32, ALICE.id),
            )
        names = [c["name"] for c in await pages.list_campaigns(self.web, self.carol)]
        self.assertNotIn("Four", names)

    async def test_a_non_member_gets_nothing_and_the_file_is_the_campaigns_own(self) -> None:
        await self.follow(self.one.id)
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await pages.read_homebrew(self.web, elsewhere, self.cref)
        _name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.store_b.list(THURSDAY.id, self.one.id)
        self.assertEqual(text.encode(), homebrew_file.write("One", shown, "2024").encode())
        self.assertNotIn("Ice Lance", text)


class PacksByReference(FollowedPacks):
    """A followed pack's imported entries on the page (#1635): read from the kept page, and only
    once this campaign approved the site (as the bot does)."""

    async def follow_with(self, entries: list[dict[str, Any]]) -> None:
        content = {"entries": entries}
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'homebrew', 'Ref Pack', %s, 1, %s::jsonb, TRUE, %s, 5)",
                (THURSDAY.id, self.one.id, "r" * 32, PUBLISHER, json.dumps(content), ALICE.id),
            )

    async def test_a_pointer_shows_its_pages_words_only_after_the_site_is_approved(self) -> None:
        from dmbot.rules.homebrew_sources import SourceStore

        url = "https://www.dndbeyond.com/magic-items/1-charm"
        await SourceStore(self.db).save_page(
            url, kind="item", name="Page Charm", text="Words from the page.", summary="Short."
        )
        await self.follow_with(
            [
                {"kind": "item", "name": "Labelled Fireball", "summary": None, "text": None,
                 "known": True, "page": url, "domain": "dndbeyond.com"},
                {"kind": "item", "name": "Rope", "summary": None, "text": "A rope.",
                 "known": True, "domain": "other.example"},
                {"kind": "item", "name": "Lamp", "summary": None, "text": "A lamp.",
                 "known": True},
            ]
        )  # fmt: skip
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        (pack,) = got["packs"]
        self.assertEqual([e["name"] for e in pack["entries"]], ["Lamp"])  # nothing approved yet
        self.assertNotIn("Words from the page.", json.dumps(got))
        listed = {c["name"]: c["entries"] for c in await pages.list_campaigns(self.web, self.carol)}
        self.assertEqual(listed["One"], 4 + 1)  # own 4 and the pack's Lamp
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO site_approvals (guild_id, campaign_id, domain, approved_by,"
                " approved_at) VALUES (%s, %s, 'dndbeyond.com', %s, 1)",
                (THURSDAY.id, self.one.id, ALICE.id),
            )
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        (pack,) = got["packs"]
        by = {e["name"]: e for e in pack["entries"]}
        self.assertEqual(set(by), {"Lamp", "Page Charm"})  # the page's own name, not the label
        self.assertEqual(
            (by["Page Charm"]["text"], by["Page Charm"]["summary"]),
            ("Words from the page.", "Short."),
        )
        listed = {c["name"]: c["entries"] for c in await pages.list_campaigns(self.web, self.carol)}
        self.assertEqual(listed["One"], 4 + 2)
        # A page recreated from a backup is not trusted, even for an approved site.
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetch_status = 'restored'")
        (pack,) = (await pages.read_homebrew(self.web, self.carol, self.cref))["packs"]
        self.assertEqual([e["name"] for e in pack["entries"]], ["Lamp"])


class TheFile(HomebrewBase):
    async def test_the_download_is_the_bots_file_for_every_entry_byte_for_byte(self) -> None:
        name, text = await pages.render(self.web, self.carol, self.cref)
        shown = await self.shown_entries()
        self.assertEqual(len(shown), 4)
        self.assertEqual(text.encode(), homebrew_file.write("One", shown, "2024").encode())
        self.assertEqual(name, homebrew_file.filename("One"))
        self.assertIn("## The Lich (monster)\nSummary: Foe\nOff: yes\n", text)  # (off is kept)
        # (the help text mentions the tag, but no entry here is from a book, so none carries one)
        self.assertNotIn("[Shared book]", text.replace(homebrew_file.HELP, ""))

    async def test_a_campaign_with_no_entry_has_no_file(self) -> None:
        empty = await self.campaigns.create(THURSDAY.id, "Empty", ALICE.id)
        with self.assertRaises(RecapError) as caught:
            await pages.render(self.web, self.carol, f"{THURSDAY.id}.{empty.id}")
        self.assertEqual(caught.exception.code, "not_found")


class WhereItCameFrom(HomebrewBase):
    """Each entry says where it came from, in the bot's words (#1653), and never who added it."""

    URL = "https://www.dndbeyond.com/magic-items/2302039-amulet-of-trueshot"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        g, c = THURSDAY.id, self.one.id
        page = await SourceStore(self.db).save_page(
            self.URL, name="Amulet", kind="item", text="Aim better.", summary="A charm."
        )
        await self.store_b.add_link(
            g, c, ALICE.id, page_id=page.id, kind=page.kind, name=page.name, domain=page.domain
        )
        for name, credit in (
            ("Site Spell", ("website", None, "dndbeyond.com")),
            ("Named Spell", ("name", "Sam", None)),
            ("Mine Spell", ("user", None, None)),
        ):
            file_text = homebrew_file.write(
                "x",
                [
                    Homebrew(
                        id=0, campaign_id=c, kind="spell", name=name, text="Words.", summary=None,
                        created_by=0, created_at=0, updated_at=0, version=1,
                    )
                ],
                "2024",
            )  # fmt: skip
            await self.store_b.load_file(
                g, c, ALICE.id, homebrew_file.parse(file_text).entries, credit=credit
            )

    async def entries(self) -> dict[str, dict[str, Any]]:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        return {e["name"]: e for e in got["entries"]}

    async def test_a_page_a_site_and_a_name_are_said_in_the_bots_words(self) -> None:
        by = await self.entries()
        self.assertEqual(by["Amulet"]["source"], "from dndbeyond.com")
        self.assertEqual(by["Site Spell"]["source"], "credited to dndbeyond.com")
        self.assertEqual(by["Named Spell"]["source"], "by Sam")

    async def test_the_dms_own_and_a_loaders_entries_say_nothing(self) -> None:
        by = await self.entries()
        for name in ("Zap", "Moon Lamp", "Mine Spell"):
            self.assertIsNone(by[name]["source"], name)

    async def test_who_added_an_entry_never_reaches_the_answer(self) -> None:
        got = await pages.read_homebrew(self.web, self.carol, self.cref)
        text = json.dumps(got)
        for key in ("added_by", "addedBy", "page_url", "pageUrl", "creditKind", "sourceKind"):
            self.assertNotIn(key, text)
        self.assertNotIn(str(ALICE.id), text)  # the DM's Discord id
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew(%s, %s) AS r", (THURSDAY.id, self.one.id)
            )
            row = await cur.fetchone()
        assert row is not None
        door = json.dumps(row["r"])
        for key in ("added_by", "addedBy", "pageUrl", "page_url", "url"):
            self.assertNotIn(key, door)  # the door itself does not hand them over

    async def test_a_credited_name_is_cut_short_on_the_page(self) -> None:
        long = {"creditKind": "name", "creditText": "x" * 500, "sourceKind": "upload"}
        self.assertEqual(len(pages.source(long) or ""), len("by ") + pages.NAME_SHOWN)
        self.assertIsNone(pages.source({"creditKind": "user"}))
        self.assertIsNone(pages.source({}))


class TheDoorsAndRole(HomebrewBase):
    async def test_the_websites_role_cannot_read_the_table(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute("SELECT * FROM homebrew_entries")

    async def test_the_doors_answer_nothing_without_the_server_set(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew(%s, %s) AS r, dmbot_web_homebrew_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_the_doors_check_the_sessions_server_list_themselves(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        async with self.web.user(CAROL.id, session=elsewhere.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew(%s, %s) AS r, dmbot_web_homebrew_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_each_door_has_one_caller(self) -> None:
        for door in ("dmbot_web_homebrew(", "dmbot_web_homebrew_campaigns"):
            self.assertEqual(callers(door), {"schema.py", "web/homebrew.py"}, door)


class TheRoutes(HomebrewBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(enforce_plans=True),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, user: Any, guilds: Any) -> None:
        token = await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")

    async def test_the_list_the_page_and_the_download_agree_with_plans_enforced(self) -> None:
        await self.sign_in(CAROL, [THURSDAY])
        mine = await self.client.get("/me/homebrew", headers=HEADERS)
        self.assertEqual([c["ref"] for c in mine.json()["campaigns"]], [self.cref, self.oref])
        page = await self.client.get(f"/me/campaigns/{self.cref}/homebrew", headers=HEADERS)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(
            [e["name"] for e in page.json()["entries"]], ["Arc", "Zap", "The Lich", "Moon Lamp"]
        )
        file = await self.client.get(
            f"/me/campaigns/{self.cref}/homebrew/download", headers=HEADERS
        )
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(
            file.headers["content-disposition"], 'attachment; filename="homebrew-one.txt"'
        )
        self.assertEqual(
            file.content, homebrew_file.write("One", await self.shown_entries(), "2024").encode()
        )

    async def test_someone_outside_the_server_gets_404_and_the_list_is_empty(self) -> None:
        await self.sign_in(CAROL, [QUILLON])
        for path in (
            f"/me/campaigns/{self.cref}/homebrew",
            f"/me/campaigns/{self.cref}/homebrew/download",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        self.assertEqual((await self.client.get("/me/homebrew")).json(), {"campaigns": []})
