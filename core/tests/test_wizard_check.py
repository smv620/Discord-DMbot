"""The start-up wizard's check-only read and the pasted campaign page's reader (#3955). Neither
calls the AI or reads names; neither keeps anything."""

from __future__ import annotations

import base64
import subprocess
import sys
import unittest

from dmbot.memory.name_documents import MAX_DOCUMENT_BYTES
from dmbot.web import wizard_check, wizard_page


class TheCheck(unittest.IsolatedAsyncioTestCase):
    async def test_a_text_file_is_counted(self) -> None:
        text = ("word " * 1200).strip()
        got = await wizard_check.check_file("lore.txt", text.encode())
        self.assertEqual((got.status, got.reason, got.words), ("ok", "", 1200))
        self.assertEqual(got.pages, 3)  # (about 500 words a page)
        self.assertEqual(got.text, text)

    async def test_a_link_is_kept_as_the_address_and_nothing_is_fetched(self) -> None:
        got = wizard_check.check_link("https://example.com/notes?page=2")
        assert got is not None
        self.assertEqual(
            (got.text, got.pages, got.words, got.status),
            ("https://example.com/notes?page=2", 0, 0, "ok"),
        )

    async def test_only_a_plain_https_address_of_sensible_length_is_a_link(self) -> None:
        for bad in (
            "http://example.com/x",
            "example.com",
            "https://",
            "https://a b",
            " https://x.test/y",
            "https://x.test/y\n",
            "ftp://x.test/y",
            "x" * 5,
        ):
            self.assertIsNone(wizard_check.check_link(bad), bad)
        self.assertIsNone(wizard_check.check_link("https://example.com/" + "a" * 2000))
        self.assertIsNotNone(wizard_check.check_link("https://example.com/" + "a" * (2000 - 20)))

    async def test_a_pdf_with_picture_pages_says_how_many(self) -> None:
        from tests.test_adventure import pdf

        words = "The party reaches the old keep at dusk and finds the gate open."
        got = await wizard_check.check_file("handout.pdf", pdf([[words], [], [words]]))
        self.assertEqual((got.status, got.pages, got.picture_pages), ("ok", 3, 1))
        plain = await wizard_check.check_file("lore.txt", b"The gate is open.")
        self.assertEqual(plain.picture_pages, 0)

    async def test_a_csv_is_read_by_rows(self) -> None:
        got = await wizard_check.check_file("rules.csv", b"rule,text\nfall,take 1d6\n")
        self.assertEqual((got.status, got.words > 0), ("ok", True))

    async def test_an_empty_file_says_empty(self) -> None:
        got = await wizard_check.check_file("lore.txt", b"   \n")
        self.assertEqual((got.status, got.reason, got.text), ("unreadable", "empty", ""))

    async def test_a_type_it_cannot_read_says_why_and_never_raises(self) -> None:
        got = await wizard_check.check_file("song.mp3", b"\x00\x01\x02")
        self.assertEqual((got.status, got.reason), ("unreadable", "not_text"))

    async def test_a_file_over_the_byte_cap_is_refused_before_it_is_parsed(self) -> None:
        big = b"x" * (MAX_DOCUMENT_BYTES + 1)
        got = await wizard_check.check_file("lore.txt", big)
        self.assertEqual((got.status, got.reason), ("unreadable", "too_big"))

    async def test_a_damaged_pdf_is_a_word_not_a_crash(self) -> None:
        got = await wizard_check.check_file("lore.pdf", b"%PDF-1.4 not really")
        self.assertEqual(got.status, "unreadable")

    def test_pasted_text_is_counted_the_same_way(self) -> None:
        self.assertEqual(wizard_check.check_text("one two three").words, 3)
        self.assertEqual(wizard_check.check_text("  ").reason, "empty")
        self.assertNotIn("\x00", wizard_check.check_text("a\x00b").text)

    def test_the_base64_body_is_checked_before_it_is_decoded(self) -> None:
        self.assertEqual(wizard_check.decode(base64.b64encode(b"hello").decode()), b"hello")
        self.assertIsNone(wizard_check.decode("not base64!!"))
        self.assertIsNone(wizard_check.decode(None))
        self.assertIsNone(wizard_check.decode("A" * (wizard_check.MAX_BASE64_CHARS + 1)))

    def test_the_reader_loads_no_ai_and_no_story_memory(self) -> None:
        code = (
            "import sys\n"
            "import dmbot.web.wizard_check, dmbot.web.wizard_page\n"
            "bad = [m for m in sys.modules if m in ('dmbot.ai', 'dmbot.extractor')"
            " or m.startswith('dmbot.memory.store') or m.startswith('anthropic')]\n"
            "print(bad)\n"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
        ).stdout
        self.assertEqual(out.strip(), "[]")


class ThePage(unittest.TestCase):
    TEXT = (
        "Rime of the Frostmaiden\n"
        "Dungeon Master: Belleros\n"
        "Characters\n"
        "Cerric Vale - Bob - Human Fighter 3\n"
        "Mira Dawn | Alice | Elf Wizard 3\n"
        "Tavin, Dwarf Cleric 3\n"
        "Notes\n"
        "Cerric Vale again\n"
    )

    def test_the_name_and_the_characters_are_found(self) -> None:
        got = wizard_page.read(self.TEXT)
        self.assertEqual(got.name, "Rime of the Frostmaiden")
        self.assertEqual(got.characters, ("Cerric Vale", "Mira Dawn", "Tavin"))

    def test_text_in_another_shape_finds_no_characters_and_does_not_guess(self) -> None:
        got = wizard_page.read("Some page\nNothing here that looks like a list\n")
        self.assertEqual(got.characters, ())

    def test_junk_and_empty_text_are_safe(self) -> None:
        self.assertEqual(wizard_page.read(""), wizard_page.PageRead("", ()))
        self.assertEqual(wizard_page.read("\x00" * 5).characters, ())
        many = "x\nCharacters\n" + "\n".join(f"Hero{chr(97 + i)}" for i in range(26))
        self.assertEqual(len(wizard_page.read(many).characters), wizard_page.MAX_CHARACTERS)


class ThePageShapes(unittest.TestCase):
    """The other shapes a campaign page comes in (#4214); every page here is invented."""

    def test_the_owners_sentence_gives_the_four_names_in_order(self) -> None:
        text = (
            "The Frost Road\nThe player characters in this adventure are Cerric, Belleros, "
            "Claude, and Okthandom.\nNotes\nNothing.\n"
        )
        self.assertEqual(
            wizard_page.read(text).characters, ("Cerric", "Belleros", "Claude", "Okthandom")
        )

    def test_a_short_sentence_with_and(self) -> None:
        got = wizard_page.read("Winter\nThe characters are Tav and Shadowheart.\n")
        self.assertEqual(got.characters, ("Tav", "Shadowheart"))

    def test_and_an_oxford_comma_ampersand_and_or(self) -> None:
        text = "x\nPlayer characters: Mira Dawn, Old Tam, & Sera Vale or Pip.\n"
        self.assertEqual(
            wizard_page.read(text).characters, ("Mira Dawn", "Old Tam", "Sera Vale", "Pip")
        )

    def test_a_long_clause_is_not_a_name(self) -> None:
        text = "x\nThe characters are Cerric and heroes who have come a long way to help.\n"
        self.assertEqual(wizard_page.read(text).characters, ())
        long_name = "x\nThe characters are Cerric the Brave of the Northern Reach.\n"
        self.assertEqual(wizard_page.read(long_name).characters, ())

    def test_unrelated_prose_gives_nothing(self) -> None:
        self.assertEqual(
            wizard_page.read("Notes\ncharacters are what make a story\n").characters, ()
        )
        self.assertEqual(
            wizard_page.read("Notes\nThe characters are what make a story.\n").characters, ()
        )

    def test_the_blocks_of_a_selected_page_skip_the_level_and_button_lines(self) -> None:
        text = (
            "Winter's Reach\nActive Characters\n"
            "Okthandom\nLevel 3 Human Fighter\nView\nManage\n"
            "Brannoc Stonebelt\nLevel 4 Dwarf Cleric\nView\nLeave\n"
            "Remove\nNotes\nBring snacks\n"
        )
        got = wizard_page.read(text)
        self.assertEqual(got.name, "Winter's Reach")
        self.assertEqual(got.characters, ("Okthandom", "Brannoc Stonebelt"))

    def test_the_old_heading_list_still_works(self) -> None:
        self.assertEqual(
            wizard_page.read("Camp\nCharacters\nIvy Quickfoot - Zed - Halfling Rogue\n").characters,
            ("Ivy Quickfoot",),
        )

    def test_when_several_shapes_match_the_names_come_in_order_without_repeats(self) -> None:
        text = (
            "Camp\nThe characters are Cerric and Mira.\n"
            "Characters\nMira Dawn\nCerric\nLevel 2 Elf Wizard\nSera\n"
        )
        # "Cerric" is in both; names keep the order they first appear in
        self.assertEqual(wizard_page.read(text).characters, ("Cerric", "Mira", "Mira Dawn", "Sera"))

    def test_the_most_it_will_take_is_twelve(self) -> None:
        names = ", ".join(f"Hero{chr(65 + i)}" for i in range(20))
        self.assertEqual(
            len(wizard_page.read(f"x\nThe characters are {names}.\n").characters),
            wizard_page.MAX_CHARACTERS,
        )
