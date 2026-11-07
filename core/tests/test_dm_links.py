"""The shared link buttons for private messages to the DM (#3663)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import discord

from dmbot.ui import dm_links as dl

SITE = "https://getdmbot.example.com"


class TheAddress(unittest.TestCase):
    def test_a_page_names_the_campaign_and_the_item_by_ids_only(self) -> None:
        url = dl.site_link(SITE, "questions", 7, "ab12", item="q9")
        self.assertEqual(url, f"{SITE}/account/questions?item=q9")  # one page for every campaign

    def test_a_recap_page_carries_its_session_number(self) -> None:
        url = dl.site_link(SITE, "recaps", 7, "ab12", tail="1700000000")
        self.assertEqual(url, f"{SITE}/account/recaps/7.ab12.1700000000")

    def test_no_word_a_person_wrote_can_reach_the_address(self) -> None:
        for bad in ("Lord Varkus", "x" * 81, "a/b", "a?b", ""):
            if bad:
                self.assertEqual(dl.site_link(SITE, "names", 7, "ab12", item=bad), "")
        self.assertEqual(dl.site_link(SITE, "names", 7, "ab12", tail="a b"), "")
        self.assertEqual(dl.site_link(SITE, "names", 7, "a b"), "")

    def test_an_unknown_page_or_a_missing_or_plain_http_site_gives_nothing(self) -> None:
        self.assertEqual(dl.site_link(SITE, "secrets", 7, "ab12"), "")
        self.assertEqual(dl.site_link("", "names", 7, "ab12"), "")
        self.assertEqual(dl.site_link("http://x.example.com", "names", 7, "ab12"), "")

    def test_the_story_graph_line_is_two_ids_joined_by_a_dot(self) -> None:
        url = dl.site_link(SITE, "web", 7, "ab12", item="n1.n2")
        self.assertEqual(url, f"{SITE}/account/web/7.ab12?item=n1.n2")

    def test_the_clarify_page_has_its_own_shape(self) -> None:
        url = dl.clarify_link(SITE, 7, "ab12", "n:5")
        self.assertEqual(url, f"{SITE}/account/clarify?campaign=7.ab12&name=n%3A5")
        self.assertEqual(dl.clarify_link(SITE, 7, "ab12", "Lord Varkus"), "")
        self.assertEqual(dl.clarify_link("", 7, "ab12", "n5"), "")

    def test_the_jump_link_is_a_discord_address_or_nothing(self) -> None:
        good = SimpleNamespace(jump_url="https://discord.com/channels/1/2/3")
        self.assertEqual(dl.jump_link(good), "https://discord.com/channels/1/2/3")  # type: ignore[arg-type]
        self.assertEqual(dl.jump_link(None), "")
        self.assertEqual(dl.jump_link(SimpleNamespace(jump_url="https://evil.example.com/")), "")  # type: ignore[arg-type]


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_both_links_use_the_same_words_everywhere(self) -> None:
        links = dl.buttons(discord_url="https://discord.com/channels/1/2/3", site_url="https://s/x")
        self.assertEqual([b.label for b in links], ["Open in Discord", "Open on the website"])
        self.assertTrue(all(b.url and b.custom_id is None for b in links))

    async def test_either_one_alone(self) -> None:
        only_site = dl.buttons(site_url="https://s/x")
        only_discord = dl.buttons(discord_url="https://discord.com/channels/1/2/3")
        self.assertEqual([b.label for b in only_site], ["Open on the website"])
        self.assertEqual([b.label for b in only_discord], ["Open in Discord"])
        self.assertEqual(dl.buttons(), [])

    async def test_they_go_on_the_row_after_the_views_own_and_nothing_adds_an_empty_row(
        self,
    ) -> None:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Answer", custom_id="a", row=2))
        same = dl.add_links(view)
        self.assertIs(same, view)
        self.assertEqual(len(view.children), 1)
        shown = dl.add_links(view, site_url="https://s/x", discord_url="https://discord.com/a")
        assert shown is not None
        self.assertEqual([c.row for c in shown.children[1:]], [3, 3])

    async def test_a_message_with_no_buttons_gets_a_view_of_just_the_links(self) -> None:
        shown = dl.add_links(None, site_url="https://s/x")
        assert shown is not None
        self.assertEqual(len(shown.children), 1)
        self.assertIsNone(dl.add_links(None))

    async def test_the_pages_are_plain_lowercase_words(self) -> None:
        for page in dl.PAGES:
            self.assertRegex(page, r"^[a-z][a-z-]{0,30}$")


class TheCampaignHelper(unittest.IsolatedAsyncioTestCase):
    async def test_the_default_is_the_questions_page_with_only_the_website_button(self) -> None:
        shown = dl.for_campaign(None, SITE, 7, "ab12", dl.DMLinks())
        assert shown is not None
        [button] = shown.children
        self.assertEqual(button.label, "Open on the website")  # type: ignore[attr-defined]
        self.assertEqual(button.url, f"{SITE}/account/questions")  # type: ignore[attr-defined]

    async def test_no_site_and_no_discord_message_means_nothing_is_added(self) -> None:
        view = discord.ui.View()
        self.assertIs(dl.for_campaign(view, "", 7, "ab12", dl.DMLinks()), view)
        self.assertEqual(len(view.children), 0)

    async def test_a_full_view_goes_without_the_links_instead_of_failing(self) -> None:
        view = discord.ui.View()
        for n in range(5):
            view.add_item(discord.ui.Button(label=str(n), custom_id=f"b{n}", row=4))
        shown = dl.for_campaign(view, SITE, 7, "ab12", dl.DMLinks())
        self.assertIs(shown, view)
        self.assertEqual(len(view.children), 5)

    async def test_auto_placed_buttons_get_the_links_after_them_without_a_row(self) -> None:
        view = discord.ui.View()
        for n in range(3):
            view.add_item(discord.ui.Button(label=str(n), custom_id=f"b{n}"))
        shown = dl.add_links(view, site_url="https://s/x")
        assert shown is not None
        self.assertEqual(shown.children[-1].label, "Open on the website")  # type: ignore[attr-defined]
        self.assertIsNone(shown.children[-1].row)

    async def test_adding_twice_does_not_double_the_links(self) -> None:
        view = discord.ui.View()
        dl.add_links(view, site_url="https://s/x", discord_url="https://discord.com/a")
        dl.add_links(view, site_url="https://s/x", discord_url="https://discord.com/a")
        self.assertEqual(len(view.children), 2)

    async def test_both_links_or_neither_when_there_is_one_place_left(self) -> None:
        view = discord.ui.View()
        for n in range(4):
            view.add_item(discord.ui.Button(label=str(n), custom_id=f"b{n}", row=4))
        dl.add_links(view, site_url="https://s/x", discord_url="https://discord.com/a")
        self.assertEqual(len(view.children), 4)  # two links did not fit, so neither was added


class TheHostIsRebuiltNotCopied(unittest.TestCase):
    def test_user_info_spaces_and_a_trailing_newline_never_get_through(self) -> None:
        for bad in ("https://good.example.com@evil.example.com", "https://a b.example.com"):
            self.assertEqual(dl.site_link(bad, "names", 7, "ab12"), "")
        self.assertEqual(dl.site_link(SITE, "names", 7, "ab12\n"), "")
        self.assertEqual(dl.site_link(SITE, "names", 7, "ab12", item="n1\n"), "")

    def test_a_port_is_kept(self) -> None:
        url = dl.site_link("https://dmbot.example.com:8443/", "names", 7, "ab12")
        self.assertEqual(url, "https://dmbot.example.com:8443/account/names/7.ab12")


class EveryPageIsServed(unittest.TestCase):
    """A link that 404s is worse than none (#3673): each page a message may point at has a page
    file, and a page that takes a `<ref>` also has a rewrite in web/public/_redirects."""

    def test_every_page_has_a_page_file_and_a_rewrite_when_it_takes_a_ref(self) -> None:
        from pathlib import Path

        web = Path(__file__).resolve().parents[2] / "web"
        redirects = (web / "public" / "_redirects").read_text(encoding="utf-8")
        for page in sorted(dl.PAGES):
            self.assertTrue(
                (web / "src" / "pages" / "account" / f"{page}.astro").is_file(),
                f"{page}: no page file",
            )
            if page not in dl.NO_REF:
                self.assertIn(f"/account/{page}/*", redirects, f"{page}: no rewrite for its ref")


class PointAtItself(unittest.IsolatedAsyncioTestCase):
    async def test_the_sent_message_is_edited_once_with_discord_first_and_the_button_comes_off(
        self,
    ) -> None:
        from unittest.mock import AsyncMock

        view = dl.add_links(None, site_url="https://s/x")
        assert view is not None
        view.add_item(discord.ui.Button(label="Answer", custom_id="a"))
        shown: list[list[str]] = []

        async def edit(*, view: discord.ui.View) -> None:
            shown.append([getattr(c, "label", "") for c in view.children])

        message = SimpleNamespace(
            jump_url="https://discord.com/channels/@me/2/3", edit=AsyncMock(side_effect=edit)
        )
        await dl.point_at_itself(message, view)  # type: ignore[arg-type]
        self.assertEqual(shown, [["Answer", "Open in Discord", "Open on the website"]])
        # taken off again, so the next DM's copy gets its own address
        self.assertEqual(
            [getattr(c, "label", "") for c in view.children], ["Answer", "Open on the website"]
        )

    async def test_a_failed_edit_is_never_fatal(self) -> None:
        from unittest.mock import AsyncMock

        view = dl.add_links(None, site_url="https://s/x")
        message = SimpleNamespace(
            jump_url="https://discord.com/channels/@me/2/3",
            edit=AsyncMock(side_effect=RuntimeError),
        )
        await dl.point_at_itself(message, view)  # type: ignore[arg-type]
        await dl.point_at_itself(None, view)
        await dl.point_at_itself(SimpleNamespace(jump_url="", edit=AsyncMock()), view)  # type: ignore[arg-type]
