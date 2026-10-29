"""Reading one published D&D Beyond homebrew page (#1634): which links are accepted, what kind a
page is, and the page's name and words, on pages written by hand for these tests (never a real
page's text: CLAUDE.md, the IP rule)."""

from __future__ import annotations

import unittest

from dmbot.rules import dndbeyond
from dmbot.rules.homebrew import TEXT_MAX

PAGE = """<!doctype html><html><head>
<title>Moonlit Charm - Magic Items - D&amp;D Beyond</title>
<meta property="og:title" content="Moonlit Charm">
<script>var secret = "SCRIPT-TEXT";</script><style>.x{color:red}</style></head>
<body>
<header><nav><a href="/">Home</a> <a href="/sign-in">Sign In</a></nav></header>
<main>
  <h1>Moonlit Charm</h1>
  <div class="meta">Wondrous item, rare</div>
  <div class="body"><p>A small silver charm.</p><p>While it glows, you see in the dark.</p>
  <ul><li>Once a day, cast light.</li><li>It dims at dawn.</li></ul></div>
  <button>Share</button>
</main>
<footer>Copyright footer words</footer>
</body></html>"""


class Addresses(unittest.TestCase):
    def test_only_an_https_page_on_an_allowed_site(self) -> None:
        good = "https://www.dndbeyond.com/magic-items/2302039-moonlit-charm"
        self.assertEqual(
            dndbeyond.check_address(good + "#reviews"),
            ("https://www.dndbeyond.com/magic-items/2302039-moonlit-charm", "dndbeyond.com"),
        )
        for bad in (
            "http://www.dndbeyond.com/magic-items/1-x",
            "https://dndbeyond.com.evil.example/magic-items/1-x",
            "https://evil.example/dndbeyond.com/magic-items/1-x",
            "https://user:pw@www.dndbeyond.com/magic-items/1-x",
            "https://www.dndbeyond.com:8443/magic-items/1-x",
            "https://127.0.0.1/magic-items/1-x",
            "https://example.com/spells/1-x",
            "not a link",
            "",
        ):
            self.assertIsNone(dndbeyond.check_address(bad), bad)

    def test_however_a_page_is_written_it_is_one_address(self) -> None:
        one = "https://www.dndbeyond.com/magic-items/1-charm"
        for written in (
            "https://dndbeyond.com/magic-items/1-charm",
            "https://WWW.DNDBeyond.com/Magic-Items/1-Charm/",
            "https://www.dndbeyond.com/magic-items/1-charm?utm=x&y=2#top",
            "  https://www.dndbeyond.com./magic-items/1-charm  ",
        ):
            found = dndbeyond.check_address(written)
            assert found is not None
            self.assertEqual(found, (one, "dndbeyond.com"), written)

    def test_the_kind_comes_from_the_first_part_of_the_path(self) -> None:
        cases = {
            "https://www.dndbeyond.com/magic-items/1-x": "item",
            "https://www.dndbeyond.com/spells/1-x": "spell",
            "https://www.dndbeyond.com/monsters/1-x": "monster",
            "https://www.dndbeyond.com/feats/1-x": "feat",
            "https://www.dndbeyond.com/species/1-x": "species",
            "https://www.dndbeyond.com/races/1-x": "species",
            "https://www.dndbeyond.com/subclasses/1-x": "subclass",
            "https://www.dndbeyond.com/backgrounds/1-x": "background",
            "https://www.dndbeyond.com/equipment/1-x": "other",
            "https://www.dndbeyond.com/": "other",
            "https://www.dndbeyond.com/Spells/1-x?x=1": "spell",
        }
        for url, kind in cases.items():
            self.assertEqual(dndbeyond.kind_for(url), kind, url)


class Reading(unittest.TestCase):
    def test_the_name_is_the_first_heading_and_the_words_leave_out_the_chrome(self) -> None:
        page = dndbeyond.parse_page(PAGE)
        assert page is not None
        self.assertEqual(page.name, "Moonlit Charm")
        self.assertFalse(page.truncated)
        for kept in ("A small silver charm.", "you see in the dark.", "Once a day, cast light."):
            self.assertIn(kept, page.text)
        for left_out in ("SCRIPT-TEXT", "Sign In", "Copyright footer", "color:red", "Share"):
            self.assertNotIn(left_out, page.text)
        self.assertNotIn("Moonlit Charm\n", page.text.split("\n", 1)[0] + "\n")  # (not the name)

    def test_without_a_heading_the_title_is_used_without_the_sites_tail(self) -> None:
        html = (
            "<html><head><title>Ember Blade - Magic Items - D&amp;D Beyond</title></head>"
            "<body><p>It burns.</p></body></html>"
        )
        page = dndbeyond.parse_page(html)
        assert page is not None
        self.assertEqual((page.name, page.text), ("Ember Blade - Magic Items", "It burns."))

    def test_words_over_the_limit_are_cut_and_say_so(self) -> None:
        html = "<h1>Long</h1>" + "".join(
            f"<p>Sentence number {n} goes here.</p>" for n in range(300)
        )
        page = dndbeyond.parse_page(html)
        assert page is not None
        self.assertTrue(page.truncated)
        self.assertLessEqual(len(page.text), TEXT_MAX)
        self.assertTrue(page.text.endswith("…"))

    def test_a_page_with_no_words_or_no_name_reads_as_nothing(self) -> None:
        for html in ("", "<html></html>", "<h1>Only a name</h1>", "<p>Words but no name</p>"):
            self.assertIsNone(dndbeyond.parse_page(html), html)

    def test_broken_or_hostile_html_never_raises(self) -> None:
        for html in ("<<<>>>", "<h1>x", "<p>" * 5000, "\x00\x01<h1>a</h1><p>b</p>", "<!--"):
            dndbeyond.parse_page(html)  # (must not raise)

    def test_a_page_built_to_be_slow_is_read_quickly_or_not_at_all(self) -> None:
        import time

        slow = (
            "<" * 400_000,
            "<i>x</i>" * 60_000,
            "<div>" * 60_000,
            "<p>&amp;" * 60_000,
        )
        for html in slow:
            started = time.monotonic()
            dndbeyond.parse_page("<h1>N</h1><p>w</p>" + html)
            self.assertLess(time.monotonic() - started, 3.0, html[:12])

    def test_repeated_blocks_are_kept_once(self) -> None:
        page = dndbeyond.parse_page("<h1>N</h1><p>Same.</p><p>Same.</p><p>Other.</p>")
        assert page is not None
        self.assertEqual(page.text, "Same.\nOther.")


class NotFound(unittest.TestCase):
    def test_a_404_or_a_not_found_title_is_not_public(self) -> None:
        self.assertTrue(dndbeyond.is_not_found(404, ""))
        self.assertTrue(dndbeyond.is_not_found(410, ""))
        self.assertTrue(
            dndbeyond.is_not_found(200, "<title>Page Not Found - D&amp;D Beyond</title>")
        )
        self.assertFalse(dndbeyond.is_not_found(200, PAGE))
        for title in ("Potion of 404 - D&amp;D Beyond", "Lost and Not Found - D&amp;D Beyond"):
            self.assertFalse(dndbeyond.is_not_found(200, f"<title>{title}</title>"), title)
        self.assertFalse(dndbeyond.is_not_found(500, PAGE))


if __name__ == "__main__":
    unittest.main()
