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
