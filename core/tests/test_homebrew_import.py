"""Adding homebrew from a published page's link (#1634): the first link from a site asks and the
second from the same site doesn't (another campaign asks again), a page kept here is never fetched
again, the not-published and no-answer messages, a name that is taken, and the one-at-a-time
rule."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.rules import dndbeyond, page_fetch
from dmbot.rules.homebrew import HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_import import HomebrewImporter, ImportRefused
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from tests.pg import DatabaseTest
from tests.test_dndbeyond import PAGE

GUILD, DM, PLAYER = 111, 7, 9
URL = "https://www.dndbeyond.com/magic-items/2302039-moonlit-charm"


class Fetches:
    """A stand-in for the network: counts the pages it was asked for."""

    def __init__(self, reply: page_fetch.Reply | Exception | None = None) -> None:
        self.reply = reply or page_fetch.Reply(200, PAGE)
        self.asked: list[str] = []

    async def __call__(self, url: str) -> page_fetch.Reply:
        self.asked.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class ImportTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.brew = HomebrewStore(self.db)
        self.sources = SourceStore(self.db)
        self.one = await self.campaigns.create(GUILD, "One", DM)
        self.two = await self.campaigns.create(GUILD, "Two", DM)
        self.net = Fetches()
        self.importer = HomebrewImporter(self.brew, self.sources, fetch=self.net)

    async def approve(self, campaign: object | None = None) -> None:
        c = campaign or self.one
        await self.sources.approve(c.guild_id, c.id, DM, "dndbeyond.com")  # type: ignore[attr-defined]

    async def refused(self, text: str, campaign: object | None = None) -> str:
        c = campaign or self.one
        with self.assertRaises(ImportRefused) as raised:
            await self.importer.read(c.guild_id, c.id, text)  # type: ignore[attr-defined]
        return str(raised.exception)


class Reading(ImportTest):
    async def test_only_an_allowed_site_and_the_site_must_be_approved_first(self) -> None:
        await self.approve()
        for bad in ("http://www.dndbeyond.com/spells/1-x", "https://example.com/spells/1-x", "x"):
            self.assertEqual(await self.refused(bad), dndbeyond.NOT_SUPPORTED)
        self.assertEqual(self.net.asked, [])  # nothing was fetched for any of them
        # Another campaign has not said it: it is told so, and nothing is fetched for it.
        self.assertIn("Add this site first", await self.refused(URL, self.two))
        self.assertEqual(self.net.asked, [])

    async def test_a_page_is_read_once_and_a_kept_page_is_not_fetched_again(self) -> None:
        await self.approve()
        first = await self.importer.read(GUILD, self.one.id, URL + "#top")
        self.assertEqual(
            (first.name, first.kind, first.domain, first.reused),
            ("Moonlit Charm", "item", "dndbeyond.com", False),
        )
        self.assertIn("see in the dark", first.text)
        self.assertEqual(await self.brew.list(GUILD, self.one.id), [])  # not saved by reading
        saved = await self.importer.save(GUILD, self.one.id, DM, first)
        self.assertEqual(
            (saved.source_kind, saved.credit_domain, saved.added_by), ("link", "dndbeyond.com", DM)
        )
        self.assertEqual(saved.text, first.text)
        # The same address for another campaign (it approves the site itself) re-uses the page.
        await self.approve(self.two)
        again = await self.importer.read(GUILD, self.two.id, URL)
        self.assertTrue(again.reused)
        await self.importer.save(GUILD, self.two.id, DM, again)
        self.assertEqual(len(self.net.asked), 1)  # one fetch for two campaigns
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM homebrew_pages")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)

    async def test_every_spelling_of_an_address_is_the_same_page_and_one_fetch(self) -> None:
        await self.approve()
        first = await self.importer.read(GUILD, self.one.id, URL)
        await self.importer.save(GUILD, self.one.id, DM, first)
        for spelling in (
            URL.replace("www.", ""),
            URL.upper().replace("HTTPS", "https"),
            URL + "?utm_source=x#top",
            URL + "/",
        ):
            again = await self.importer.read(GUILD, self.one.id, spelling)
            self.assertTrue(again.reused, spelling)
        self.assertEqual(len(self.net.asked), 1)

    async def test_a_page_is_read_off_the_event_loop_and_a_slow_one_is_cut(self) -> None:
        await self.approve()
        self.net.reply = page_fetch.Reply(
            200, "<h1>Big</h1>" + "".join(f"<p>word {n}</p>" for n in range(50_000))
        )
        preview = await self.importer.read(GUILD, self.one.id, URL)
        self.assertTrue(preview.truncated)

    async def test_a_page_that_is_not_published_says_so(self) -> None:
        await self.approve()
        for reply in (
            page_fetch.Reply(404, "x"),
            page_fetch.Reply(200, "<title>Page Not Found</title>"),
        ):
            self.net.reply = reply
            self.assertEqual(await self.refused(URL), dndbeyond.NOT_PUBLIC)

    async def test_a_block_or_any_other_failure_says_it_did_not_answer(self) -> None:
        await self.approve()
        for reply in (
            page_fetch.Reply(403, "blocked"),
            page_fetch.Reply(500, "oops"),
            page_fetch.PageError("did not answer"),
        ):
            self.net.reply = reply
            self.assertEqual(await self.refused(URL), dndbeyond.NO_ANSWER)

    async def test_a_page_with_no_words_is_said_so(self) -> None:
        await self.approve()
        self.net.reply = page_fetch.Reply(200, "<html><body></body></html>")
        self.assertEqual(await self.refused(URL), dndbeyond.NO_WORDS)

    async def test_only_one_page_at_a_time_per_campaign_and_the_place_is_always_given_back(
        self,
    ) -> None:
        await self.approve()
        gate = page_fetch.FetchGate()
        importer = HomebrewImporter(self.brew, self.sources, fetch=self.net, gate=gate)
        gate.enter(GUILD, self.one.id)  # another page is being read for this campaign
        with self.assertRaises(ImportRefused) as busy:
            await importer.read(GUILD, self.one.id, URL)
        self.assertEqual(str(busy.exception), page_fetch.BUSY)
        gate.leave(GUILD, self.one.id)
        self.net.reply = page_fetch.PageError("did not answer")
        with self.assertRaises(ImportRefused):
            await importer.read(GUILD, self.one.id, URL)
        self.net.reply = page_fetch.Reply(200, PAGE)
        await importer.read(GUILD, self.one.id, URL)  # (the failed read gave its place back)


class Saving(ImportTest):
    async def test_a_name_the_campaign_has_is_refused_like_any_add(self) -> None:
        await self.approve()
        await self.brew.add(GUILD, self.one.id, DM, kind="item", name="Moonlit Charm", text="x")
        preview = await self.importer.read(GUILD, self.one.id, URL)
        with self.assertRaises(ImportRefused) as raised:
            await self.importer.save(GUILD, self.one.id, DM, preview)
        self.assertIn("already has homebrew with that name", str(raised.exception))

    async def test_a_player_cannot_save_and_a_site_taken_back_stops_the_save(self) -> None:
        await self.approve()
        preview = await self.importer.read(GUILD, self.one.id, URL)
        with self.assertRaises(ImportRefused):
            await self.importer.save(GUILD, self.one.id, PLAYER, preview)
        await self.sources.revoke(GUILD, self.one.id, DM, "dndbeyond.com")
        with self.assertRaises(ImportRefused):
            await self.importer.save(GUILD, self.one.id, DM, preview)
        self.assertEqual(await self.brew.list(GUILD, self.one.id), [])

    async def test_the_entry_is_on_and_used_only_while_the_site_is_approved(self) -> None:
        await self.approve()
        preview = await self.importer.read(GUILD, self.one.id, URL)
        saved = await self.importer.save(GUILD, self.one.id, DM, preview)
        self.assertTrue(saved.enabled)
        self.assertEqual(len(await self.brew.list(GUILD, self.one.id, usable_only=True)), 1)
        await self.sources.revoke(GUILD, self.one.id, DM, "dndbeyond.com")
        self.assertEqual(await self.brew.list(GUILD, self.one.id, usable_only=True), [])
