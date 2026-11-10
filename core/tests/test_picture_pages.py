"""PDF pages that hold only pictures are counted, not skipped in silence (#4000): the reader keeps
how many pages a PDF had and how many had no text DMbot could read, across its child process, and
the note for the DM is counts only. A PDF with no readable page at all is still refused as scanned.
"""

from __future__ import annotations

import unittest

from dmbot.memory.document_reader import read_document
from dmbot.memory.name_documents import (
    PICTURE_PAGE_CHARS,
    DocumentError,
    ReadText,
    picture_note,
    text_of,
)
from dmbot.rules.house_reader import house_text_of
from tests.test_adventure import pdf

WORDS = "Part one. The party reaches the old keep at dusk and finds the gate open."


class TheCount(unittest.TestCase):
    def test_two_text_pages_and_one_picture_page_report_one_of_three(self) -> None:
        text = text_of("handout.pdf", pdf([[WORDS], [], [WORDS]]))
        self.assertIsInstance(text, ReadText)
        self.assertEqual((text.pages, text.picture_pages), (3, 1))  # type: ignore[attr-defined]
        self.assertIn("**1 of 3 pages** had only pictures", picture_note(text))

    def test_an_all_text_pdf_reports_nothing(self) -> None:
        text = text_of("story.pdf", pdf([[WORDS], [WORDS]]))
        self.assertEqual(text.picture_pages, 0)  # type: ignore[attr-defined]
        self.assertEqual(picture_note(text), "")

    def test_an_all_picture_pdf_is_still_refused_as_scanned(self) -> None:
        with self.assertRaises(DocumentError) as caught:
            text_of("scan.pdf", pdf([[], []]))
        self.assertIn("pictures of pages", str(caught.exception))

    def test_a_page_with_only_a_few_characters_counts_as_pictures(self) -> None:
        short = "Map".ljust(PICTURE_PAGE_CHARS - 1, "x")  # a caption, below the threshold
        enough = "x" * PICTURE_PAGE_CHARS
        text = text_of("map.pdf", pdf([[WORDS], [short], [enough]]))
        self.assertEqual((text.pages, text.picture_pages), (3, 1))  # type: ignore[attr-defined]

    def test_other_files_have_no_page_counts(self) -> None:
        self.assertEqual(picture_note(text_of("notes.txt", b"The gate is open.")), "")
        self.assertEqual(picture_note("plain text"), "")

    def test_the_house_reader_keeps_the_counts(self) -> None:
        text = house_text_of("rules.pdf", pdf([[WORDS], []]))
        counts = (getattr(text, "pages", -1), getattr(text, "picture_pages", -1))
        self.assertEqual(counts, (2, 1))

    def test_the_note_is_counts_only_and_says_what_to_do(self) -> None:
        note = picture_note(ReadText("x", 140, 12))
        self.assertIn("**12 of 140 pages** had only pictures", note)
        self.assertIn("type it into a text file and add that too", note)
        self.assertEqual(picture_note(ReadText("x", 140, 0)), "")
        self.assertEqual(picture_note(ReadText("x", 0, 0)), "")


class AcrossTheChildProcess(unittest.IsolatedAsyncioTestCase):
    async def test_the_counts_come_back_from_the_reading_process(self) -> None:
        text = await read_document("handout.pdf", pdf([[WORDS], [], [WORDS]]))
        self.assertEqual((text.pages, text.picture_pages), (3, 1))  # type: ignore[attr-defined]
        self.assertIn("**1 of 3 pages**", picture_note(text))

    async def test_a_file_read_in_a_thread_has_no_counts_and_still_reads(self) -> None:
        text = await read_document("notes.txt", b"The gate is open.")
        self.assertEqual(text, "The gate is open.")
        self.assertEqual(picture_note(text), "")


if __name__ == "__main__":
    unittest.main()
